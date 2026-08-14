"""F10~F12 루틴 API 통합 스모크 — 실제 DB 를 쓴다.

    python scripts/smoke_routine_api.py            # mock 선택(LLM 호출 없음)
    python scripts/smoke_routine_api.py --live-llm # 실제 LLM 선택까지

흐름
    test_ 세션 생성 → 루틴 생성 잡 → 워커 핸들러 직접 실행 → 조회 3종
    → 수행 기록 2건 → 진행 상태 확인 → 정리(세션 삭제, CASCADE)

⚠️ 무료 티어 공유 DB 라 `test_` 접두사 규약을 지키고, 끝나면 지운다.
⚠️ 라우터를 HTTP 로 부르지 않고 서비스·핸들러를 직접 호출한다 — 서버를 띄우지
   않고도 저장 계약(행 수 검증·소유권·진행 계산)을 그대로 검증할 수 있다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-llm", action="store_true", help="실제 LLM 으로 운동 선택")
    args = ap.parse_args()
    settings.use_mock = not args.live_llm

    from app.schemas.enums import JobKind
    from app.services import routine_repo
    from app.services.db import get_client
    from app.worker import queue
    from app.worker.handlers import routine as routine_handler

    client = get_client()
    print(f"루틴 API 스모크 (LLM {'실호출' if args.live_llm else 'mock'})\n")

    # ── 준비: test_ 사용자 + 세션 ───────────────────────────────────────────
    user = client.table("users").insert({}).execute().data[0]
    user_id = UUID(str(user["user_id"]))
    session = client.table("analysis_session").insert({"user_id": str(user_id)}).execute().data[0]
    session_id = UUID(str(session["session_id"]))
    print(f"세션 {session_id}")

    try:
        # ── 1. 생성 ─────────────────────────────────────────────────────────
        print("\n1. 루틴 생성")
        created = routine_repo.create_routine(session_id, days_per_week=3)
        month_routine_id = UUID(str(created["month_routine_id"]))
        check("PENDING 으로 생성", created["status"] == "PENDING")
        check("생성 직후엔 비활성", created["is_active"] is False, "실패 시 화면 빈다")

        job = queue.enqueue(
            session_id,
            JobKind.ROUTINE_GEN,
            {"month_routine_id": str(month_routine_id), "days_per_week": 3},
        )
        result = routine_handler._generate(job)
        check("핸들러 성공", result.get("days") == 3, str(result))
        check("모드 판정 기록", bool(result.get("mode_basis")), result.get("mode_basis"))

        routine = routine_repo.get_routine(month_routine_id)
        check("DONE 으로 전환", routine["status"] == "DONE")
        check("DONE 이후 활성화", routine["is_active"] is True)

        # ── 2. 조회 ─────────────────────────────────────────────────────────
        print("\n2. 조회")
        active = routine_repo.get_active(session_id)
        check("활성 루틴 조회", active is not None)

        days = routine_repo.list_days(month_routine_id)
        check("Day 수 = N (휴식일 행 없음)", len(days) == 3, f"{len(days)}행")
        check("day_order 1..3 연속", [d["day_order"] for d in days] == [1, 2, 3])
        check("전 Day 에 운동 존재", all(d["exercises"] for d in days))

        first = days[0]["exercises"][0]
        check("운동에 카탈로그 참조", bool(first.get("exercise_ref")))
        check("한글명 저장", any("가" <= c <= "힣" for c in first["name"]), first["name"])
        check("RIR 저장 (중량 아님)", first.get("rir") == 2)

        # ── 3. 저장 계약 (A 지적 — DB 가 못 막는 것) ────────────────────────
        print("\n3. 저장 계약 검증")
        try:
            routine_repo.replace_days(month_routine_id, days[:2], 3)  # 2행인데 N=3
            check("행 수 불일치 거부", False)
        except routine_repo.RoutineIntegrityError:
            check("행 수 불일치 거부", True)

        # ⚠️ 활성 세션은 사용자당 1개(UNIQUE)라 **다른 사용자**로 만들어야 한다.
        other_user = client.table("users").insert({}).execute().data[0]
        other_session = (
            client.table("analysis_session")
            .insert({"user_id": other_user["user_id"]})
            .execute()
            .data[0]
        )
        try:
            routine_repo.create_log(
                session_id=UUID(str(other_session["session_id"])),
                month_routine_id=month_routine_id,
                routine_day_id=UUID(str(days[0]["routine_day_id"])),
                cycle_no=1,
            )
            check("세션 불일치 거부", False, "남의 세션으로 기록이 들어갔다")
        except routine_repo.RoutineIntegrityError:
            check("세션 불일치 거부", True)
        client.table("users").delete().eq("user_id", other_user["user_id"]).execute()

        # ── 4. 수행 기록 + 진행 ─────────────────────────────────────────────
        print("\n4. 수행 기록 · 진행 계산")
        p0 = routine_repo.progress(month_routine_id, 3)
        check("초기 다음 Day = 1", p0["next_day_order"] == 1 and p0["cycle_no"] == 1)

        for order in (1, 2):
            routine_repo.create_log(
                session_id=session_id,
                month_routine_id=month_routine_id,
                routine_day_id=UUID(str(days[order - 1]["routine_day_id"])),
                cycle_no=1,
            )
        p2 = routine_repo.progress(month_routine_id, 3)
        check("2회 후 다음 Day = 3", p2["next_day_order"] == 3, str(p2))
        check("아직 1주기", p2["cycle_no"] == 1)
        check("진행률 계산", p2["percent"] == round(2 / 12 * 100), f"{p2['percent']}%")

        # 3회째 → 다음 주기로 넘어가야 한다
        routine_repo.create_log(
            session_id=session_id,
            month_routine_id=month_routine_id,
            routine_day_id=UUID(str(days[2]["routine_day_id"])),
            cycle_no=1,
        )
        p3 = routine_repo.progress(month_routine_id, 3)
        check(
            "1주기 완주 후 2주기 Day 1", p3["cycle_no"] == 2 and p3["next_day_order"] == 1, str(p3)
        )

        # 같은 주기·같은 Day 중복 완료는 DB UNIQUE 가 막는다
        try:
            routine_repo.create_log(
                session_id=session_id,
                month_routine_id=month_routine_id,
                routine_day_id=UUID(str(days[0]["routine_day_id"])),
                cycle_no=1,
            )
            check("같은 주기 중복 완료 거부", False)
        except Exception:
            check("같은 주기 중복 완료 거부", True)

        # ── 5. 일수 변경 = 새 버전 ──────────────────────────────────────────
        print("\n5. 운동 일수 변경")
        v2 = routine_repo.create_routine(session_id, days_per_week=4)
        check("버전 증가", v2["version"] == 2, f"v{v2['version']}")
        check("기존 버전 유지 (기록 보존)", routine_repo.get_routine(month_routine_id) is not None)
        check("새 버전은 아직 비활성", v2["is_active"] is False)
        check(
            "이전 버전이 여전히 활성",
            str(routine_repo.get_active(session_id)["month_routine_id"]) == str(month_routine_id),
        )
        check("수행 기록 살아 있음", routine_repo.count_logs(month_routine_id) == 3)

    finally:
        client.table("analysis_session").delete().eq("session_id", str(session_id)).execute()
        client.table("users").delete().eq("user_id", str(user_id)).execute()
        print("\n정리 완료 (세션·사용자 삭제)")

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
