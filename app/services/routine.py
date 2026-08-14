"""루틴 생성·패치 서비스 (F10, F12).

F10 — generate_routine(): 4주 루틴 초기 생성 (exercise_days_per_week 기반)
F12 — patch_routine(): 피드백 기반 루틴 패치 (Function Calling)
"""

import json
from typing import Any

from app.config import settings
from app.prompts.routine_gen import build_generate_prompt
from app.prompts.routine_patch import SYSTEM_PROMPT, TOOLS
from app.services.routine_templates import (
    SEVEN_DAY_NOTICE,
    expand_to_28_days,
    get_week_template,
)

# ── Mock ─────────────────────────────────────────────────────────────────────

_REST_DAY = {"is_rest": True, "title": "휴식", "estimated_duration_min": None, "exercises": []}

#: LLM이 반환하는 **1주치** 계획 모양 (주 3일 = 전신 A/B/C 템플릿 기준).
#: generate_routine()이 expand_to_28_days()로 4주 복제한다.
_MOCK_GENERATE_RESULT: dict[str, Any] = {
    "goal": "어깨 라인 보완 및 전신 근력 균형 개선",
    "focus_areas": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
    "week": [
        {
            "day_of_week": 1,
            "is_rest": False,
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
                    "note": None,
                },
                {
                    "order_index": 2,
                    "muscle_group": "등",
                    "name": "덤벨 원암 로우",
                    "equipment": "덤벨",
                    "target_muscle": "광배근",
                    "sets": 3,
                    "reps": 10,
                    "weight_kg": 14.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 3,
                    "muscle_group": "대퇴사두",
                    "name": "고블릿 스쿼트",
                    "equipment": "덤벨",
                    "target_muscle": "대퇴사두근",
                    "sets": 3,
                    "reps": 12,
                    "weight_kg": 16.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 4,
                    "muscle_group": "코어",
                    "name": "플랭크",
                    "equipment": None,
                    "target_muscle": "복직근",
                    "sets": 3,
                    "reps": None,
                    "weight_kg": None,
                    "rest_sec": 60,
                    "note": "40초 유지",
                },
            ],
        },
        dict(_REST_DAY, day_of_week=2),
        {
            "day_of_week": 3,
            "is_rest": False,
            "title": "전신 B",
            "estimated_duration_min": 55,
            "exercises": [
                {
                    "order_index": 1,
                    "muscle_group": "어깨",
                    "name": "덤벨 숄더프레스",
                    "equipment": "덤벨",
                    "target_muscle": "삼각근",
                    "sets": 3,
                    "reps": 12,
                    "weight_kg": 10.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 2,
                    "muscle_group": "등",
                    "name": "랫 풀다운",
                    "equipment": "케이블 머신",
                    "target_muscle": "광배근",
                    "sets": 3,
                    "reps": 10,
                    "weight_kg": 30.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 3,
                    "muscle_group": "햄스트링·둔근",
                    "name": "루마니안 데드리프트",
                    "equipment": "덤벨",
                    "target_muscle": "햄스트링",
                    "sets": 3,
                    "reps": 12,
                    "weight_kg": 18.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 4,
                    "muscle_group": "코어",
                    "name": "데드버그",
                    "equipment": None,
                    "target_muscle": "복횡근",
                    "sets": 3,
                    "reps": 15,
                    "weight_kg": None,
                    "rest_sec": 60,
                    "note": None,
                },
            ],
        },
        dict(_REST_DAY, day_of_week=4),
        {
            "day_of_week": 5,
            "is_rest": False,
            "title": "전신 C",
            "estimated_duration_min": 55,
            "exercises": [
                {
                    "order_index": 1,
                    "muscle_group": "가슴",
                    "name": "머신 체스트 프레스",
                    "equipment": "머신",
                    "target_muscle": "대흉근",
                    "sets": 3,
                    "reps": 10,
                    "weight_kg": 25.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 2,
                    "muscle_group": "등",
                    "name": "시티드 케이블 로우",
                    "equipment": "케이블 머신",
                    "target_muscle": "승모근·능형근",
                    "sets": 3,
                    "reps": 12,
                    "weight_kg": 28.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 3,
                    "muscle_group": "대퇴사두",
                    "name": "레그프레스",
                    "equipment": "머신",
                    "target_muscle": "대퇴사두근",
                    "sets": 3,
                    "reps": 10,
                    "weight_kg": 60.0,
                    "rest_sec": 90,
                    "note": None,
                },
                {
                    "order_index": 4,
                    "muscle_group": "종아리",
                    "name": "스탠딩 카프 레이즈",
                    "equipment": None,
                    "target_muscle": "비복근",
                    "sets": 3,
                    "reps": 15,
                    "weight_kg": None,
                    "rest_sec": 60,
                    "note": None,
                },
            ],
        },
        dict(_REST_DAY, day_of_week=6),
        dict(_REST_DAY, day_of_week=7),
    ],
    "raw_response": None,
}

_MOCK_PATCH_RESULT: dict[str, Any] = {
    "interpretation": "무릎 통증으로 바벨 스쿼트를 레그프레스로 교체하고, 무릎을 주의 금기로 등록했습니다.",
    "changes": [
        {
            "function": "replace_exercise",
            "args": {
                "day_number": 3,
                "old_exercise_name": "바벨 스쿼트",
                "new_exercise": {
                    "name": "레그프레스",
                    "equipment": "레그프레스 머신",
                    "target_muscle": "대퇴사두근",
                    "sets": 3,
                    "reps": 12,
                    "rest_sec": 90,
                },
                "reason": "무릎 통증으로 고관절 굴곡 부하를 최소화하기 위해 교체",
            },
        },
        {
            "function": "flag_contraindication",
            "args": {
                "body_part": "무릎",
                "severity": "WARN",
                "reason": "사용자 직접 통증 보고 — 이후 루틴에서 무릎 부하 운동 주의",
            },
        },
    ],
    "contraindications_added": [{"body_part": "무릎", "severity": "WARN"}],
    "raw_response": None,
}


