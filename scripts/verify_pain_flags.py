"""#171 점검 — 사용자가 통증을 말했는데 코치가 flag_contraindication 을 안 부른 대화가 있는가.

    python scripts/verify_pain_flags.py

⚠️ 읽기 전용 — DB 에 아무것도 쓰지 않고 GPT 도 부르지 않는다.
⚠️ 대화 원문은 출력하지 않는다 (건강 정보). 건수와 시각만 찍는다.

무엇을 보나
    routine_revision.raw_response.conversation 에 박제된 코치 대화를 전부 읽어,
    사용자 발화에 통증 표현(services/routine._PAIN_TERMS 재사용)이 있는 대화를 고른다.
    그 대화 안에 flag_contraindication 도구 호출이 하나라도 있으면 "등록됨",
    없으면 "누락" 으로 센다.

왜 필요한가
    부상 주의 등록은 코치(LLM)의 판정에 달려 있고 temperature=0.4 라 흔들릴 수 있다.
    등록이 빠지면 다음 루틴이 아픈 부위에 부담 주는 운동을 그대로 넣는다 — 안전 문제라
    1건이라도 나오면 최우선으로 올린다 (#171 확인 항목).

한계
    [적용]까지 간 대화만 저장되므로, 적용 없이 끝난 대화의 누락은 여기서 안 보인다.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.db import get_client  # noqa: E402
from app.services.routine import _PAIN_TERMS  # noqa: E402


def main() -> int:
    client = get_client()
    rows = (
        client.table("routine_revision")
        .select("routine_revision_id,created_at,raw_response")
        .order("created_at")
        .execute()
        .data
    )

    total = with_pain = flagged = 0
    missing: list[str] = []
    for row in rows:
        conv = (row.get("raw_response") or {}).get("conversation") or []
        if not conv:
            continue
        total += 1
        user_texts = [str(m.get("content") or "") for m in conv if m.get("role") == "user"]
        if not any(term in text for text in user_texts for term in _PAIN_TERMS):
            continue
        with_pain += 1
        has_flag = any(
            (tc.get("function") or {}).get("name") == "flag_contraindication"
            for m in conv
            for tc in (m.get("tool_calls") or [])
        )
        if has_flag:
            flagged += 1
        else:
            missing.append(row["created_at"][:19])

    print(f"저장된 코치 대화 {total}건 중 통증 언급 {with_pain}건")
    print(f"  부상 주의 등록됨: {flagged}건")
    print(f"  누락(통증 언급했는데 flag 없음): {len(missing)}건")
    for at in missing:
        print(f"    - {at}")
    if missing:
        print(
            "\n⚠️ 누락이 있다 — 해당 시각의 대화를 로컬에서 열어 원인(판정 실패 / 통증 아님 오탐)을 가른다."
        )
        return 1
    print("\n통증 언급 대화 전부 등록됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
