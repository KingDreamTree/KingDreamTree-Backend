"""ROUTINE_GEN 프롬프트 — LLM은 **1주치 슬롯에 운동만 채운다**.

⚠️ 분할·휴식일·세트수는 코드(`routine_templates.py`)가 이미 정했다.
   LLM에게 분할 설계를 맡기지 않는다 — 근거는 옵시디언 [[08 - 해커톤 MVP 범위]].

   LLM 호출 1회 → 1주 계획 → 코드가 4주 복제 (토큰 1/4, 28행 보장)
"""

import json

SYSTEM_PROMPT = """너는 초보자를 담당하는 개인 트레이너다. 이미 확정된 1주 운동 계획표의 빈 슬롯에 **구체적인 운동만** 채운다.

절대 규칙:
- 슬롯의 muscle_group·sets·reps·rest_sec은 **그대로 유지**한다. 바꾸지 마라.
- 슬롯을 추가하거나 삭제하지 마라. 휴식일에 운동을 넣지 마라.
- 초보자 대상이므로 복합운동(compound) 위주로, 머신·덤벨처럼 폼이 안정적인 종목을 우선한다.
- 금기(contraindications) 부위에 부하가 걸리는 운동은 절대 넣지 마라.
- weight_kg는 초보자 기준 보수적 추정치. 확신이 없으면 null로 두고 note에 "체력에 맞게 조정" 이라고 써라.
- 모든 텍스트는 한국어. JSON만 반환한다."""

_OUTPUT_FORMAT = """{
  "goal": "루틴 전체 목표 한 문장",
  "focus_areas": ["가중치를 더 준 부위 class_name 또는 근육군"],
  "week": [
    {
      "day_of_week": 1,
      "is_rest": false,
      "title": "전신 A",
      "estimated_duration_min": 55,
      "exercises": [
        {
          "order_index": 1,
          "muscle_group": "가슴",
          "name": "덤벨 벤치프레스",
          "equipment": "덤벨",
          "target_muscle": "대흉근",
          "sets": 3,
          "reps": 10,
          "weight_kg": 12.0,
          "rest_sec": 90,
          "note": null
        }
      ]
    },
    { "day_of_week": 2, "is_rest": true, "title": "휴식", "estimated_duration_min": null, "exercises": [] }
  ]
}"""


def build_generate_prompt(
    week_template: list[dict],
    overall_diagnosis: dict,
    inbody: dict | None,
    contraindications: list[dict],
) -> tuple[str, str]:
    """(system_prompt, user_message) 반환.

    Args:
        week_template: routine_templates.get_week_template() 결과 (7일 슬롯)
        overall_diagnosis: F09 종합 진단 (priority_parts 등)
        inbody: 인바디 수치. 없으면 None — 없어도 루틴은 생성된다
        contraindications: 세션 누적 금기
    """
    sections = [
        f"1주 계획표 (슬롯 고정 — 이 구조를 그대로 유지):\n"
        f"{json.dumps(week_template, ensure_ascii=False, indent=2)}",
        f"\n체형 진단 결과:\n{json.dumps(overall_diagnosis, ensure_ascii=False, indent=2)}\n"
        f"→ 약점 부위에는 **볼륨 가중치**만 준다. 부위를 빼거나 추가하지 마라.",
    ]

    if inbody:
        sections.append(
            f"\n인바디 수치:\n{json.dumps(inbody, ensure_ascii=False, indent=2)}\n"
            f"→ 부위별 percentage가 100% 미만이면 해당 근육군 운동 선택 시 참고하고,\n"
            f"   체지방률·내장지방레벨이 높으면 유산소 성격의 종목을 일부 섞어라."
        )

    if contraindications:
        sections.append(
            f"\n⚠️ 금기 (해당 부위 부하 운동 금지):\n"
            f"{json.dumps(contraindications, ensure_ascii=False)}"
        )

    sections.append(f"\n\n아래 형식의 JSON으로 1주 계획을 반환해줘:\n{_OUTPUT_FORMAT}")

    return SYSTEM_PROMPT, "".join(sections)
