"""ROUTINE_PATCH 워커용 Function Calling 도구 정의 + 시스템 프롬프트.

설계 원칙:
- 전체 재생성 금지. 변경 오퍼레이션만 반환한다.
- 각 함수 호출 = DB changes 컬럼의 항목 하나.
- 통증 피드백 → flag_contraindication 반드시 동반 호출.

⚠️ 해커톤 MVP는 도구 3개만 쓴다 (옵시디언 [[08 - 해커톤 MVP 범위]]):
     adjust_intensity · replace_exercise · flag_contraindication
   제외됨 — reschedule_day(데모 가치 낮음) · remove_exercise(replace로 대체 가능)
"""

SYSTEM_PROMPT = """너는 개인 트레이너야. 사용자의 피드백을 읽고 운동 루틴을 최소한으로 수정한다.

반드시 지켜야 할 규칙:
- 변경이 필요한 부분만 수정한다. 전체 루틴 재생성 금지.
- 통증·부상 피드백이 있으면 flag_contraindication을 반드시 호출한다.
- 통증 부위의 부하 운동은 replace_exercise로 부담이 적은 대체 운동으로 즉시 바꾼다.
- 모든 reason은 한국어로 작성한다.
- weight_kg는 보수적으로 제안하고, 추정치임을 인지한다.
- 변경이 필요 없으면 아무 함수도 호출하지 않는다."""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "replace_exercise",
            "description": (
                "특정 Day의 운동 하나를 다른 운동으로 교체한다. "
                "통증·장비 없음·선호도 등 운동 자체를 바꿔야 할 때 사용. "
                "통증이면 flag_contraindication도 함께 호출할 것."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day_number": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 28,
                        "description": "수정 대상 Day 번호 (1~28)",
                    },
                    "old_exercise_name": {
                        "type": "string",
                        "description": "교체할 기존 운동 이름 (현재 루틴과 정확히 일치해야 함)",
                    },
                    "new_exercise": {
                        "type": "object",
                        "description": "대체 운동 정보",
                        "properties": {
                            "name": {"type": "string"},
                            "equipment": {"type": "string"},
                            "target_muscle": {"type": "string"},
                            "sets": {"type": "integer", "minimum": 1},
                            "reps": {"type": "integer", "minimum": 1},
                            "weight_kg": {"type": "number", "minimum": 0},
                            "rest_sec": {"type": "integer", "minimum": 0},
                            "note": {"type": "string"},
                        },
                        "required": ["name", "sets"],
                    },
                    "reason": {"type": "string", "description": "교체 이유 (한국어)"},
                },
                "required": ["day_number", "old_exercise_name", "new_exercise", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_intensity",
            "description": (
                "특정 운동의 세트·횟수·중량·휴식시간을 조정한다. "
                "너무 힘들거나 너무 쉬울 때, 점진적 과부하 적용 시 사용. "
                "운동 종류 자체는 유지한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day_number": {"type": "integer", "minimum": 1, "maximum": 28},
                    "exercise_name": {"type": "string", "description": "조정할 운동 이름"},
                    "sets_delta": {
                        "type": "integer",
                        "description": "세트 증감량 (예: +1, -1, 0). 변경 없으면 0",
                    },
                    "reps_delta": {
                        "type": "integer",
                        "description": "횟수 증감량 (예: +2, -2, 0). 변경 없으면 0",
                    },
                    "weight_kg_new": {
                        "type": "number",
                        "description": "변경할 중량(kg). 변경 없으면 null",
                    },
                    "rest_sec_new": {
                        "type": "integer",
                        "description": "변경할 휴식시간(초). 변경 없으면 null",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["day_number", "exercise_name", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_contraindication",
            "description": (
                "통증·부상 부위를 세션 단위 금기(contraindication)로 등록한다. "
                "통증 피드백이 있으면 반드시 호출. "
                "BLOCK이면 해당 부위 부하 운동을 이후 루틴 생성에서도 전면 제외한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body_part": {
                        "type": "string",
                        "description": "금기 부위 (예: 무릎, 어깨, 허리, 손목)",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["WARN", "BLOCK"],
                        "description": "WARN=주의(가벼운 통증), BLOCK=해당 부위 운동 전면 제외",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["body_part", "severity", "reason"],
            },
        },
    },
]
