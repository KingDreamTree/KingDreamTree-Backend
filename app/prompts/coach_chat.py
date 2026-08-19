"""F12 코치 대화 — 페르소나 + 도구 4개.

설계 근거: docs/f12-coach-chat.md

━━ 페르소나의 출처 — GPTCoach (Stanford, CHI 2025) ━━

운동 코칭 챗봇 연구의 표준인 GPTCoach 는 **동기부여 면담(Motivational
Interviewing)** 전략을 프롬프트로 구현했다. 핵심 4기법(OARS)을 그대로 옮긴다:

    Open questions   닫힌 질문("아팠어요?") 대신 열린 질문("어떠셨어요?")
    Affirmation      완료 자체를 먼저 인정 — 초보자의 지속률은 여기서 갈린다
    Reflection       사용자의 말을 되돌려 확인 ("무릎이 계속 신경쓰이셨군요")
    Summary          마지막에 합의 내용을 요약 — 우리는 finalize 카드가 이 역할

지시형("~하세요")이 아니라 **끌어내는** 대화가 행동 변화에 효과적이라는 것이
MI 의 핵심 근거고, GPTCoach 가 이를 LLM 에서 재현할 수 있음을 보였다.

━━ 도구 원칙 — 말은 자유, 손은 묶는다 ━━

대화가 어디로 흘러도 실제 변경은 도구 4개로만 나간다. 도구 인자는 코드가
재검증한다(후보 집합·상한) — LLM 이 규칙 밖 값을 넣어도 적용 단계에서 걸린다.
"""

from app.schemas.enums import ExerciseKind

SYSTEM_PROMPT = """당신은 사용자의 퍼스널 트레이닝 코치입니다. 방금 운동을 끝낸
사용자와 짧은 대화를 나누고, 필요하면 다음 운동부터 루틴을 조정합니다.

# 대화 원칙 (동기부여 면담)

1. **완료를 먼저 인정하세요.** 오늘 운동을 끝냈다는 사실 자체가 성과입니다.
2. **열린 질문으로 시작하세요.** "어떠셨어요?" — "아팠어요?" 같은 유도 질문 금지.
3. **사용자의 말을 되돌려 확인하세요.** "무릎이 계속 신경쓰이셨군요" 식으로.
4. **지시하지 말고 제안하세요.** "바꾸세요" 가 아니라 "바꿔볼까요? ~라서요".
5. 짧게 말하세요. 한 번에 2~4문장. 상담이지 강의가 아닙니다.
6. 읽는 사람은 운동 초보자입니다. 해부학 용어 대신 쉬운 말을 쓰세요.

# 통증 — 최우선 규칙

통증·불편 언급이 나오면:
1. 정도를 확인하세요 (운동을 멈출 정도였는지, 살짝 불편한 정도였는지).
2. flag_contraindication 을 **반드시** 호출하세요 (살짝이면 WARN, 심하면 BLOCK).
3. 그 부위에 부담을 주는 운동은 replace_exercise 로 교체를 제안하세요.
4. "통증이 계속되면 운동을 중단하고 전문가와 상담하세요"를 마무리에 넣으세요.
⚠️ 통증을 참고 계속하라는 취지의 말은 절대 하지 마세요.

# 도구 사용 규칙

- 변경은 사용자와 **합의한 뒤에** 도구를 호출하세요. 일방적으로 바꾸지 마세요.
- replace_exercise 의 새 운동은 반드시 제공된 후보 목록에서 고르세요.
  목록 밖 운동은 시스템이 거부합니다.
- 조정할 것이 없으면 도구 없이 대화만 해도 됩니다. 억지로 바꾸지 마세요.
- adjust_intensity 는 **운동 하나씩만** 바꿉니다. 사용자가 특정 운동이 아니라
  "오늘 운동 전체" 나 "휴식시간 다" 처럼 오늘 Day의 모든 운동을 말하면,
  그 Day에 있는 운동 각각에 대해 adjust_intensity 를 **여러 번 호출**하세요.
  한 운동만 바꾸고 넘어가지 마세요 — 사용자는 "말했는데 안 바뀌었다"고 느낍니다.
- 대화가 마무리되면(보통 2~4턴) **finalize_revision 을 호출**해 변경 요약을
  확정하세요. 변경이 없으면 changes 를 빈 배열로 호출합니다.
- **사용자가 동의해서 변경 도구를 호출했다면, 같은 턴에서 finalize_revision 까지
  연달아 호출해 마무리하세요.** 변경만 하고 카드 없이 끝내지 마세요.
- [마지막 턴] 표시가 오면 이번 응답에서 반드시 finalize_revision 을 호출하세요.
- [마지막 턴] 같은 대괄호 표시는 내부 신호입니다. 답변 문장에 그대로 쓰지 마세요.

# 하지 말 것

- 식단·보충제·의학 상담: "저는 운동 루틴 담당이라 정확히 답하기 어려워요"
  한 줄로 답하고 운동 이야기로 돌아오세요.
- **체중·외모 평가 질문**("살 빼야 하나요?", "저 뚱뚱한가요?", "몇 kg 빼야 돼요?"):
  긍정도 부정도 하지 마세요. 감량 필요 여부를 판단해주는 것은 이 대화의 일이
  아닙니다. "몸무게 목표는 제가 정할 일이 아니에요. 저는 오늘 운동이 몸에
  맞았는지만 같이 볼게요." 로 받고 운동 이야기로 돌아옵니다.
- 사용자가 굶었다거나 식사를 걸렀다고 하면 **강도를 올리지 마세요.**
  그 자리에서 세트를 줄이는 쪽으로 제안하고, 무리하지 말라고 한 줄 덧붙이세요.
- 중량(kg) 추정 금지. "2회 남길 수 있는 무게" 기준만 말하세요.
- 오늘 하지 않은 운동, 루틴에 없는 Day 에 대한 변경 금지."""


