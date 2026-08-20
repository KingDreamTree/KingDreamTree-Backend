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


#: 안내 문구는 **(소제목, 본문) 쌍**이다.
#:
#: ⚠️ 여러 안내가 동시에 붙을 수 있다(감량 + 7일 + 진단없음). 예전엔 공백으로
#:    이어붙여 한 문단으로 나갔는데, 조건이 서로 다른 이야기가 섞여 읽히지 않았다
#:    (사용자 신고 2026-08-20: "체지방률 26% 이상인 사람 이야기"와 "7일 선택한
#:    사람 이야기"가 한 덩어리). 소제목을 붙여 화면에서 나눠 보여줄 수 있게 한다.
#: ⚠️ 본문에 소제목을 반복하지 않는다 — 화면에 두 번 나온다.

#: 7일 선택 시 안내 — 7일 근력을 주지 않는 이유.
SEVEN_DAY_NOTICE_TITLE = "매일 운동을 선택하셨네요"
SEVEN_DAY_NOTICE = (
    "근육은 쉬는 날 자랍니다. "
    "6일은 근력 운동, 하루는 가볍게 걷기·스트레칭으로 구성했어요."
)

#: CUT 모드 안내 — 왜 "무게를 낮추지 않고 휴식을 줄였는지" 를 설명한다.
#:
#: ⚠️ 사용자가 가장 오해하는 지점이다. 감량 모드라고 하면 근력을 빼고 맨몸운동을
#:    할 거라 기대하는데, 우리는 정반대로 근력을 그대로 두고 휴식만 줄인다.
#:    이유를 안 적어두면 "감량 모드인데 왜 무겁게 드냐"는 문의가 온다.
CUT_NOTICE_TITLE = "감량을 함께 하는 구성이에요"
CUT_NOTICE = (
    "체지방률 기준으로 감량을 함께 하면 효과가 좋아요. "
    "다만 감량 중에 무게를 낮추면 근육이 같이 빠져요. 그래서 근력 운동은 그대로 두고, "
    "세트 사이 휴식을 짧게(60초·45초) 줄여 쉬지 않고 도는 서킷처럼 구성했어요. "
    "숨이 더 차고 칼로리 소모가 늘어나는 대신 근육은 지킬 수 있어요. "
    "관절 부담을 줄이려고 유산소는 자전거·일립티컬처럼 충격이 적은 종목으로 골랐고, "
    "쉬는 날에도 30분 정도 걷기를 추천해요."
)


