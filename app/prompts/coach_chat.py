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

# ⚠️ 프롬프트 본문은 DB(prompt_version)에 있다 (2026-09-11, #164) — 'coach.system'.
#    보기: python scripts/prompt_version.py show coach.system
#    고치기: python scripts/prompt_version.py new coach.system <파일> --note "…" (배포 없음)


#: 대화 중 코치가 쓸 수 있는 도구. 기존 routine_patch 3종 + finalize.
#: day_order 기준(주기 모델) — day_number(1~28)가 아니다.
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "adjust_intensity",
            "description": (
                "특정 운동의 세트·횟수·휴식시간을 조정한다. "
                "'너무 힘들었다/쉬웠다' 피드백에 사용. 사용자와 합의 후 호출. "
                "같은 운동에 여러 번 호출하면 마지막 것만 적용된다 — 누적이 아니라 최종값을 보낼 것. "
                "취소는 sets_delta=0, reps_delta=0 으로 다시 호출."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day_order": {"type": "integer", "minimum": 1, "maximum": 7},
                    "exercise_name": {
                        "type": "string",
                        "description": (
                            "조정할 운동 이름. 사용자가 말한 대로 적어도 된다 — "
                            "시스템이 오늘 목록과 대조해 특정한다"
                        ),
                    },
                    "sets_delta": {"type": "integer", "description": "세트 증감 (-2~+2). 없으면 0"},
                    "reps_delta": {"type": "integer", "description": "횟수 증감 (-4~+4). 없으면 0"},
                    "rest_sec_new": {
                        "type": "integer",
                        "description": "새 휴식시간(초). 변경 없으면 생략",
                    },
                    "load_scale": {
                        "type": "number",
                        "description": (
                            "시작 중량 안내 배율. 무거웠다면 0.8, 가벼웠다면 1.2 처럼. "
                            "무게 얘기가 없었으면 생략한다. ⚠️ kg 을 직접 정하지 말 것 — "
                            "kg 은 체중 기준으로 코드가 계산한다"
                        ),
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
                    "old_exercise_name": {
                        "type": "string",
                        "description": "바꿀 운동 이름. 사용자가 말한 대로 적어도 된다",
                    },
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
                        "description": (
                            "이번 대화에서 실제로 도구를 호출한 변경의 짧은 설명. "
                            "시스템이 실제 실행 내역으로 다시 만든다"
                        ),
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
            ref = f"[{e['exercise_ref']}]" if e.get("exercise_ref") else ""
            lines.append(
                f"- {e['name']}{ref} ({e.get('sets', '?')}세트 × {e.get('reps', '?')}회, "
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