# ── Public API ────────────────────────────────────────────────────────────────


async def generate_routine(
    exercise_days_per_week: int,
    overall_diagnosis: dict,
    inbody: dict | None,
    contraindications: list[dict],
) -> dict[str, Any]:
    """4주 루틴을 생성한다 (F10 — ROUTINE_GEN 워커).

    구조: 코드가 분할 골격을 정하고, LLM은 1주치 운동만 채우고, 코드가 4주로 복제한다.
    → LLM 호출 1회로 28행이 보장된다 (옵시디언 [[08 - 해커톤 MVP 범위]]).

    Args:
        exercise_days_per_week: 사용자가 선택한 주당 운동 일수 (1~7)
        overall_diagnosis:      VLM_OVERALL 결과 (F09)
        inbody:                 인바디 데이터 (없으면 None — 없어도 생성됨)
        contraindications:      analysis_session.contraindications (누적 금기)

    Returns:
        {
          "goal", "focus_areas",
          "days": list[dict],   # 28개. day_routine + day_routine_exercise로 분해
          "notice": str | None, # 7일 선택 시 능동회복 변환 안내
          "raw_response": dict,
        }
    """
    week_template = get_week_template(exercise_days_per_week)
    notice = SEVEN_DAY_NOTICE if exercise_days_per_week == 7 else None

    if settings.use_mock:
        result = dict(_MOCK_GENERATE_RESULT)
        result["days"] = expand_to_28_days(result.pop("week"))
        result["notice"] = notice
        return result

    from openai import AsyncOpenAI  # 지연 import — services/ocr.py 상단 주석 참고

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    system, user = build_generate_prompt(
        week_template=week_template,
        overall_diagnosis=overall_diagnosis,
        inbody=inbody,
        contraindications=contraindications,
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    raw = response.model_dump()
    parsed = json.loads(response.choices[0].message.content)

    week: list[dict] = parsed.get("week", [])
    if len(week) != 7:
        raise ValueError(f"LLM이 {len(week)}일짜리 주간 계획을 반환했습니다. 7일이어야 합니다.")

    return {
        "goal": parsed.get("goal", ""),
        "focus_areas": parsed.get("focus_areas", []),
        "days": expand_to_28_days(week),  # 코드가 4주 복제 + 주차 배율
        "notice": notice,
        "raw_response": raw,
    }


async def patch_routine(
    current_routine: dict[str, Any],
    feedback_text: str,
    contraindications: list[dict],
) -> dict[str, Any]:
    """피드백을 Function Calling으로 해석해 루틴 변경 오퍼레이션 목록을 반환한다.

    Args:
        current_routine: 현재 활성 루틴 전체 (day_routine + exercises 포함)
        feedback_text: workout_log.feedback_text
        contraindications: analysis_session.contraindications (기존 누적 금기)

    Returns:
        {
          "interpretation": str,           # LLM 해석 요약 (routine_revision.interpretation)
          "changes": list[dict],           # 변경 오퍼레이션 목록 (routine_revision.changes)
          "contraindications_added": list, # 이번 회차 증분 (routine_revision.contraindications_added)
          "raw_response": dict | None,     # 원본 API 응답 (routine_revision.raw_response)
        }
    """
    if settings.use_mock:
        return dict(_MOCK_PATCH_RESULT)

    from openai import AsyncOpenAI  # 지연 import — services/ocr.py 상단 주석 참고

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    existing = (
        f"\n기존 누적 금기: {json.dumps(contraindications, ensure_ascii=False)}"
        if contraindications
        else ""
    )

    user_message = (
        f"현재 루틴:\n{json.dumps(current_routine, ensure_ascii=False, indent=2)}\n\n"
        f"사용자 피드백: {feedback_text}"
        f"{existing}"
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        tools=TOOLS,
        tool_choice="auto",
    )

    raw = response.model_dump()
    message = response.choices[0].message

    changes: list[dict] = []
    contraindications_added: list[dict] = []

    if message.tool_calls:
        for tc in message.tool_calls:
            args = json.loads(tc.function.arguments)
            changes.append({"function": tc.function.name, "args": args})
            if tc.function.name == "flag_contraindication":
                contraindications_added.append(
                    {"body_part": args["body_part"], "severity": args["severity"]}
                )

    # message.content: 변경 없을 때 LLM이 텍스트로 이유를 설명하기도 함
    interpretation = message.content or _summarize_changes(changes)

    return {
        "interpretation": interpretation,
        "changes": changes,
        "contraindications_added": contraindications_added,
        "raw_response": raw,
    }


# ── Internal ──────────────────────────────────────────────────────────────────

_CHANGE_LABELS = {
    "replace_exercise": "운동 교체",
    "adjust_intensity": "강도 조정",
    "reschedule_day": "일정 변경",
    "remove_exercise": "운동 제거",
    "flag_contraindication": "금기 등록",
}


def _summarize_changes(changes: list[dict]) -> str:
    if not changes:
        return "변경 사항 없음"
    labels = [_CHANGE_LABELS.get(c["function"], c["function"]) for c in changes]
    return ", ".join(labels) + f" ({len(changes)}건)"
