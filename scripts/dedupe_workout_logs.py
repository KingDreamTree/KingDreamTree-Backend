#!/usr/bin/env python
"""중복 완료 기록(workout_log) 정리 — 연타·새로고침으로 몇 초 사이에 한 장 더 들어간 것.

    python scripts/dedupe_workout_logs.py                       # 전 세션 미리보기
    python scripts/dedupe_workout_logs.py --session <UUID>      # 한 세션만
    python scripts/dedupe_workout_logs.py --window 30           # 간격 기준 (기본 60초)
    python scripts/dedupe_workout_logs.py --yes                 # 실제 삭제
    python scripts/dedupe_workout_logs.py --self-test           # 판정 로직만 (DB 불필요)

━━ 왜 필요한가 (#169) ━━

완료 버튼 연타, 그리고 «보내는 중 새로고침» 경로에서 프론트가 **다음 Day 로 한 번 더**
보냈다. workout_log_uniq 는 (루틴, 주기, Day) 라 Day 가 다르면 통과한다 — 실측
(2026-09-07) 같은 세션 60초 이내 연속 48건, 최단 1.2초, 전부 다른 Day.

프론트는 막았다 (fe #264). 그러나 **이미 들어간 행은 남아 있다.** 서버 progress() 는
위치(cycle_no·next_day_order)를 마지막 기록에서 읽도록 바뀌었지만(#173) 개수
(completed_count·percent)는 로그를 그대로 센다. 그래서 중복이 든 세션은
«14/16회 · 88%» 인데 Day 는 8 처럼 **영구히 어긋난다.**

━━ 무엇을 지우나 ━━

한 세션 안에서 completed_at 순으로 볼 때, **마지막으로 남긴 기록**으로부터 --window 초
안에 들어온 기록. 사람이 그 시간에 운동을 마칠 수는 없다.

    · **처음 것을 남기고 뒤의 것을 지운다.** 처음 것이 사용자가 실제로 누른 것이고, 뒤의
      것은 프론트가 Day 를 전진시켜 보낸 유령이다 (fe 4c47815 주석의 ①~⑤).
    · 기준은 «직전 행» 이 아니라 **«마지막으로 남긴 행»** 이다. 직전 행 기준이면
      0초·50초·100초 가 사슬로 이어져 100초 행까지 지워진다 — 지우는 작업은
      적게 지우는 쪽으로 틀려야 한다.

━━ 절대 지우지 않는 것 ━━

    · feedback_text 가 있는 행 — 피드백 문장은 **workout_log 에만** 저장되고 «왜 루틴이
      바뀌었는지» 이력이 조인으로 읽는다 (routes/workout_logs.py 모듈 주석).
      지우면 그 이력의 설명이 빈다.
    · routine_revision.source_log_id 가 가리키는 행 — FK 가 ON DELETE SET NULL 이라
      리비전은 남지만 출처가 끊긴다.
    → 두 경우 모두 «수동 확인» 으로 따로 찍고 건너뛴다.

⚠️ 되돌릴 수 없다. 기본은 **미리보기만** 한다. 실제 삭제는 --yes.
⚠️ **Dev 에서 먼저 돌려라** (#161). 운영에 돌리기 전에 미리보기 출력을 사람이 읽을 것.
⚠️ 지우면 사용자의 게이지가 **뒤로 간다** — 유령 Day 가 빠지면서 실제로 한 곳으로
   돌아가는 것이다. 틀렸던 게 맞아지는 것이지만 화면에서는 줄어든 것으로 보인다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_WINDOW_SEC = 60


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def find_duplicates(
    logs: list[dict[str, Any]],
    window_sec: float,
    referenced: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """한 세션의 기록에서 (지울 것, 수동 확인할 것) 을 가른다. DB 를 보지 않는다.

    Args:
        logs:       한 세션의 workout_log 행. 순서는 상관없다 (여기서 정렬한다).
        window_sec: «마지막으로 남긴 행» 으로부터 이 초 안이면 중복.
        referenced: routine_revision 이 source_log_id 로 가리키는 workout_log_id.
    """
    rows = sorted(logs, key=lambda r: _ts(r["completed_at"]))
    delete: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    anchor: datetime | None = None
    for row in rows:
        at = _ts(row["completed_at"])
        if anchor is not None and (at - anchor).total_seconds() < window_sec:
            if row.get("feedback_text") or str(row["workout_log_id"]) in referenced:
                manual.append(row)
            else:
                delete.append(row)
            continue  # 기준은 움직이지 않는다 — «마지막으로 남긴 행» (모듈 주석)
        anchor = at
    return delete, manual


def _self_test() -> int:
    def log(i: int, sec: float, **kw: Any) -> dict[str, Any]:
        base = datetime(2026, 9, 7, 12, 0, 0).timestamp() + sec
        return {
            "workout_log_id": f"L{i}",
            "completed_at": datetime.fromtimestamp(base).isoformat(),
            **kw,
        }

    fb = {"feedback_text": "어깨 아파요"}
    # (이름, 기록, 리비전이 가리키는 id, 지울 것, 수동 확인)
    cases = [
        ("연타 — 1.2초 뒤 한 장", [log(1, 0), log(2, 1.2)], set(), ["L2"], []),
        ("정상 — 하루 간격", [log(1, 0), log(2, 86400)], set(), [], []),
        ("처음 것을 남긴다 (순서 뒤집힘)", [log(2, 3), log(1, 0)], set(), ["L2"], []),
        ("사슬 금지 — 0·50·100초", [log(1, 0), log(2, 50), log(3, 100)], set(), ["L2"], []),
        ("피드백 달린 중복은 수동", [log(1, 0), log(2, 2, **fb)], set(), [], ["L2"]),
        ("리비전이 가리키면 수동", [log(1, 0), log(2, 2)], {"L2"}, [], ["L2"]),
        ("경계 — 정확히 window 초", [log(1, 0), log(2, 60)], set(), [], []),
        ("기록 없음", [], set(), [], []),
    ]
    failed = 0
    for label, logs, ref, exp_del, exp_man in cases:
        d, m = find_duplicates(logs, DEFAULT_WINDOW_SEC, ref)
        got = ([r["workout_log_id"] for r in d], [r["workout_log_id"] for r in m])
        ok = got == (exp_del, exp_man)
        failed += not ok
        print(f"{'OK  ' if ok else 'FAIL'}  {label}   지울것={got[0]} 수동={got[1]}")
    print(f"\n{len(cases) - failed}/{len(cases)} 통과")
    return 1 if failed else 0


def _day(row: dict[str, Any]) -> str:
    d = (row.get("routine_day") or {}).get("day_order")
    return f"{row['cycle_no']}주기 Day {d}" if d is not None else f"{row['cycle_no']}주기"


def main() -> int:
    ap = argparse.ArgumentParser(description="중복 완료 기록 정리 (#169)")
    ap.add_argument("--session", help="대상 세션 UUID (생략 시 전 세션)")
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_SEC, help="중복으로 볼 간격(초)")
    ap.add_argument("--yes", action="store_true", help="실제로 삭제한다")
    ap.add_argument("--self-test", action="store_true", help="판정 로직만 점검 (DB 불필요)")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    # ⚠️ 지연 import — --self-test 는 DB 설정 없이 돌아야 한다.
    from app.services.db import get_client

    client = get_client()
    q = client.table("workout_log").select(
        "workout_log_id,session_id,cycle_no,completed_at,feedback_text,routine_day(day_order)"
    )
    if args.session:
        q = q.eq("session_id", args.session)
    logs = q.order("completed_at").execute().data

    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in logs:
        by_session.setdefault(str(row["session_id"]), []).append(row)

    ids = [str(r["workout_log_id"]) for r in logs]
    referenced: set[str] = set()
    if ids:
        refs = (
            client.table("routine_revision")
            .select("source_log_id")
            .in_("source_log_id", ids)
            .execute()
            .data
        )
        referenced = {str(r["source_log_id"]) for r in refs if r.get("source_log_id")}

    to_delete: list[dict[str, Any]] = []
    to_review: list[dict[str, Any]] = []
    print(f"기록 {len(logs)}건 · 세션 {len(by_session)}개 · 기준 {args.window:g}초\n")
    for sid, rows in by_session.items():
        d, m = find_duplicates(rows, args.window, referenced)
        if not d and not m:
            continue
        print(f"세션 {sid}  ({len(rows)}건 → {len(rows) - len(d)}건)")
        for row in sorted(rows, key=lambda r: _ts(r["completed_at"])):
            mark = "  삭제" if row in d else "  확인" if row in m else "  유지"
            note = f"  피드백: {row['feedback_text'][:30]}" if row.get("feedback_text") else ""
            print(f"  {mark}  {str(row['completed_at'])[:19]}  {_day(row)}{note}")
        to_delete += d
        to_review += m

    if not to_delete and not to_review:
        print("중복이 없습니다.")
        return 0

    print(f"\n지울 것 {len(to_delete)}건 · 수동 확인 {len(to_review)}건")
    if to_review:
        print("⚠️ 수동 확인 행은 피드백이 달렸거나 루틴 변경 이력이 가리키고 있어 건너뜁니다.")

    if not args.yes:
        print("\n미리보기입니다. 실제로 지우려면 --yes 를 붙이세요.")
        return 0
    if not to_delete:
        return 0

    del_ids = [str(r["workout_log_id"]) for r in to_delete]
    client.table("workout_log").delete().in_("workout_log_id", del_ids).execute()
    left = (
        client.table("workout_log")
        .select("workout_log_id")
        .in_("workout_log_id", del_ids)
        .execute()
        .data
    )
    print(f"\n삭제 완료 — 요청 {len(del_ids)}건, 남은 것 {len(left)}건 (0 이어야 한다).")
    return 1 if left else 0


if __name__ == "__main__":
    raise SystemExit(main())
