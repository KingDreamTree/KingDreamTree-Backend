"""루틴 불변식 검증 — 구조적으로 보장되는 규칙이 회귀로 깨지지 않게 못 박는다.

    python scripts/verify_routine_rules.py

여기 있는 규칙들은 **에러 없이 조용히 깨지는** 종류다. 유산소가 근력 앞으로
가도, 가중이 이중으로 붙어도 루틴은 정상적으로 생성된다. 그래서 테스트가 없으면
아무도 모른다.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows 콘솔(cp949)에서 한글·기호가 깨지지 않게. 검증 스크립트는 출력이 결과다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services import exercise_catalog  # noqa: E402
from app.services.exercise_catalog import PART_TO_SLOTS  # noqa: E402
from app.services.routine_mode import decide_mode  # noqa: E402
from app.services.routine_templates import (  # noqa: E402
    BOOST_MULTIPLIER_CAP,
    CUT_MIN_REPS,
    WEEKLY_BOOST_CAP_PER_GROUP,
    WEEKLY_GROUP_SET_CAP,
    apply_weakness_boost,
    get_template,
    slot_sets_cap,
    weekly_sets_by_group,
)

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


def rule_cardio_last() -> None:
    """유산소는 반드시 근력 **뒤**에 온다.

    근거: 유산소를 먼저 하면 글리코겐이 고갈된 상태로 근력을 하게 되어
    수행 능력과 근비대 신호가 함께 떨어진다. 현재는 get_template 이
    append 로 붙여 구조적으로 보장되지만, 누가 insert 로 바꾸면 조용히 깨진다.
    """
    print("\n[유산소는 근력 뒤]")
    for n in range(1, 8):
        for day in get_template(n, "CUT"):
            kinds = [s.get("kind") for s in day["slots"]]
            if "CARDIO" not in kinds:
                continue
            first_cardio = kinds.index("CARDIO")
            ok = all(k == "CARDIO" for k in kinds[first_cardio:])
            check(f"{n}일 Day{day['day_order']}", ok, " ".join(kinds))


def rule_boost_per_group() -> None:
    """가중 예산은 **근육군 단위**로 공유되되, 좌우가 둘 다 약하면 2배까지 준다.

    두 문제를 구분한다 (routine_templates §WEEKLY_BOOST_CAP_PER_GROUP):
        양쪽 다 약함 = 절대 볼륨 부족 → 예산 2배
        한쪽만 약함  = 좌우 불균형   → 예산 1배 + 단측 운동으로 교정
    배수는 BOOST_MULTIPLIER_CAP 에서 멈춘다 — 상완·전완이 같은 근육군을
    공유해도 3배가 되지 않아야 한다.
    """
    print("\n[근육군당 가중 상한]")
    cap1 = WEEKLY_BOOST_CAP_PER_GROUP
    cap2 = WEEKLY_BOOST_CAP_PER_GROUP * BOOST_MULTIPLIER_CAP

    cases = [
        ("한쪽 팔만", ["Left_Upper_Arm"], cap1),
        ("좌우 팔", ["Left_Upper_Arm", "Right_Upper_Arm"], cap2),
        ("좌우 허벅지", ["Left_Upper_Leg", "Right_Upper_Leg"], cap2),
        ("몸통+좌우팔", ["Torso", "Left_Upper_Arm", "Right_Upper_Arm"], cap2),
    ]
    for label, parts, ceiling in cases:
        for n in (3, 4, 6):
            days = get_template(n)
            before = weekly_sets_by_group(days)
            apply_weakness_boost(days, parts, PART_TO_SLOTS)
            after = weekly_sets_by_group(days)
            over = {
                g: (after[g] - before.get(g, 0))
                for g in after
                if after[g] - before.get(g, 0) > ceiling
            }
            check(f"{label} {n}일 ≤ {ceiling}", not over, str(over) if over else "")

    # 양쪽이 한쪽보다 **실제로 더 받아야** 한다 — 상한만 검사하면 0 도 통과한다.
    print("\n[좌우 이중 가산이 실제로 작동]")
    for n in (4, 5, 6):
        one, both = get_template(n), get_template(n)
        b1 = weekly_sets_by_group(one)
        apply_weakness_boost(one, ["Left_Upper_Arm"], PART_TO_SLOTS)
        apply_weakness_boost(both, ["Left_Upper_Arm", "Right_Upper_Arm"], PART_TO_SLOTS)
        a1, a2 = weekly_sets_by_group(one), weekly_sets_by_group(both)
        gain1 = sum(a1[g] - b1.get(g, 0) for g in a1)
        gain2 = sum(a2[g] - b1.get(g, 0) for g in a2)
        check(f"{n}일 양쪽({gain2}) > 한쪽({gain1})", gain2 > gain1)


def rule_cut_is_circuit() -> None:
    """CUT 은 강도를 낮추지 않고 밀도를 올린다.

    감량기에 무게를 빼면 제지방이 같이 빠진다. 근력 슬롯의 세트 수는 그대로
    두고 휴식만 줄이며, 반복수는 중강도 구간(12+)으로 올린다.
    """
    print("\n[CUT 서킷화]")
    for n in range(1, 8):
        base = {
            (d["day_order"], i): s
            for d in get_template(n, "BALANCE")
            for i, s in enumerate(d["slots"])
        }
        cut_days = get_template(n, "CUT")

        rest_ok = reps_ok = sets_ok = True
        for d in cut_days:
            for i, s in enumerate(d["slots"]):
                if s.get("kind") != "STRENGTH":
                    continue
                b = base[(d["day_order"], i)]
                if s["sets"] != b["sets"]:
                    sets_ok = False  # 강도(볼륨)를 깎지 않는다
                if s["rest_sec"] >= b["rest_sec"]:
                    rest_ok = False
                if s["reps"] < CUT_MIN_REPS:
                    reps_ok = False
        check(f"{n}일 휴식 단축", rest_ok)
        check(f"{n}일 반복 ≥ {CUT_MIN_REPS}", reps_ok)
        check(f"{n}일 세트 수 불변 (볼륨을 깎지 않음)", sets_ok)


def rule_cut_cardio_is_low_impact() -> None:
    """CUT 유산소 후보에 달리기·줄넘기·버피가 없어야 한다.

    CUT 은 체지방률로 판정되므로 체중이 무겁다. 무릎 반력은 걷기 체중 ~3배,
    달리기 ~8배다. 다만 자전거는 이름에 "Run" 이 붙어도 통과해야 한다.
    """
    print("\n[CUT 저충격 유산소]")
    catalog = exercise_catalog.load_catalog()
    picked = exercise_catalog.cardio_candidates(catalog, low_impact_only=True)
    names = [e.get("name_en", "") for e in picked]

    check("후보가 비지 않음", len(picked) > 0, f"{len(picked)}개")
    banned = [n for n in names if not exercise_catalog.is_low_impact(n)]
    check("고충격 종목 없음", not banned, str(banned))

    for name, want in [
        ("Assault Bike Run", True),   # 장비가 이름을 이긴다
        ("Stationary Bike Run", True),
        ("Walking on Incline Treadmill", True),
        ("Lever stepper", True),
        ("Run on Treadmill", False),
        ("Jump Rope", False),
        ("Burpee", False),
        ("Front Toe Touching", True),  # 오탐 회귀 방지
    ]:
        check(f"{name} → {'저충격' if want else '고충격'}",
              exercise_catalog.is_low_impact(name) is want)


def rule_caps() -> None:
    """슬롯 세트 상한(복합/고립)과 근육군 주간 총량 상한을 넘지 않는다."""
    print("\n[세트 상한]")
    for n in range(1, 8):
        days = get_template(n)
        apply_weakness_boost(days, ["Torso", "Left_Upper_Leg", "Left_Upper_Arm"], PART_TO_SLOTS)

        bad_slot = [
            (d["day_order"], s["muscle_group"], s["sets"])
            for d in days
            for s in d["slots"]
            if s.get("kind") == "STRENGTH" and s["sets"] > slot_sets_cap(s["muscle_group"])
        ]
        check(f"{n}일 슬롯 상한", not bad_slot, str(bad_slot) if bad_slot else "")

        weekly = weekly_sets_by_group(days)
        over = {g: v for g, v in weekly.items() if v > WEEKLY_GROUP_SET_CAP}
        check(f"{n}일 주간 총량", not over, str(over) if over else "")


def rule_no_diagnosis_shaped_days() -> None:
    """진단이 없어도 모든 Day 가 슬롯을 갖는다 (D10 — 진단은 가중치일 뿐)."""
    print("\n[진단 없이도 골격 성립]")
    for n in range(1, 8):
        days = get_template(n)
        empty = [d["day_order"] for d in days if not d["slots"]]
        check(f"{n}일 빈 Day 없음", not empty, str(empty) if empty else "")


def rule_bmi_never_triggers_cut() -> None:
    """체지방률 없이 BMI 만으로 CUT 에 보내지 않는다 (근육형 오분류 차단)."""
    print("\n[BMI 단독으로 CUT 금지]")
    cases = [
        ("BMI 27 근육형", {"bmi": 27.0}),
        ("BMI 31 고도", {"bmi": 31.0}),
        ("체중/신장만", {"weight": 90.0, "height": 175.0}),
    ]
    for label, inbody in cases:
        r = decide_mode(inbody)
        check(f"{label} → BALANCE", r["mode"] == "BALANCE", f"basis={r['basis']}")

    r = decide_mode({"body_fat_percentage": 30.0, "gender": "MALE"})
    check("체지방률 30% 남성 → CUT", r["mode"] == "CUT", f"basis={r['basis']}")


def rule_sanitize_holds_under_partial_llm() -> None:
    """LLM 선택이 **일부만** 유효할 때도 중복·상한 불변식이 유지된다.

    ⚠️ 이 경로가 실제 운영 경로다. mock·전면실패는 폴백표가 통째로 쓰여서
       자연히 정합적이지만, LLM 응답이 섞이면 미리 만든 폴백표의 중복 계산이
       무효가 된다. 퍼징으로 같은 Day 중복 41건·주간 상한 초과 14건을 재현했다.
    """
    print("\n[LLM 부분 실패 시 선택 정합성]")
    import random
    from collections import Counter

    from app.services import exercise_catalog as ec
    from app.services import routine

    try:
        catalog = ec.load_catalog()
    except ec.CatalogNotBuiltError as e:
        print(f"  SKIP  {e}")
        return

    random.seed(7)
    bad_week = bad_day = unfilled = 0
    trials = 0
    for n in (3, 4, 5, 6, 7):
        for mode in ("BALANCE", "CUT"):
            days = get_template(n, mode)
            cands = routine._collect_candidates(days, catalog, [])
            slots = [
                routine._slot_id(d, i)
                for d in days
                for i, s in enumerate(d["slots"])
                if s.get("kind") != "CARDIO"
            ]
            fillable = [s for s in slots if cands.get(s)]
            for _ in range(200):
                trials += 1
                raw: dict[str, str] = {}
                for sid in fillable:
                    roll = random.random()
                    if roll < 0.3:
                        raw[sid] = "garbage-ref"  # 후보 밖
                    elif roll < 0.9:
                        raw[sid] = random.choice(cands[sid])["exercise_ref"]
                    # 나머지 10% 는 아예 누락
                final, _fixed = routine._sanitize_selections(raw, days, cands)

                if len(final) != len(fillable):
                    unfilled += 1
                if any(v > routine.MAX_WEEKLY_REPEAT for v in Counter(final.values()).values()):
                    bad_week += 1
                for d in days:
                    ids = [
                        routine._slot_id(d, i)
                        for i, s in enumerate(d["slots"])
                        if s.get("kind") != "CARDIO"
                    ]
                    picks = [final[x] for x in ids if x in final]
                    if len(picks) != len(set(picks)):
                        bad_day += 1
                        break

    check(f"같은 Day 중복 없음 ({trials}시행)", bad_day == 0, f"{bad_day}건")
    check(f"주간 동일 운동 ≤ {routine.MAX_WEEKLY_REPEAT}", bad_week == 0, f"{bad_week}건")
    check("빈 슬롯 없음 (교정이 슬롯을 지우지 않는다)", unfilled == 0, f"{unfilled}건")


def main() -> int:
    rule_cardio_last()
    rule_boost_per_group()
    rule_cut_is_circuit()
    rule_cut_cardio_is_low_impact()
    rule_caps()
    rule_no_diagnosis_shaped_days()
    rule_bmi_never_triggers_cut()
    rule_sanitize_holds_under_partial_llm()

    print()
    if _failures:
        print(f"실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
