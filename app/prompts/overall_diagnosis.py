"""F09 — 종합 진단 프롬프트 (텍스트 전용).

━━ 왜 F08 과 같은 호출에 합치지 않는가 ━━

이미지가 필요 없기 때문이다. 종합 진단의 입력은 F08 이 이미 뽑아낸 부위별
결론이지 픽셀이 아니다. 분리하면:

  * 이 호출에는 이미지 토큰이 0 이다 (F08 호출의 약 1/5 비용)
  * 요약 문구를 튜닝할 때 **이미지를 다시 올리지 않고** 재실행할 수 있다.
    프롬프트 반복 수정이 가장 많이 일어나는 곳이 여기다
  * F08 이 성공했는데 F09 만 실패해도 부위별 진단은 남는다

llm-strategy.md §F09 의 "Prompt Chaining" 이 이것이다.
"""

from typing import Any

SYSTEM_PROMPT = """당신은 체형 분석 결과를 종합하는 전문가입니다.
부위별 비교 진단이 이미 끝났고, 당신은 그것을 종합해 사용자에게 보여줄
최종 진단을 만듭니다.

# 원칙

- 부위별 진단에 없는 사실을 새로 만들지 마세요. 주어진 것만 종합합니다.
- 판단 불가(blocked) 부위는 "모른다"로 두세요. 없는 걸 있다고 하지 마세요.
- 사용자에게 그대로 보여지는 문장입니다. 평가·비하 없이, 개선 관점으로.
- 의학적 진단이나 질병을 언급하지 마세요.

# 유사도 점수

0~100 정수. 레퍼런스 체형에 얼마나 가까운지입니다.

- 판단된 부위들의 격차를 종합해 매깁니다
- gap_level 이 NONE 에 가까울수록 높고, SIGNIFICANT 가 많을수록 낮습니다
- 판단 불가 부위는 점수에 반영하지 마세요 (모르는 것을 감점하지 않습니다)
- score_rationale 에 그렇게 매긴 근거를 한 문장으로 씁니다

⚠️ 0점이나 100점 같은 극단값은 근거가 확실할 때만 쓰세요.

# 출력 형식

JSON 하나만 반환합니다.

{
  "similarity_score": 68,
  "score_rationale": "상체 근육량 격차가 크고 하체는 근접",
  "summary": "상체 중심 개선이 필요합니다. 어깨와 팔 근육 강화가 가장 우선입니다.",
  "priority_parts": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
  "strengths": ["하체 균형이 좋습니다"],
  "cautions": ["좌우 팔 근육량 차이가 있어 균형 운동을 권합니다"]
}

## 필드 규칙

- similarity_score : 0~100 정수
- score_rationale  : 점수 근거 한 문장
- summary          : 한국어 2~3문장. 사용자가 가장 먼저 읽는 문장입니다.
- priority_parts   : 개선 우선순위 class_name **배열**, 시급한 순. 최대 5개.
                     주어진 부위 목록에 있는 이름만 쓰세요.
- strengths        : 이미 좋은 점 **배열**. 없으면 빈 배열.
- cautions         : 주의할 점 **배열**. 좌우 불균형, 판단 불가 부위가 많음 등.
                     없으면 빈 배열.

⚠️ priority_parts / strengths / cautions 는 전부 배열입니다. 문자열 하나로
   이어붙이지 마세요 — 화면에서 항목별로 나열됩니다."""


def _part_line(p: dict[str, Any]) -> str:
    if p.get("blocked_reason"):
        return f"- {p['class_name']}: 판단 불가 — {p['blocked_reason']}"

    bits = [f"격차 {p.get('gap_level') or '?'}"]
    if p.get("priority") is not None:
        bits.append(f"우선순위 {p['priority']}")
    if p.get("confidence"):
        bits.append(f"확신도 {p['confidence']}")

    line = f"- {p['class_name']} ({' · '.join(bits)})"
    if p.get("assessment"):
        line += f"\n    {p['assessment']}"
    for d in p.get("differences") or []:
        line += f"\n    · {d}"
    return line


def _inbody_block(inbody: dict[str, Any] | None) -> str:
    if not inbody:
        return "인바디 결과 없음 — 시각 진단만으로 종합하세요."

    body = inbody.get("body") or {}
    bits = []
    for label, key, unit in (
        ("체중", "weight", "kg"),
        ("골격근량", "skeletal_muscle_mass", "kg"),
        ("체지방률", "body_fat_percentage", "%"),
        ("BMI", "bmi", ""),
    ):
        if body.get(key) is not None:
            bits.append(f"{label} {body[key]}{unit}")

    lines = [" · ".join(bits)] if bits else []
    for segment, s in (inbody.get("segments") or {}).items():
        if s.get("lean_mass") is not None:
            extra = (
                f" (표준 대비 {s['lean_percentage']}%)"
                if s.get("lean_percentage") is not None
                else ""
            )
            lines.append(f"- {segment}: 제지방 {s['lean_mass']}kg{extra}")

    return "\n".join(lines) if lines else "인바디 수치 없음"


def build_overall_prompt(
    parts: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    failed: list[str],
    inbody: dict[str, Any] | None,
) -> str:
    """F09 사용자 메시지. 이미지 없음.

    Args:
        parts:   판단에 성공한 부위 진단 (gap_level 이 있는 것)
        blocked: 판단 불가로 처리된 부위 (gap_level 이 null)
        failed:  응답 자체가 없거나 형식이 깨진 부위의 class_name
    """
    sections = [
        "# 부위별 진단 결과",
        "",
        "\n".join(_part_line(p) for p in parts) if parts else "(판단된 부위 없음)",
    ]

    if blocked:
        sections += [
            "",
            "## 판단 불가 부위",
            "",
            "\n".join(_part_line(p) for p in blocked),
            "",
            "※ 이 부위들은 점수에 반영하지 마세요. 다만 판단 불가가 많다면 "
            "cautions 에 '일부 부위는 복장·각도로 확인이 어려웠다'는 취지를 넣으세요.",
        ]

    if failed:
        sections += [
            "",
            f"## 진단 실패 부위: {', '.join(failed)}",
            "",
            "※ 기술적 실패입니다. 점수에 반영하지 말고 언급도 하지 마세요.",
        ]

    sections += [
        "",
        "# 인바디",
        "",
        _inbody_block(inbody),
        "",
        "# 요청",
        "",
        "위 결과를 종합해 최종 진단을 JSON 으로 반환하세요.",
    ]
    return "\n".join(sections)
