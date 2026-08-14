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

# 유사도 점수 — 당신이 만들지 않습니다

점수는 부위별 격차 등급에서 **코드가 규칙으로 이미 계산**했고, 아래에 주어집니다.
당신의 일은 그 점수와 **일치하는 이야기**를 쓰는 것입니다.

- summary 에서 점수를 언급한다면 주어진 값을 그대로 쓰세요
- 점수를 새로 매기거나, 주어진 점수와 다른 인상("거의 비슷합니다" ↔ 40점)을
  주는 문장을 쓰지 마세요

# 출력 형식

JSON 하나만 반환합니다.

{
  "summary": "...",
  "priority_parts": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
  "strengths": ["하체 균형이 좋습니다"],
  "cautions": ["좌우 팔 근육량 차이가 있어 균형 운동을 권합니다"]
}

## 필드 규칙

- summary          : 한국어 2~3문장. 사용자가 가장 먼저 읽는 문장입니다. (아래 §summary)
- priority_parts   : 개선 우선순위 class_name **배열**, 시급한 순. 최대 5개.
                     주어진 부위 목록에 있는 이름만 쓰세요.
- strengths        : 이미 좋은 점 **배열**. 없으면 빈 배열.
- cautions         : 주의할 점 **배열**. 좌우 불균형, 판단 불가 부위가 많음 등.
                     없으면 빈 배열.

⚠️ priority_parts / strengths / cautions 는 전부 배열입니다. 문자열 하나로
   이어붙이지 마세요 — 화면에서 항목별로 나열됩니다.

# §summary — 코치가 첫 상담을 끝내며 하는 말

읽는 사람은 운동을 처음 하는 사람입니다. 헬스장 용어·해부학 용어를 모릅니다.
제지방량→근육량, 레퍼런스→목표 체형, 대퇴사두근→허벅지 앞쪽 근육처럼
쉬운 말로 바꾸고, 부위는 «왼쪽/오른쪽»으로 부르세요.

세 문장 구조를 지키세요:

  1문장  현재 상태를 담백하게 — **칭찬은 근거가 있을 때만** (아래 §칭찬 자격)
  2문장  좁혀야 할 격차 **하나만** 짚기 — 전부 나열하면 아무것도 전달 안 됩니다
  3문장  4주간 무엇부터 할지 — 구체적 부위, 실행 가능한 방향

## §칭찬 자격 — 근거 없는 칭찬은 신뢰를 깎습니다

1문장에 "좋다"는 표현을 쓰려면 **자격 조건**이 있습니다:

  격차 NONE 부위가 있다        → "○○는 목표 체형과 거의 같아요" 가능
  SLIGHT 뿐이다                → "○○는 목표에 가까운 편이에요" 까지만
  전부 MODERATE/SIGNIFICANT    → **칭찬 금지.** 1문장은 상태 서술로:
                                  "지금은 목표 체형과 거리가 있는 상태예요.
                                   그만큼 바뀔 수 있는 폭도 커요." 처럼
  confidence LOW 인 부위        → 그 부위로는 칭찬하지 마세요. 흐릿하게 본
                                  것을 "좋다"고 말하는 셈입니다

⚠️ "가장 격차가 작은 부위 = 좋은 점"이 **아닙니다.** 전 부위가 SIGNIFICANT 인데
   그중 하나를 골라 "균형이 잡혀 있어요"라고 하면 거짓말입니다. 상대적으로 덜
   나쁜 것과 좋은 것은 다릅니다. 판단이 안 서면 칭찬을 빼세요 — 세 문장이
   두 문장이 되는 쪽이 억지 칭찬보다 낫습니다.

strengths 배열도 같은 자격을 따릅니다: NONE/SLIGHT 근거가 있는 부위만.
없으면 **빈 배열**이 정답입니다. 채우려고 지어내지 마세요.

좋은 예 (이 톤을 따르세요):