def join_notices(notices: list[dict[str, str]]) -> str | None:
    """안내 목록을 옛 클라이언트용 한 문자열로 합친다.

    형식: 소제목 줄 + 본문 줄을 한 덩어리로 두고, 덩어리 사이를 **빈 줄**로 나눈다.
    받는 쪽이 빈 줄로 잘라 첫 줄을 소제목으로 읽으면 구조를 복원할 수 있다
    (raw_response 에 notices 를 같이 저장하므로 보통은 그럴 필요가 없다).

    ⚠️ 공백 하나로 이어붙이지 않는다 — 조건이 다른 안내가 한 문단으로 섞여
       읽히지 않았던 것이 이 함수를 만든 이유다 (사용자 신고 2026-08-20).
    """
    if not notices:
        return None
    blocks = [f"{n['title']}\n{n['body']}" for n in notices]
    return "\n\n".join(blocks)


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

    근력 후 중강도. ⚠️ 이것만으로 주 150분이 되지 않는다.
    나머지는 CUT_NOTICE 의 일상 걷기 안내가 담당한다.
    """
    return {"muscle_group": "유산소", "kind": "CARDIO", "duration_min": duration_min, "sets": 1}


#: 복합 다관절이 주로 배치되는 근육군. 서킷 휴식·슬롯 상한 양쪽에서 쓴다.
_COMPOUND_GROUPS = frozenset({"가슴", "등", "대퇴사두", "햄스트링·둔근", "어깨"})


# ── CUT 서킷화 (2026-08-15) ──────────────────────────────────────────────────
#
# ⚠️ **강도를 낮추지 않는다. 밀도를 올린다.**
#
#    "감량 모드니까 맨몸운동으로 바꾸자"는 직관은 방향이 반대다. 감량기에
#    근손실을 막는 것은 저항운동 자체이고(노인 비만에서 근손실의 93.5% 방지,
#    PMC5946208), 강도를 빼면 그 보호 효과가 같이 빠진다.
#
#    대신 근거가 있는 건 **서킷화**다 — 휴식을 줄여 세션 밀도를 올리면
#    근력·제지방 효과는 전통 방식과 동등하면서 심폐·대사 반응이 커지고
#    지방 감소에 더 유리하다 (Resistance Circuit-Based Training 메타분석
#    PMC8145598 · 고강도 서킷 vs 전통 PMID 40323711).
#
#    또 감량기에는 **중강도가 고강도보다 제지방 보존에 낫다**는 결과가 있어
#    (네트워크 메타분석 PMC12158682), 반복수를 12~15 로 올려 중강도 구간에
#    맞춘다. 무게는 RIR 자가조절이 알아서 낮춘다.

#: CUT 모드 휴식 시간 (초). 복합 90→60, 고립 60→45.
#: 완전 회복을 포기하는 대신 세션 밀도를 얻는다.
CUT_REST_SEC_COMPOUND = 60
CUT_REST_SEC_ISOLATION = 45

#: CUT 모드 최소 반복수 — 중강도 구간(12~15)으로 올린다.
CUT_MIN_REPS = 12


def _to_circuit(slot: Slot) -> Slot:
    """근력 슬롯을 CUT 서킷형으로 바꾼다 (휴식 단축 + 반복 상향)."""
    if slot.get("kind") != "STRENGTH":
        return slot
    out = dict(slot)
    compound = slot["muscle_group"] in _COMPOUND_GROUPS
    out["rest_sec"] = CUT_REST_SEC_COMPOUND if compound else CUT_REST_SEC_ISOLATION
    out["reps"] = max(int(slot["reps"]), CUT_MIN_REPS)
    return out  # type: ignore[return-value]


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


def _cut_cardio_min(days_per_week: int) -> int:
    """CUT 근력일 1회당 유산소 분(分).

    ACSM Donnelly 2009 는 감량에 주 150분+ 를 권고한다. 루틴이 그 전부를
    보장하지는 않지만(나머지는 CUT_NOTICE 의 일상 걷기), **일수가 적을수록
    한 세션에 더 담아** 주간 총량이 크게 어긋나지 않게 한다.

        1~2일 → 25분   3~4일 → 20분   5일+ → 15분
    """
    if days_per_week <= 2:
        return 25
    if days_per_week <= 4:
        return 20
    return 15


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
            # 근력은 그대로 두고 **밀도만** 올린다 (§CUT 서킷화 주석).
            day["slots"] = [_to_circuit(s) for s in day["slots"]]
            # 유산소는 주간 총량이 비슷하게 유지되도록 일수에 반비례해 배분한다.
            day["slots"].append(_cardio(_cut_cardio_min(days_per_week)))
        days.append(day)
    return days


# ── L2 — 약점 부위 볼륨 가중 (D10 확정: 진단은 가중치, 포함/제외 아님) ─────────

#: 근육군 하나가 주간에 받는 추가 세트 상한 — **우선 부위 1개 기준**.
#:
#: ⚠️ 2026-08-15 재정정: 좌우가 **둘 다** 약하면 예산을 2배로 준다.
#:
#:    08-14 에는 "운동은 좌우를 함께 하니 근육군 예산 하나로 묶어야 한다"고
#:    보고 이중 가산을 막았다. 그런데 그건 **두 개의 다른 문제를 하나로 뭉갠**
#:    것이었다:
#:
#:      양쪽 다 약함  = 그 근육군의 **절대 볼륨 부족** → 볼륨을 더 올려야 한다
#:      한쪽만 약함   = **좌우 불균형**  → 볼륨이 아니라 단측 운동으로 교정한다
#:
#:    처방이 다르므로 예산도 달라야 한다. 양팔이 다 약한 사람과 왼팔만 약한
#:    사람에게 같은 볼륨을 주면 전자는 부족하고 후자는 불균형이 안 고쳐진다.
#:
#:    근거 — 단측 훈련이 비대칭 교정에 효과적이고 양측 훈련은 그렇지 않다
#:    (Front Physiol 2023 메타분석). 비대칭 10~15% 초과 시 부상 위험 상승.
#:    → 한쪽만 약하면 `asymmetric_parts` 경로가 단측 운동을 우선 배치한다.
#:
#:    상한은 WEEKLY_GROUP_SET_CAP 이 최종적으로 막는다.
WEEKLY_BOOST_CAP_PER_GROUP = 4

#: 같은 근육군에 매핑된 우선 부위가 여럿일 때 예산 배수의 상한.
#: 좌우 2개까지만 인정한다 — 상완·전완이 같은 근육군을 공유하는 경우까지
#: 배수로 세면 한 근육군에 볼륨이 과하게 몰린다.
BOOST_MULTIPLIER_CAP = 2

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
    targets = priority_parts[:max_parts]

    # 근육군별 예산 = 기본값 × (그 근육군을 가리키는 우선 부위 수, 최대 2배).
    # 좌우가 둘 다 약하면 절대 볼륨이 부족한 것이므로 예산을 2배로 준다
    # (상수 주석의 "양쪽 다 약함 vs 한쪽만 약함" 참고).
    demand: dict[str, int] = {}
    for part in targets:
        for group in part_to_slots.get(part, ()):
            demand[group] = demand.get(group, 0) + 1

    group_budget: dict[str, int] = {
        g: WEEKLY_BOOST_CAP_PER_GROUP * min(n, BOOST_MULTIPLIER_CAP)
        for g, n in demand.items()
    }
    weekly = weekly_sets_by_group(days)

    for part in targets:
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

                budget = group_budget.get(group, 0)
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
