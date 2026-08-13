"""루틴 분할 템플릿 — **코드 계층(L1 골격)**. LLM이 건드리지 않는다.

설계 근거: 옵시디언 [[07 - 의류 가림 문제와 루틴 분리 설계]] · [[08 - 해커톤 MVP 범위]]

    [코드]  days_per_week → 분할 템플릿 → 1주 슬롯 확정 (근육군·세트수)
      ↓
    [LLM]   각 슬롯에 구체적 운동만 선택 (호출 1회)
      ↓
    [코드]  4주 복제 + 주차별 볼륨 배율 → 28행

LLM에게 분할을 맡기면 초보자에게 브로스플릿을 주거나, 휴식일을 빠뜨리거나,
같은 근육군을 연속 배치(회복 무시)하는 사고가 난다. 골격을 코드로 고정하면
LLM 출력이 어떻든 28행과 회복 간격이 보장된다.

⚠️ 진단은 부위의 **포함/제외**가 아니라 **볼륨 가중치**로만 작용한다.
   진단이 없는 부위(의류 가림 등)도 기본 볼륨을 받으므로 루틴이 깨지지 않는다.
"""

from typing import Any, TypedDict


class Slot(TypedDict):
    """LLM이 운동명을 채울 빈 슬롯. 근육군·세트·횟수는 코드가 정한다."""

    muscle_group: str
    sets: int
    reps: int
    rest_sec: int


class DayTemplate(TypedDict):
    day_of_week: int  # 1=월 … 7=일
    is_rest: bool
    title: str
    slots: list[Slot]


def _s(muscle_group: str, sets: int, reps: int, rest_sec: int = 90) -> Slot:
    return {"muscle_group": muscle_group, "sets": sets, "reps": reps, "rest_sec": rest_sec}


def _rest(day_of_week: int) -> DayTemplate:
    return {"day_of_week": day_of_week, "is_rest": True, "title": "휴식", "slots": []}


# ── 세션 구성 (근육군 슬롯) ──────────────────────────────────────────────────
# 초보자 기준: 복합운동 위주, 부위당 주 2회, 세션당 4~5종목 (60분 이내)

_FULL_A = [_s("가슴", 3, 10), _s("등", 3, 10), _s("대퇴사두", 3, 12), _s("코어", 3, 15, 60)]
_FULL_B = [_s("어깨", 3, 12), _s("등", 3, 10), _s("햄스트링·둔근", 3, 12), _s("코어", 3, 15, 60)]
_FULL_C = [_s("가슴", 3, 10), _s("등", 3, 12), _s("대퇴사두", 3, 10), _s("종아리", 3, 15, 60)]

_UPPER = [_s("가슴", 4, 10), _s("등", 4, 10), _s("어깨", 3, 12), _s("팔", 3, 12, 60)]
_LOWER = [
    _s("대퇴사두", 4, 10),
    _s("햄스트링·둔근", 4, 10),
    _s("종아리", 3, 15, 60),
    _s("코어", 3, 15, 60),
]

_PUSH = [_s("가슴", 4, 10), _s("어깨", 3, 12), _s("삼두", 3, 12, 60)]
_PULL = [_s("등", 4, 10), _s("이두", 3, 12, 60), _s("후면 어깨", 3, 15, 60)]
_LEGS = [_s("대퇴사두", 4, 10), _s("햄스트링·둔근", 4, 10), _s("종아리", 3, 15, 60)]

# 약점 보완일 — 진단 결과(priority_parts)를 LLM이 여기 반영한다
_WEAK_POINT = [_s("약점 보완", 3, 12), _s("약점 보완", 3, 12), _s("코어", 3, 15, 60)]

# 능동 회복일 — 근력 아님. 7일 선택 시 자동 변환
_ACTIVE_RECOVERY = [_s("능동 회복(가벼운 유산소·스트레칭)", 1, 20, 0)]


# ── 일수별 분할 템플릿 ───────────────────────────────────────────────────────
# ⚠️ 7일은 그대로 7일 근력을 주지 않는다. 회복이 적응의 조건이므로
#    6일 + 능동회복으로 변환하고 UI에서 이유를 안내한다.