> "하체는 이미 목표 체형에 가깝게 균형이 잡혀 있어요.
>  차이는 상체, 특히 양쪽 팔 근육에서 가장 큽니다.
>  4주간 팔 위주의 상체 운동에 집중하면 실루엣이 눈에 띄게 달라질 거예요."

> "전반적인 균형이 좋아 출발점이 탄탄합니다.
>  목표와의 거리는 몸통 근육 하나로 좁혀져 있어요.
>  이번 4주는 몸통을 중심에 두고, 나머지는 유지만 해도 충분합니다."

격차가 전반적으로 클 때의 좋은 예 (칭찬 없이):

> "지금은 목표 체형과 거리가 있는 상태예요 — 그만큼 4주간 바뀔 폭도 큽니다.
>  차이는 특히 양쪽 팔에서 커요.
>  팔 운동부터 시작하면 변화가 가장 빨리 눈에 보일 거예요."

나쁜 예 (피하세요):

> "상체 중심 개선이 필요합니다."          ← 진단서 문체. 뭘 하라는 건지 없음
> "어깨·팔·몸통·허벅지 모두 부족합니다."   ← 전부 나열 = 방향 없음. 읽는 사람만 위축
> "꾸준히 운동하면 좋아질 거예요."         ← 어느 몸에나 붙는 빈말
> "왼팔은 균형이 잘 잡혀 있어요."          ← 전 부위 격차가 큰데 하나를 골라 칭찬
>                                           = 억지 칭찬. 신뢰를 깎는 지름길

⚠️ 부위 범례에 없는 이름을 만들지 마세요. 어깨·복근처럼 비교 부위에 없는 단어를
   쓰면 사용자가 화면에서 그 항목을 찾다가 못 찾습니다.
⚠️ 인바디 수치가 주어졌다면 summary 에 한 번은 근거로 반영하세요."""


def _part_line(p: dict[str, Any]) -> str:
    # ⚠️ blocked_reason 이 있어도 gap_level 이 있으면 판단 불가가 아니다.
    #    옷에 가렸지만 인바디로 등급을 매긴 경우가 이에 해당한다. 여기서 뭉뚱그리면
    #    인바디로 건진 부위가 다시 "모름"으로 떨어져 점수에서 빠진다.
    if not p.get("gap_level"):
        reason = p.get("blocked_reason") or "사유 미기재"
        return f"- {p['class_name']}: 판단 불가 — {reason}"

    bits = [f"격차 {p['gap_level']}"]
    if p.get("priority") is not None:
        bits.append(f"우선순위 {p['priority']}")
    if p.get("confidence"):
        bits.append(f"확신도 {p['confidence']}")
    if p.get("blocked_reason"):
        # 시각은 막혔지만 인바디로 등급이 나온 부위 — 근거가 다르다는 걸 알려준다.
        bits.append("시각 확인 불가 · 인바디 기준")

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
    score: int | None = None,
) -> str:
    """F09 사용자 메시지. 이미지 없음.

    Args:
        parts:   판단에 성공한 부위 진단 (gap_level 이 있는 것)
        blocked: 판단 불가로 처리된 부위 (gap_level 이 null)
        failed:  응답 자체가 없거나 형식이 깨진 부위의 class_name
        score:   scoring.compute_similarity() 가 계산한 유사도 점수.
                 LLM 은 이 값을 **받아서 쓸 뿐** 만들지 않는다 (score_source=RULE).
    """
    sections = [
        "# 부위별 진단 결과",
        "",
        "\n".join(_part_line(p) for p in parts) if parts else "(판단된 부위 없음)",
    ]

    if score is not None:
        sections += [
            "",
            f"# 유사도 점수 (계산 완료): {score}점",
            "",
            "※ 부위별 격차 등급을 규칙으로 합산한 값입니다. 점수를 새로 만들지 말고,",
            "  summary 가 이 점수와 같은 인상을 주는지만 확인하세요.",
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
