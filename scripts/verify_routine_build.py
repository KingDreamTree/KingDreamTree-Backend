"""F10 루틴 조립 검증 (mock — DB·LLM 키 불필요).

    python scripts/verify_routine_build.py

확정 로직(2026-08-14 PM)의 계약을 검증한다:
    A. 인바디 없음 → BALANCE + 진단 실패 부위도 기본 볼륨 (D10)
    B. 체지방률 초과 → CUT + 근력일마다 유산소 항목 (D1·D3)
    C. 체지방률 미만 → BALANCE + 약점 가중 (D1)
    D. 가중 상한 (부위당 +4세트/주, 슬롯 5세트, 총 20세트/주)
    E. 선택 무결성 — 모든 운동이 카탈로그 후보에서만 나옴
    F. RIR 처방 · 중량 미표기 (D9)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

settings.use_mock = True  # LLM 없이 결정론 폴백 경로 검증

from app.services import exercise_catalog as ec  # noqa: E402
from app.services.routine import build_routine  # noqa: E402
from app.services.routine_templates import SLOT_SETS_CAP, WEEKLY_BOOST_CAP  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)


def run(**kwargs):
    return asyncio.run(build_routine(**kwargs))


INBODY_FAT = {"gender": "MALE", "body_fat_percentage": 27.0, "bmi": 26.0, "weight": 80.0}
INBODY_FIT = {"gender": "MALE", "body_fat_percentage": 18.9, "bmi": 22.0, "weight": 63.5}
PRIORITY = ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"]


def main() -> int:
    print("F10 루틴 조립 검증 (결정론 폴백 경로)\n")
    try:
        ec.load_catalog()
    except ec.CatalogNotBuiltError as e:
        print(f"  [SKIP] {e}")
        return 0

    # ── A. 인바디 없음 (D10 — 기본 볼륨 + 가중) ──────────────────────────────
    print("A. 인바디 없음 → BALANCE, 진단 부위만 가중")
    plan = run(days_per_week=3, inbody=None, priority_parts=PRIORITY)
    check("BALANCE 모드", plan["mode"] == "BALANCE", plan["mode_basis"])
    check("Day 수 = N (휴식일 행 없음)", len(plan["days"]) == 3)
    check(
        "유산소 항목 없음",
        all(e["kind"] == "STRENGTH" for d in plan["days"] for e in d["exercises"]),
    )
    groups = {
        e["muscle_group"] for d in plan["days"] for e in d["exercises"] if e["kind"] == "STRENGTH"
    }
    # 진단은 팔·몸통뿐이지만 하체(진단 실패 가정)도 기본 볼륨으로 존재해야 한다
    check("하체도 기본 볼륨 존재 (D10)", {"대퇴사두", "햄스트링·둔근"} & groups, str(groups))
    check("약점 가중 발생", bool(plan["boosts"]), str(plan["boosts"]))
    boosted = [e for d in plan["days"] for e in d["exercises"] if e.get("boosted_by")]
    check("가중 슬롯에 근거 부위 표기", bool(boosted), f"{len(boosted)}슬롯")

    # ── B. CUT (D1 체지방률 1차 · D3 유산소 항목) ────────────────────────────
    print("\nB. 체지방률 27% → CUT")
    cut = run(days_per_week=3, inbody=INBODY_FAT, priority_parts=PRIORITY)
    check(
        "CUT 모드 (체지방률 근거)",
        cut["mode"] == "CUT" and cut["mode_basis"] == "BODY_FAT_MEASURED",
    )
    for d in cut["days"]:
        kinds = [e["kind"] for e in d["exercises"]]
        check(f"Day{d['day_order']} 근력+유산소 항목", "STRENGTH" in kinds and "CARDIO" in kinds)
    check(
        "근력 유지 (감량기 근육 보존)",
        all(sum(1 for e in d["exercises"] if e["kind"] == "STRENGTH") >= 3 for d in cut["days"]),
    )
    check("CUT 에서도 약점 가중 유지", bool(cut["boosts"]), str(cut["boosts"]))
    check("모드 근거 문구 존재", "체지방률" in cut["mode_reason"], cut["mode_reason"])

    # ── C. 근육형 오분류 방지 (BMI 26 · 체지방 12%) ──────────────────────────
    print("\nC. 근육형 → BALANCE")
    fit = run(
        days_per_week=4,
        inbody={"gender": "MALE", "bmi": 26.0, "body_fat_percentage": 12.0},
        priority_parts=[],
    )
    check("BMI 26 이어도 BALANCE", fit["mode"] == "BALANCE")
    check("가중 없음 (우선 부위 없음)", not fit["boosts"])

    # ── D. 가중 상한 ─────────────────────────────────────────────────────────
    print("\nD. 볼륨 상한")
    check(
        f"부위당 주간 가산 ≤ {WEEKLY_BOOST_CAP}",
        all(v <= WEEKLY_BOOST_CAP for v in plan["boosts"].values()),
    )
    all_sets = [e["sets"] for d in plan["days"] for e in d["exercises"] if e["kind"] == "STRENGTH"]
    check(f"슬롯 세트 ≤ {SLOT_SETS_CAP}", max(all_sets) <= SLOT_SETS_CAP, f"max={max(all_sets)}")
    check("근육군 주간 ≤ 20세트", max(plan["weekly_sets"].values()) <= 20, str(plan["weekly_sets"]))

    # ── E. 선택 무결성 ───────────────────────────────────────────────────────
    print("\nE. 선택 무결성")
    catalog_refs = {c["exercise_ref"] for c in ec.load_catalog()}
    every = [e for p in (plan, cut, fit) for d in p["days"] for e in d["exercises"]]
    check("모든 운동이 카탈로그에서만", all(e["exercise_ref"] in catalog_refs for e in every))
    check("모든 운동에 한글명", all(any("가" <= ch <= "힣" for ch in e["name"]) for e in every))
    check("모든 운동에 이미지 URL", all(e.get("image_url") for e in every))
    for p, name in ((plan, "A"), (cut, "B")):
        for d in p["days"]:
            refs = [e["exercise_ref"] for e in d["exercises"]]
            if len(refs) != len(set(refs)):
                check(f"{name} Day{d['day_order']} 중복 없음", False)
                break
    check("같은 Day 내 중복 없음", True)
    week_refs: dict[str, int] = {}
    for d in plan["days"]:
        for e in d["exercises"]:
            week_refs[e["exercise_ref"]] = week_refs.get(e["exercise_ref"], 0) + 1
    check("주간 동일 운동 ≤ 2회", max(week_refs.values()) <= 2, f"max={max(week_refs.values())}")

    # ── F. RIR / 7일 변환 / 결정론 ───────────────────────────────────────────
    print("\nF. 처방·안내")
    strength = [e for d in plan["days"] for e in d["exercises"] if e["kind"] == "STRENGTH"]
    check(
        "전 근력 운동에 RIR 안내", all(e.get("rir") == 2 and "여유" in e["note"] for e in strength)
    )
    check("weight_kg 미표기 (D9)", all("weight_kg" not in e for e in strength))
    seven = run(days_per_week=7, inbody=None)
    check(
        "7일 → 6근력+1회복",
        len(seven["days"]) == 7
        and any(e["kind"] == "CARDIO" for e in seven["days"][-1]["exercises"]),
    )
    check("7일 안내 문구", bool(seven["notice"]))
    plan2 = run(days_per_week=3, inbody=None, priority_parts=PRIORITY)
    check("mock 결정론 (같은 입력 = 같은 루틴)", plan == plan2)

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
