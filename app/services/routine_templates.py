"""루틴 분할 템플릿 — L1 골격 (코드 계층). LLM 이 건드리지 않는다.

━━ 확정 모델 (2026-08-14 PM, docs/routine-logic-decision.md §Q3·Q8) ━━

    루틴 단위 = **주기당 N일** (N = 사용자가 고른 1~7). 4주기 반복.
    요일 아님. 휴식일 행 없음. day_order 는 주기 내 순서다.

━━ 수치의 출처 (H4 — 전부 명기) ━━

    분할:      ACSM 2009 Position Stand (Progression Models in Resistance
               Training) — 초보 전신 2~3일, 분할은 4일+부터. PMID 19204579
    세트·횟수: 같은 문서 — 초보 1~3세트 × 8~12회. 코어·종아리 15회는
               근지구력 권고 범위(15~25회)
    빈도:      Schoenfeld 2019 — 총볼륨 동일 시 빈도 무관. PMID 30558493
               → 일수는 사용자 선택, 볼륨 배분은 여기가 책임진다
    유산소:    ACSM Donnelly 2009 — 감량에 주 150분+ 권고.
               ⚠️ **루틴이 150분을 보장하지 않는다** (2026-08-14 정정).
               루틴이 주는 것은 근력일당 15~20분뿐이다 (주 3일이면 45분).
               나머지는 일상 활동·걷기 안내(CUT_NOTICE)로 채우도록 분리했고,
               "주간 활동량을 점진적으로 늘린다"가 실제 처방이다.
    진행:      주차 배율 폐기 — RIR 자가조절(Zourdos 2016)이 점진 과부하를
               내장한다. "2회 남기고 멈추는 무게"는 능력을 따라 저절로 오른다

━━ 순서 규칙 ━━

    day_order 는 요일이 아니라서 사용자가 이틀 연속으로 할 수 있다.
    그래서 **인접 Day 가 같은 근육군을 반복하지 않게** 배치한다 (상→하→상).
    전신×3(1~3일)은 구성(A/B/C)을 달리해 주동근이 겹치지 않게 한다.

⚠️ 진단은 부위의 포함/제외가 아니라 **볼륨 가중치**로만 작용한다 (D10 확정).
   진단이 없거나 실패한 부위도 아래 기본 볼륨을 그대로 받는다 — 기본 볼륨의
   근거는 진단이 아니라 가이드라인이므로 개인화 문구만 붙이지 않으면 된다.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Slot(TypedDict, total=False):
    """LLM 이 운동(exercise_ref)을 채울 빈 칸. 세트·횟수·휴식은 코드가 정한다."""

    muscle_group: str  # exercise_catalog.SLOT_BODY_PARTS 의 키
    sets: int
    reps: int
    rest_sec: int
    kind: str  # STRENGTH | CARDIO
    duration_min: int  # CARDIO 전용


class DayPlan(TypedDict):
    day_order: int  # 주기 내 순서 (1..N). 요일 아님
    title: str
    slots: list[Slot]


#: 7일 선택 시 안내 문구 — 7일 근력을 주지 않는 이유.
SEVEN_DAY_NOTICE = (
    "매일 운동을 선택하셨네요! 근육은 쉬는 날 자랍니다. "
    "6일은 근력 운동, 하루는 가볍게 걷기·스트레칭으로 구성했어요."
)

#: CUT 모드 안내 — 왜 전신+유산소인지 사용자에게 설명.
CUT_NOTICE = (
    "체지방률 기준으로 감량을 함께 하면 효과가 좋아요. "
    "근육을 지키기 위해 근력 운동은 유지하고, 매 운동 끝에 유산소를 더했어요. "
    "쉬는 날에도 30분 정도 걷기를 추천해요."
)


def _s(muscle_group: str, sets: int, reps: int, rest_sec: int = 90) -> Slot:
    return {
        "muscle_group": muscle_group,
        "sets": sets,
        "reps": reps,
        "rest_sec": rest_sec,
        "kind": "STRENGTH",
    }


def _cardio(duration_min: int = 15) -> Slot:
    """CUT 모드 유산소 마무리 슬롯 (D3 확정 — 문구가 아니라 운동 항목).

    근력 후 15~20분 중강도. ⚠️ 이것만으로 주 150분이 되지 않는다
    (주 3일이면 45분). 나머지는 CUT_NOTICE 의 일상 걷기 안내가 담당한다.
    """
    return {"muscle_group": "유산소", "kind": "CARDIO", "duration_min": duration_min, "sets": 1}


# ── 세션 구성 ────────────────────────────────────────────────────────────────
# 초보 기준: 복합운동 위주, 세션 4~5종목(60분 이내), 부위당 주 2회 언저리.
# A/B/C 는 주동근이 겹치지 않게 — 연속 수행 대비 (모듈 주석 §순서 규칙).

_FULL_A = [_s("가슴", 3, 10), _s("등", 3, 10), _s("대퇴사두", 3, 12), _s("코어", 3, 15, 60)]
_FULL_B = [_s("어깨", 3, 12), _s("등", 3, 10), _s("햄스트링·둔근", 3, 12), _s("코어", 3, 15, 60)]
_FULL_C = [_s("가슴", 3, 10), _s("이두", 3, 12, 60), _s("대퇴사두", 3, 10), _s("종아리", 3, 15, 60)]

_UPPER_1 = [_s("가슴", 4, 10), _s("등", 4, 10), _s("어깨", 3, 12), _s("삼두", 3, 12, 60)]
_UPPER_2 = [_s("가슴", 3, 10), _s("등", 4, 10), _s("후면 어깨", 3, 15, 60), _s("이두", 3, 12, 60)]
#: 하체도 A/B 로 나눈다 (2026-08-14). 상체만 A/B 이고 하체는 같은 날이 두 번
#: 반복되던 비대칭을 해소한다. 주동근을 다르게 두면 같은 주에 두 번 해도
#: 회복이 겹치지 않고, 운동 선택 후보도 자연히 갈린다.
_LOWER_1 = [  # 대퇴사두 dominant
    _s("대퇴사두", 4, 10),
    _s("햄스트링·둔근", 3, 12),
    _s("종아리", 3, 15, 60),
    _s("코어", 3, 15, 60),
]
_LOWER_2 = [  # 햄스트링·둔근 dominant
    _s("햄스트링·둔근", 4, 10),
    _s("대퇴사두", 3, 12),
    _s("종아리", 3, 15, 60),
    _s("코어", 3, 15, 60),
]

_PUSH = [_s("가슴", 4, 10), _s("어깨", 3, 12), _s("삼두", 3, 12, 60)]
_PULL = [_s("등", 4, 10), _s("이두", 3, 12, 60), _s("후면 어깨", 3, 15, 60)]
_LEGS = [_s("대퇴사두", 4, 10), _s("햄스트링·둔근", 4, 10), _s("종아리", 3, 15, 60)]

#: 능동 회복일 — 근력 아님. 7일 선택 시 자동 포함.
_ACTIVE_RECOVERY = [_cardio(30)]


def _day(order: int, title: str, slots: list[Slot]) -> DayPlan:
    return {"day_order": order, "title": title, "slots": [dict(s) for s in slots]}


#: BALANCE 골격 — N일 → Day 1..N. 휴식일 행 없음.
_BALANCE: dict[int, list[tuple[str, list[Slot]]]] = {
    1: [("전신", _FULL_A)],
    2: [("전신 A", _FULL_A), ("전신 B", _FULL_B)],
    3: [("전신 A", _FULL_A), ("전신 B", _FULL_B), ("전신 C", _FULL_C)],
    4: [("상체 A", _UPPER_1), ("하체 A", _LOWER_1), ("상체 B", _UPPER_2), ("하체 B", _LOWER_2)],
    #: ⚠️ 5일차는 **전신 + 개인화** 다 (2026-08-14 정정).
    #:    이전에는 슬롯이 빈 "약점 보완" Day 를 두고 priority_parts 로만 채웠는데,
    #:    이는 "진단은 볼륨 가중치일 뿐 부위의 포함/제외가 아니다"(D10)라는
    #:    원칙과 정면으로 충돌했다 — 진단이 Day 하나의 구성 전체를 결정했다.
    #:    이제 전신 골격(_FULL_C)을 깔고 그 위에 L2 가중이 얹힌다. 진단이 없어도
    #:    Day 가 성립하고, 있으면 그 부위 슬롯의 세트가 올라간다.
    5: [
        ("상체 A", _UPPER_1),
        ("하체 A", _LOWER_1),
        ("상체 B", _UPPER_2),
        ("하체 B", _LOWER_2),
        ("전신 마무리", _FULL_C),
    ],
    6: [
        ("밀기", _PUSH),
        ("당기기", _PULL),
        ("하체", _LEGS),
        ("밀기", _PUSH),
        ("당기기", _PULL),
        ("하체", _LEGS),
    ],
    7: [
        ("밀기", _PUSH),
        ("당기기", _PULL),
        ("하체", _LEGS),
        ("밀기", _PUSH),
        ("당기기", _PULL),
        ("하체", _LEGS),
        ("가벼운 회복", _ACTIVE_RECOVERY),
    ],
}


def get_template(days_per_week: int, mode: str = "BALANCE") -> list[DayPlan]:
    """N일 템플릿. mode="CUT" 이면 근력일마다 유산소 마무리 슬롯을 더한다.

    CUT 에서 근력을 빼지 않는 이유: 감량기 제지방 보존 (llm-strategy §Q2).
    부위 가중(L2)은 CUT 에서도 그대로 동작한다.
    """
    if not 1 <= days_per_week <= 7:
        raise ValueError(f"days_per_week 는 1~7 이어야 합니다: {days_per_week}")

    days: list[DayPlan] = []
    for i, (title, slots) in enumerate(_BALANCE[days_per_week], start=1):
        day = _day(i, title, slots)
        is_strength_day = any(s.get("kind") == "STRENGTH" for s in day["slots"]) or not day["slots"]
        if mode == "CUT" and is_strength_day:
            # 1~2일은 세션이 그날의 전부라 유산소를 20분으로 (주간 총량 보전)
            day["slots"].append(_cardio(20 if days_per_week <= 2 else 15))
        days.append(day)
    return days


# ── L2 — 약점 부위 볼륨 가중 (D10 확정: 진단은 가중치, 포함/제외 아님) ─────────

#: **근육군 하나**가 주간에 받을 수 있는 추가 세트 상한.
#:
#: ⚠️ 2026-08-14 정정: 이전에는 *부위(part)* 별로 4세트씩 독립 예산이었다.
#:    여러 부위가 같은 근육군에 매핑되면 중복 가산된다 — 실측으로 확인했다:
#:      Left_Upper_Arm + Right_Upper_Arm 이 둘 다 우선 부위 → 둘 다 ("이두","삼두")
#:      → 이두 3 → 5, 삼두 3 → 5 로 각각 +2 씩 이중 가산
#:    진단 feature 는 좌우로 갈리지만 **운동은 좌우를 함께 한다.** 그래서
#:    예산은 진단 단위가 아니라 **실제 볼륨이 쌓이는 단위(근육군)** 로 잡아야 한다.
WEEKLY_BOOST_CAP_PER_GROUP = 4

#: 근육군 주간 총 세트 운영 상한.
#: ⚠️ 생리학적 절대 상한이 아니다. Schoenfeld 2017 (PMID 27433992) 은 ~10세트/주
#:    까지 뚜렷한 용량-반응을 보이고 그 이상은 근거가 얇아진다고 본다. 20 은
#:    **MVP 에서 쓰는 보수적 운영 상한** — 초보에게 이 이상을 처방할 근거가 없다.
WEEKLY_GROUP_SET_CAP = 20

#: 슬롯 하나의 세트 상한 — 운동 분류에 따라 다르다.
#:
#: 복합 다관절(스쿼트·데드리프트·프레스류)은 전신 피로가 크게 쌓여 한 종목
#: 5세트가 초보 세션을 무너뜨린다. 고립(컬·레이즈류)은 국소 피로라 여유가 있다.
#: 슬롯의 근육군으로 판별한다 — 어느 운동이 올지는 LLM 선택 뒤에 정해지지만,
#: 근육군은 코드가 이미 확정했으므로 여기서 결정할 수 있다.
SLOT_SETS_CAP_COMPOUND = 4
SLOT_SETS_CAP_ISOLATION = 5

#: 복합 다관절이 주로 배치되는 근육군.
_COMPOUND_GROUPS = frozenset({"가슴", "등", "대퇴사두", "햄스트링·둔근", "어깨"})


def slot_sets_cap(muscle_group: str) -> int:
    """그 근육군 슬롯이 가질 수 있는 최대 세트 수."""
    return SLOT_SETS_CAP_COMPOUND if muscle_group in _COMPOUND_GROUPS else SLOT_SETS_CAP_ISOLATION


def apply_weakness_boost(
    days: list[DayPlan],
    priority_parts: list[str],
    part_to_slots: dict[str, tuple],
    max_parts: int = 3,
) -> dict[str, int]:
    """우선 부위의 슬롯에 +1세트씩 가산한다. **예산은 근육군 단위로 공유한다.**

    in-place 수정. 반환값은 {부위: 실제 가산 세트 수} — 프론트 문구
    ("왼팔이 부족해서 세트를 더 넣었어요")의 근거로 쓴다.

    3중 상한이 걸린다:
        1. 근육군당 가산   WEEKLY_BOOST_CAP_PER_GROUP  (부위가 아니라 근육군 단위)
        2. 근육군 주간 총량 WEEKLY_GROUP_SET_CAP        (기본 볼륨 + 가산 합계)
        3. 슬롯당 세트     slot_sets_cap()              (복합 4 / 고립 5)

    ⚠️ 진단 실패 부위는 이 함수에 아예 들어오지 않는다. 그 부위는 기본
       볼륨을 그대로 받는다 (D10 — 기본 볼륨의 근거는 가이드라인이다).
    """
    added: dict[str, int] = {}

    # 근육군별로 공유하는 가산 예산. 부위가 몇 개든 근육군 하나당 이 값이 상한이다.
    group_budget: dict[str, int] = {}
    weekly = weekly_sets_by_group(days)

    for part in priority_parts[:max_parts]:
        groups = set(part_to_slots.get(part, ()))
        if not groups:
            continue
        gained = 0
        for day in days:
            for slot in day["slots"]:
                if slot.get("kind") != "STRENGTH":
                    continue
                group = slot["muscle_group"]
                if group not in groups:
                    continue

                budget = group_budget.setdefault(group, WEEKLY_BOOST_CAP_PER_GROUP)
                if budget <= 0:
                    continue
                if weekly.get(group, 0) >= WEEKLY_GROUP_SET_CAP:
                    continue
                if slot["sets"] >= slot_sets_cap(group):
                    continue

                slot["sets"] += 1
                slot["boosted_by"] = part  # type: ignore[typeddict-unknown-key]
                group_budget[group] = budget - 1
                weekly[group] = weekly.get(group, 0) + 1
                gained += 1
        if gained:
            added[part] = gained
    return added


def weekly_sets_by_group(days: list[DayPlan]) -> dict[str, int]:
    """근육군별 주간 세트 합 — 검증·리포트용."""
    out: dict[str, int] = {}
    for day in days:
        for slot in day["slots"]:
            if slot.get("kind") == "STRENGTH":
                out[slot["muscle_group"]] = out.get(slot["muscle_group"], 0) + slot["sets"]
    return out