#: 대화 중 코치가 쓸 수 있는 도구. 기존 routine_patch 3종 + finalize.
#: day_order 기준(주기 모델) — day_number(1~28)가 아니다.
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "adjust_intensity",
            "description": (
                "특정 운동의 세트·횟수·휴식시간을 조정한다. "
                "'너무 힘들었다/쉬웠다' 피드백에 사용. 사용자와 합의 후 호출."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day_order": {"type": "integer", "minimum": 1, "maximum": 7},
                    "exercise_name": {
                        "type": "string",
                        "description": "조정할 운동 이름(현재 루틴과 일치)",
                    },
                    "sets_delta": {"type": "integer", "description": "세트 증감 (-2~+2). 없으면 0"},
                    "reps_delta": {"type": "integer", "description": "횟수 증감 (-4~+4). 없으면 0"},
                    "rest_sec_new": {
                        "type": "integer",
                        "description": "새 휴식시간(초). 변경 없으면 생략",
                    },
                    "reason": {"type": "string", "description": "사용자에게 보여줄 이유 (한국어)"},
                },
                "required": ["day_order", "exercise_name", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_exercise",
            "description": (
                "운동을 다른 운동으로 교체한다. 통증·장비 없음·불호 피드백에 사용. "
                "new_exercise_ref 는 반드시 제공된 후보 목록의 ref 여야 한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day_order": {"type": "integer", "minimum": 1, "maximum": 7},
                    "old_exercise_name": {"type": "string"},
                    "new_exercise_ref": {
                        "type": "string",
                        "description": "후보 목록의 exercise_ref. 목록 밖은 거부됨",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["day_order", "old_exercise_name", "new_exercise_ref", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_contraindication",
            "description": (
                "통증·부상 부위를 금기로 등록한다. 통증 언급 시 반드시 호출. "
                "이후 모든 루틴 생성·조정에 반영된다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body_part": {"type": "string", "description": "예: 무릎, 어깨, 허리, 손목"},
                    "severity": {
                        "type": "string",
                        "enum": ["WARN", "BLOCK"],
                        "description": "WARN=주의(가벼운 불편), BLOCK=해당 부위 부하 운동 전면 제외",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["body_part", "severity", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_revision",
            "description": (
                "대화를 마무리하고 변경 요약을 확정한다. 이걸 호출해야만 루틴이 "
                "실제로 바뀐다. 변경이 없으면 changes 빈 배열로 호출한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "사용자에게 보여줄 마무리 인사 + 요약 (2~3문장, 한국어)",
                    },
                    "changes": {
                        "type": "array",
                        "description": "이번 대화에서 합의된 변경의 짧은 설명 목록",
                        "items": {
                            "type": "object",
                            "properties": {
                                "what": {"type": "string", "description": "무엇이 바뀌나"},
                                "why": {"type": "string", "description": "왜 바뀌나"},
                            },
                            "required": ["what", "why"],
                        },
                    },
                },
                "required": ["summary", "changes"],
            },
        },
    },
]


def build_context(
    day: dict,
    contraindications: list[dict],
    candidates_by_group: dict[str, list[dict]],
    turn: int,
    max_turns: int,
) -> str:
    """첫 system 메시지 뒤에 붙는 컨텍스트 블록.

    후보 목록을 여기 넣는 이유: replace_exercise 가 "목록에서만" 고르려면
    목록이 대화 안에 있어야 한다. 근육군당 상위 5개면 충분하다 — 전부 주면
    토큰만 늘고, 정렬 상위가 이미 초보 적합 순이다.
    """
    lines = [
        f"# 오늘 완료한 운동 (Day {day.get('day_order')} — {day.get('title')})",
        "",
    ]
    for e in day.get("exercises", []):
        if e.get("exercise_kind") == ExerciseKind.CARDIO or e.get("kind") == ExerciseKind.CARDIO:
            lines.append(f"- {e['name']} (유산소 {e.get('duration_min', '?')}분)")
        else:
            lines.append(
                f"- {e['name']} ({e.get('sets', '?')}세트 × {e.get('reps', '?')}회, "
                f"근육군: {e.get('muscle_group', '?')})"
            )

    if contraindications:
        lines += ["", "# 기존 주의 부위", ""]
        for c in contraindications:
            lines.append(f"- {c.get('body_part')} ({c.get('severity')})")

    lines += ["", "# 교체 후보 (replace_exercise 는 이 안에서만)", ""]
    for group, cands in candidates_by_group.items():
        names = ", ".join(
            f"{c.get('name_ko') or c['name_en']}[{c['exercise_ref']}]" for c in cands[:5]
        )
        lines.append(f"- {group}: {names}")

    lines += ["", f"# 진행: {turn}/{max_turns}턴"]
    if turn >= max_turns:
        lines.append("[마지막 턴] 이번 응답에서 반드시 finalize_revision 을 호출하세요.")
    return "\n".join(lines)
