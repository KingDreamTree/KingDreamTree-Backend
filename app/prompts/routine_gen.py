"""F10 — 운동 선택 프롬프트 (후보 제약형).

⚠️ LLM 의 역할은 **선택뿐**이다. 분할·세트·횟수·유산소 여부는 코드가 이미
   정했고(routine_templates), 후보 목록도 코드가 걸렀다(exercise_catalog).
   LLM 은 슬롯마다 후보 중 exercise_ref 하나를 고른다 — 목록 밖 운동을
   낼 방법이 없으므로 환각이 구조적으로 차단된다.

그럼 LLM 이 왜 필요한가? 후보 정렬 1순위를 기계적으로 뽑으면 매 사용자가
똑같은 루틴을 받고, 주간 중복(같은 운동 6번)도 코드는 문맥 없이 못 피한다.
LLM 은 **다양성·중복 회피·진단 문맥**(약점 부위 슬롯에 그 부위를 정확히
때리는 운동)을 맡는다. 무엇을 골라도 규칙 위반이 아닌 집합 안에서만.
"""

from __future__ import annotations

import json
from typing import Any

# ⚠️ 프롬프트 본문은 DB(prompt_version)에 있다 (2026-09-11, #164) — 'routine.select'.
#    보기: python scripts/prompt_version.py show routine.select
#    고치기: python scripts/prompt_version.py new routine.select <파일> --note "…" (배포 없음)


def build_selection_prompt(
    days: list[dict[str, Any]],
    slot_candidates: dict[str, list[dict[str, Any]]],
    priority_parts: list[str],
) -> str:
    """슬롯별 후보를 붙인 사용자 메시지를 만든다.

    slot_id 형식: "d{day_order}s{slot_index}" — 코드가 응답을 되매핑하는 키.
    후보는 ref/이름/장비만 준다 — 근육 상세까지 주면 토큰만 늘고, 어차피
    후보는 이미 그 슬롯 근육군으로 걸러져 있다.
    """
    lines: list[str] = []
    if priority_parts:
        lines.append(f"약점 부위(진단): {', '.join(priority_parts)}")
        lines.append("")

    for day in days:
        lines.append(f"## Day {day['day_order']} — {day['title']}")
        for idx, slot in enumerate(day["slots"]):
            slot_id = f"d{day['day_order']}s{idx}"
            if slot.get("kind") == "CARDIO":
                continue  # 유산소는 코드가 직접 고른다
            tags = []
            if slot.get("boosted_by"):
                tags.append(f"focus={slot['boosted_by']}")
            if slot.get("single_side"):
                tags.append("single_side: true")
            tag_txt = f"  ({', '.join(tags)})" if tags else ""
            lines.append(f"- {slot_id}: {slot['muscle_group']} {slot['sets']}세트{tag_txt}")
            cands = [
                {
                    "ref": c["exercise_ref"],
                    "name": c.get("name_ko") or c["name_en"],
                    "eq": c["equipments"],
                }
                for c in slot_candidates.get(slot_id, [])
            ]
            lines.append(f"  candidates: {json.dumps(cands, ensure_ascii=False)}")
        lines.append("")

    lines.append("모든 슬롯에 대해 selections 를 반환하세요.")
    return "\n".join(lines)