WEEK_TEMPLATES: dict[int, list[DayTemplate]] = {
    1: [
        _rest(1),
        _rest(2),
        {"day_of_week": 3, "is_rest": False, "title": "전신", "slots": _FULL_A},
        _rest(4),
        _rest(5),
        _rest(6),
        _rest(7),
    ],
    2: [
        {"day_of_week": 1, "is_rest": False, "title": "전신 A", "slots": _FULL_A},
        _rest(2),
        _rest(3),
        {"day_of_week": 4, "is_rest": False, "title": "전신 B", "slots": _FULL_B},
        _rest(5),
        _rest(6),
        _rest(7),
    ],
    3: [
        {"day_of_week": 1, "is_rest": False, "title": "전신 A", "slots": _FULL_A},
        _rest(2),
        {"day_of_week": 3, "is_rest": False, "title": "전신 B", "slots": _FULL_B},
        _rest(4),
        {"day_of_week": 5, "is_rest": False, "title": "전신 C", "slots": _FULL_C},
        _rest(6),
        _rest(7),
    ],
    4: [
        {"day_of_week": 1, "is_rest": False, "title": "상체", "slots": _UPPER},
        {"day_of_week": 2, "is_rest": False, "title": "하체", "slots": _LOWER},
        _rest(3),
        {"day_of_week": 4, "is_rest": False, "title": "상체", "slots": _UPPER},
        {"day_of_week": 5, "is_rest": False, "title": "하체", "slots": _LOWER},
        _rest(6),
        _rest(7),
    ],
    5: [
        {"day_of_week": 1, "is_rest": False, "title": "상체", "slots": _UPPER},
        {"day_of_week": 2, "is_rest": False, "title": "하체", "slots": _LOWER},
        _rest(3),
        {"day_of_week": 4, "is_rest": False, "title": "상체", "slots": _UPPER},
        {"day_of_week": 5, "is_rest": False, "title": "하체", "slots": _LOWER},
        {"day_of_week": 6, "is_rest": False, "title": "약점 보완", "slots": _WEAK_POINT},
        _rest(7),
    ],
    6: [
        {"day_of_week": 1, "is_rest": False, "title": "Push", "slots": _PUSH},
        {"day_of_week": 2, "is_rest": False, "title": "Pull", "slots": _PULL},
        {"day_of_week": 3, "is_rest": False, "title": "Legs", "slots": _LEGS},
        {"day_of_week": 4, "is_rest": False, "title": "Push", "slots": _PUSH},
        {"day_of_week": 5, "is_rest": False, "title": "Pull", "slots": _PULL},
        {"day_of_week": 6, "is_rest": False, "title": "Legs", "slots": _LEGS},
        _rest(7),
    ],
    7: [
        {"day_of_week": 1, "is_rest": False, "title": "Push", "slots": _PUSH},
        {"day_of_week": 2, "is_rest": False, "title": "Pull", "slots": _PULL},
        {"day_of_week": 3, "is_rest": False, "title": "Legs", "slots": _LEGS},
        {"day_of_week": 4, "is_rest": False, "title": "Push", "slots": _PUSH},
        {"day_of_week": 5, "is_rest": False, "title": "Pull", "slots": _PULL},
        {"day_of_week": 6, "is_rest": False, "title": "Legs", "slots": _LEGS},
        {"day_of_week": 7, "is_rest": False, "title": "능동 회복", "slots": _ACTIVE_RECOVERY},
    ],
}

#: 7일을 고른 사용자에게 UI로 안내할 문구 (자동 변환 사실을 숨기지 않는다)
SEVEN_DAY_NOTICE = (
    "주 7일 연속 근력 운동은 회복이 부족해 권장되지 않습니다. "
    "6일 근력 + 1일 능동 회복(가벼운 유산소·스트레칭)으로 구성했습니다."
)


# ── 주차별 진행 (progressive overload) ───────────────────────────────────────


class WeekProgression(TypedDict):
    rpe: int
    volume_multiplier: float
    note: str


WEEK_PROGRESSION: dict[int, WeekProgression] = {
    1: {"rpe": 6, "volume_multiplier": 0.8, "note": "적응 · 폼 학습"},
    2: {"rpe": 7, "volume_multiplier": 1.0, "note": "볼륨 증가"},
    3: {"rpe": 8, "volume_multiplier": 1.1, "note": "최대 볼륨"},
    4: {"rpe": 7, "volume_multiplier": 1.0, "note": "유지 + 재측정"},
}


# ── Public helpers ───────────────────────────────────────────────────────────


def get_week_template(days_per_week: int) -> list[DayTemplate]:
    """주당 운동 일수에 맞는 1주 템플릿(7일)을 반환한다."""
    if days_per_week not in WEEK_TEMPLATES:
        raise ValueError(f"days_per_week는 1~7이어야 합니다: {days_per_week}")
    return WEEK_TEMPLATES[days_per_week]


def expand_to_28_days(week_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM이 채운 1주 계획을 4주(28일)로 복제하고 주차별 볼륨 배율을 적용한다.

    Args:
        week_plan: 7개 항목. 각 항목은 {is_rest, title, exercises:[{name, sets, ...}]}

    Returns:
        28개 항목. day_number 1~28 부여, 세트수에 주차 배율 적용.

    ⚠️ week_number는 DB 생성 컬럼이므로 여기서 만들지 않는다 (INSERT에 넣으면 실패).
    """
    if len(week_plan) != 7:
        raise ValueError(f"week_plan은 7일이어야 합니다: {len(week_plan)}일")

    days: list[dict[str, Any]] = []
    for week in range(1, 5):
        progression = WEEK_PROGRESSION[week]
        multiplier = progression["volume_multiplier"]

        for offset, day in enumerate(week_plan):
            day_number = (week - 1) * 7 + offset + 1
            exercises = [
                {
                    **exercise,
                    "sets": max(1, round(exercise["sets"] * multiplier)),
                }
                for exercise in day.get("exercises", [])
            ]
            days.append(
                {
                    "day_number": day_number,
                    "is_rest": day["is_rest"],
                    "title": day.get("title"),
                    "estimated_duration_min": day.get("estimated_duration_min"),
                    "rpe": progression["rpe"],
                    "week_note": progression["note"],
                    "exercises": exercises,
                }
            )
    return days
