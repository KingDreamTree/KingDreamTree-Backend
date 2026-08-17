#!/usr/bin/env python
"""테스트로 쌓인 수행 기록(workout_log)을 지워 진행도를 1주차 DAY 1 로 되돌린다.

진행도는 날짜가 아니라 **완료 횟수**로 계산되므로(routine_repo.progress),
테스트하며 "운동마치기"를 누른 횟수만큼 주차·Day 가 앞으로 가 있다.
시연 전에 처음 화면을 보려면 그 기록을 지워야 한다.

⚠️ 되돌릴 수 없다. 기본은 **미리보기만** 하고, 실제 삭제는 --yes 를 줘야 한다.
⚠️ 지정한 세션의 기록만 지운다. 세션을 안 주면 현재 활성 루틴의 세션을 쓴다.

    python scripts/reset_test_logs.py                    # 뭐가 지워질지 보기만
    python scripts/reset_test_logs.py --yes              # 실제 삭제
    python scripts/reset_test_logs.py --session <UUID> --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.db import get_client  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="테스트 수행 기록 삭제")
    ap.add_argument("--session", help="대상 세션 UUID (생략 시 현재 활성 루틴의 세션)")
    ap.add_argument("--yes", action="store_true", help="실제로 삭제한다")
    args = ap.parse_args()

    client = get_client()

    session_id = args.session
    days_per_week = None
    if session_id is None:
        rows = (
            client.table("month_routine")
            .select("session_id,exercise_days_per_week")
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if not rows:
            print("활성 루틴이 없습니다. --session 으로 직접 지정하세요.")
            return 1
        session_id = str(rows[0]["session_id"])
        days_per_week = rows[0]["exercise_days_per_week"]

    logs = (
        client.table("workout_log")
        .select("workout_log_id,cycle_no,completed_at,feedback_text")
        .eq("session_id", session_id)
        .order("completed_at")
        .execute()
        .data
    )

    print(f"세션 {session_id}")
    print(f"수행 기록 {len(logs)}건")
    for row in logs:
        note = row.get("feedback_text")
        mark = f"  피드백: {note[:40]}" if note else ""
        print(f"  · {str(row['completed_at'])[:19]}  {row['cycle_no']}주기{mark}")

    if not logs:
        print("\n지울 것이 없습니다 — 이미 1주차 DAY 1 상태입니다.")
        return 0

    # 피드백이 달린 기록은 진짜 사용 흔적일 수 있다 — 지우기 전에 눈에 띄게 알린다.
    with_feedback = [r for r in logs if r.get("feedback_text")]
    if with_feedback:
        print(f"\n⚠️ 이 중 {len(with_feedback)}건에는 피드백 텍스트가 달려 있습니다.")

    if not args.yes:
        print("\n미리보기입니다. 실제로 지우려면 --yes 를 붙이세요.")
        return 0

    client.table("workout_log").delete().eq("session_id", session_id).execute()
    left = (
        client.table("workout_log")
        .select("workout_log_id")
        .eq("session_id", session_id)
        .execute()
        .data
    )
    print(f"\n삭제 완료. 남은 기록 {len(left)}건.")
    if days_per_week:
        done = len(left)
        print(f"→ 이제 {done // days_per_week + 1}주차 DAY {done % days_per_week + 1} 부터 시작합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
