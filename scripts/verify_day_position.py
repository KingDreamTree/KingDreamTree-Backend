"""#169 검증 — 코치·게이지·오늘의 운동이 «마지막 완료 기록» 기준으로 같은 Day 를 가리키는가.

    python scripts/verify_day_position.py                      # 경계값 + 활성 세션 전수 대조
    python scripts/verify_day_position.py --session <uuid>     # 한 세션만 자세히 (마지막 기록 3건 포함)

⚠️ 읽기 전용 — DB 에 아무것도 쓰지 않는다. GPT 도 부르지 않는다.

무엇을 보나
    1. routine_repo._next_position 의 경계값 — 기록 없음 / 주기 중간 / 주기 마지막 Day /
       마지막 주기 상한 / 「운동 일수 조정」으로 day_order 가 현재 일수를 넘는 기록
    2. 활성 루틴이 있는 모든 세션에 대해
           옛 방식(완료 횟수 역산)  vs  새 방식(마지막 기록의 실제 Day)
       을 나란히 찍고, 세 값 — 게이지·오늘의 운동(progress.next_day_order) · 코치(_today_day)
       — 가 마지막 기록과 서로 맞는지 확인한다.
    3. 옛 방식과 결과가 달라진 세션은 "기록이 밀린 세션"이어야 한다. --session 으로 보면
       마지막 기록 3건의 간격을 함께 찍어 더블클릭 중복 기록인지 눈으로 확인할 수 있다.

왜 필요한가
    #169 — 더블클릭·옛 user_id 승계로 완료 횟수가 밀리면 세 화면이 동시에 엉뚱한 Day 를
    가리켰다 ("오늘 벤치프레스는 포함되지 않았어요", 실측 2026-08-24). 수리의 핵심은
    세 화면이 **한 기준(마지막 기록)** 을 쓰는 것이고, 그게 유지되는지가 회귀 포인트다.

종료 코드: 불일치가 하나라도 있으면 1.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.routes.coach_chat import _today_day  # noqa: E402
from app.services import routine_repo  # noqa: E402
from app.services.db import get_client  # noqa: E402
from app.services.routine_repo import (  # noqa: E402
    TOTAL_CYCLES,
    _next_position,
    count_session_logs,
    progress,
)


def check_pure() -> bool:
    cases = [
        (None, 3, (1, 1), "기록 없음 → 1주기 Day 1"),
        ({"day_order": 1, "cycle_no": 1}, 3, (1, 2), "1주기 Day 1 완료 → Day 2"),
        ({"day_order": 3, "cycle_no": 1}, 3, (2, 1), "1주기 마지막 Day → 2주기 Day 1"),
        ({"day_order": 3, "cycle_no": 4}, 3, (4, 1), "마지막 주기 마지막 Day → 상한 유지"),
        ({"day_order": 7, "cycle_no": 2}, 4, (3, 1), "일수 축소로 day_order > n → 다음 주기"),
        ({"day_order": 2, "cycle_no": 2}, 5, (2, 3), "주기 중간"),
    ]
    ok = True
    print("── 1. 경계값 ──")
    for last, n, expected, label in cases:
        got = _next_position(last, n)
        mark = "OK  " if got == expected else "FAIL"
        ok &= got == expected
        print(f"  {mark} {label}: {got}")
    return ok


def _old_formula(done: int, n: int) -> tuple[int, int, int]:
    """수리 전 계산 — (주기, 다음 Day, 코치 Day). 비교용으로만 남긴다."""
    total = n * TOTAL_CYCLES
    d = min(done, total)
    return (
        min(d // n + 1, TOTAL_CYCLES),
        (d % n) + 1,
        ((max(done, 1) - 1) % (n or 1)) + 1,
    )


def _last_logs(session_id: UUID, limit: int = 3) -> list[dict]:
    return (
        get_client()
        .table("workout_log")
        .select("cycle_no, completed_at, routine_day(day_order)")
        .eq("session_id", str(session_id))
        .order("completed_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def check_sessions(only: str | None) -> bool:
    client = get_client()
    q = client.table("month_routine").select("session_id,month_routine_id,exercise_days_per_week")
    q = q.eq("is_active", True)
    if only:
        q = q.eq("session_id", only)
    actives = q.execute().data

    print(f"\n── 2. 활성 세션 대조 ({len(actives)}개) ──")
    ok = True
    changed = 0
    for mr in actives:
        sid = UUID(mr["session_id"])
        mid = UUID(mr["month_routine_id"])
        n = mr["exercise_days_per_week"]
        done = count_session_logs(sid)
        if done == 0 and not only:
            continue

        last = routine_repo.last_session_log(sid)
        prog = progress(mid, n, sid)
        days = routine_repo.list_days(mid)
        coach_day = _today_day(days, sid)["day_order"] if days else None

        # 세 값이 마지막 기록과 맞는가 — 이게 #169 의 회귀 포인트
        expect_cycle, expect_next = _next_position(last, n)
        if prog["is_completed"]:
            expect_cycle, expect_next = TOTAL_CYCLES, 1
        expect_coach = last["day_order"] if last else 1
        if coach_day is not None and not any(d["day_order"] == expect_coach for d in days):
            expect_coach = days[0]["day_order"]  # 활성 버전에 그 Day 가 없으면 폴백

        consistent = (prog["cycle_no"], prog["next_day_order"], coach_day) == (
            expect_cycle,
            expect_next,
            expect_coach,
        )
        ok &= consistent

        old = _old_formula(done, n)
        new = (prog["cycle_no"], prog["next_day_order"], coach_day)
        differs = old != new
        changed += differs
        if differs or not consistent or only:
            flag = "FAIL" if not consistent else ("달라짐" if differs else "동일")
            print(
                f"  [{flag}] {mr['session_id'][:8]} 기록 {done}건 | "
                f"옛(주기,다음Day,코치Day)={old} → 새={new} | 마지막 기록="
                f"{(last['cycle_no'], last['day_order']) if last else None}"
            )
        if only:
            prev = None
            for row in _last_logs(sid):
                t = datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00"))
                gap = f" (+{(prev - t).total_seconds():.0f}s 뒤 기록)" if prev else ""
                print(
                    f"      {row['completed_at'][:19]} 주기 {row['cycle_no']} "
                    f"Day {(row.get('routine_day') or {}).get('day_order')}{gap}"
                )
                prev = t

    print(f"\n  옛 방식과 결과가 달라진 세션: {changed}개 (기록이 밀린 세션이어야 정상)")
    print("  세 화면 값이 마지막 기록과 전부 일치" if ok else "  ⚠️ 불일치 있음")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--session", help="이 세션만 자세히 본다")
    args = parser.parse_args()

    ok = check_pure()
    ok &= check_sessions(args.session)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
