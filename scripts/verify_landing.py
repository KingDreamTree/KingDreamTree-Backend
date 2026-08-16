"""재진입 화면 분기 계약 검증 — DB 를 쓴다.

    python scripts/verify_landing.py

━━ 무엇을 지키려는 검사인가 ━━

이 서비스에서 사용자가 반복하는 행동은 **운동하고 기록하는 것**이지 사진을
다시 찍는 게 아니다. 그래서 루틴이 한 번 만들어지면 그 뒤 앱을 열 때마다
"오늘의 운동"이 첫 화면이어야 한다 (docs/FRONTEND-HANDOFF.md §1 분기표).

프론트는 `GET /sessions/active` 의 `steps` 하나로 분기하므로, 그 값이
실제 진행 상태를 정확히 반영하는지가 계약의 전부다. 특히:

  · 루틴이 DONE 이면 steps.routine 이 그걸 알려주는가 (홈 = 루틴)
  · 피드백으로 버전이 갈리면 steps 와 today 가 **새 버전**을 가리키는가
  · 그때 진행도가 0 으로 되감기지 않는가

⚠️ 이 검사가 없으면 조용히 깨진다. steps 가 옛 버전을 가리켜도 API 는
   200 을 주고, 화면은 "바뀌기 전 루틴"을 멀쩡히 보여준다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings  # noqa: E402

settings.use_mock = True  # LLM 없이 결정론 경로로

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.schemas.enums import GenerationType  # noqa: E402
from app.services import routine_repo  # noqa: E402
from app.services.db import get_client  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []
API = "/api/v1"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(label)


def _days(n: int) -> list[dict]:
    return [
        {
            "day_order": i,
            "title": f"D{i}",
            "exercises": [
                {
                    "order_index": 1,
                    "name": "스쿼트",
                    "exercise_kind": "STRENGTH",
                    "muscle_group": "대퇴사두",
                    "sets": 3,
                    "reps": 10,
                    "rest_sec": 90,
                    "rir": 2,
                }
            ],
        }
        for i in range(1, n + 1)
    ]


def main() -> int:
    print("재진입 화면 분기 검증\n")
    client = TestClient(app)
    db = get_client()

    user = db.table("users").insert({}).execute().data[0]
    uid = user["user_id"]
    H = {"X-User-Id": uid}
    N = 3

    try:
        # ── 세션 없음 → 시작 화면 ────────────────────────────────────────────
        print("1. 온보딩 전")
        r = client.get(f"{API}/sessions/active", headers=H)
        check(
            "세션 없으면 404 NO_ACTIVE_SESSION (오류 아님, 시작 화면 신호)",
            r.status_code == 404
            and (r.json().get("error") or {}).get("code") == "NO_ACTIVE_SESSION",
            str(r.json())[:80],
        )

        session = db.table("analysis_session").insert({"user_id": uid}).execute().data[0]
        sid = UUID(str(session["session_id"]))

        r = client.get(f"{API}/sessions/active", headers=H)
        steps = r.json()["steps"]
        check(
            "루틴 전에는 active_version 이 null (홈이 아직 루틴이 아님)",
            steps["routine"]["active_version"] is None,
            str(steps["routine"]),
        )

        # ── 루틴 생성 → 홈 = 오늘의 운동 ────────────────────────────────────
        print("\n2. 루틴 생성 후 — 홈은 오늘의 운동")
        v1 = routine_repo.create_routine(sid, N)
        v1id = UUID(str(v1["month_routine_id"]))
        routine_repo.replace_days(v1id, _days(N), N)
        routine_repo.update_routine(v1id, {"status": "DONE"})
        routine_repo.activate(v1id, sid)

        steps = client.get(f"{API}/sessions/active", headers=H).json()["steps"]
        check(
            "steps.routine.status = DONE (프론트가 루틴 화면으로 분기할 신호)",
            steps["routine"]["status"] == "DONE" and steps["routine"]["active_version"] == 1,
            str(steps["routine"]),
        )

        r = client.get(f"{API}/sessions/{sid}/routines/today", headers=H)
        check("today 200", r.status_code == 200, str(r.json())[:80])
        today = r.json()
        check(
            "첫 진입은 1주기 Day1",
            today["cycle_no"] == 1 and today["day"]["day_order"] == 1,
        )

        # ── 수행 → 다음 Day 로 이동 ─────────────────────────────────────────
        print("\n3. 운동 완료 — 다음 Day 로 넘어간다")
        days = routine_repo.list_days(v1id)
        routine_repo.create_log(
            session_id=sid,
            month_routine_id=v1id,
            routine_day_id=UUID(str(days[0]["routine_day_id"])),
            cycle_no=1,
        )
        today = client.get(f"{API}/sessions/{sid}/routines/today", headers=H).json()
        check(
            "완료 후 today 가 Day2 를 가리킴",
            today["day"]["day_order"] == 2,
            f"Day{today['day']['day_order']}",
        )
        check("진행 1/12", today["progress"]["completed_count"] == 1)

        # ── 피드백으로 새 버전 → 홈이 새 버전을 본다 ────────────────────────
        print("\n4. 피드백으로 루틴이 바뀌면 — 홈도 바뀐 루틴")
        v2 = routine_repo.create_routine(sid, N, GenerationType.FEEDBACK)
        v2id = UUID(str(v2["month_routine_id"]))
        changed = _days(N)
        changed[1]["exercises"][0]["name"] = "레그프레스"  # Day2 를 바꿔 둔다
        routine_repo.replace_days(v2id, changed, N)
        routine_repo.update_routine(v2id, {"status": "DONE"})
        routine_repo.activate(v2id, sid)

        steps = client.get(f"{API}/sessions/active", headers=H).json()["steps"]
        check(
            "steps 가 새 버전(v2)을 가리킴",
            steps["routine"]["active_version"] == 2,
            str(steps["routine"]),
        )

        today = client.get(f"{API}/sessions/{sid}/routines/today", headers=H).json()
        check(
            "today 가 바뀐 내용을 준다 (프론트는 버전을 안 들고 있어도 됨)",
            today["day"]["exercises"][0]["name"] == "레그프레스",
            today["day"]["exercises"][0]["name"],
        )
        check(
            "⭐ 진행도가 되감기지 않는다 (버전이 아니라 세션 기준)",
            today["progress"]["completed_count"] == 1 and today["day"]["day_order"] == 2,
            f"{today['progress']['completed_count']}/12 · Day{today['day']['day_order']}",
        )

        active = client.get(f"{API}/sessions/{sid}/routines/active", headers=H).json()
        check(
            "active 도 FEEDBACK 새 버전",
            active["version"] == 2 and active["generation_type"] == "FEEDBACK",
            f"v{active['version']} {active['generation_type']}",
        )

    finally:
        db.table("analysis_session").delete().eq("user_id", uid).execute()
        db.table("users").delete().eq("user_id", uid).execute()
        print("\n정리 완료")

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
