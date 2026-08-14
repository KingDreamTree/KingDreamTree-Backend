"""운동 카탈로그 후보 필터 검증.

    python scripts/verify_exercise_catalog.py

⚠️ data/exercise_catalog.json 이 있어야 한다 (fetch_exercisedb.py fetch).
   카탈로그가 없으면 SKIP 하고 0 을 반환한다 — CI 에서 키 없이도 돌게 하기 위함.

확인하는 것
    1. 모든 슬롯에 쓸 만한 후보가 남는가       ← 후보 0개면 그 슬롯은 루틴이 빔
    2. 스트레칭이 근력 후보에 섞이지 않는가     ← 데이터 라벨 오류(35건) 보정
    3. 같은 이름이 중복되지 않는가             ← 중복 수록 9건
    4. CUT 유산소 후보가 실재하는가            ← D3 실현 가능성
    5. 장비 운동이 앞에 오는가                 ← 헬스장 전제
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import exercise_catalog as ec  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []

#: 슬롯당 최소 후보 수. 이보다 적으면 LLM 이 "고를" 여지가 없다.
MIN_CANDIDATES = 5


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)


def main() -> int:
    print("운동 카탈로그 후보 필터 검증\n")

    try:
        catalog = ec.load_catalog()
    except ec.CatalogNotBuiltError as e:
        print(f"  [SKIP] {e}")
        return 0

    print(f"카탈로그 {len(catalog)}개\n")

    print("1. 슬롯별 후보 확보")
    for slot, stat in ec.coverage_report(catalog).items():
        check(f"{slot} 후보 {stat['total']}개", stat["total"] >= MIN_CANDIDATES)

    print("\n2. 스트레칭 혼입 차단 (라벨 오류 보정)")
    leaked = []
    for slot in ec.SLOT_BODY_PARTS:
        for e in ec.candidates_for_slot(slot, catalog, limit=999):
            name = (e.get("name_en") or "").lower()
            if any(h in name for h in ec.NON_STRENGTH_NAME_HINTS):
                leaked.append(e["name_en"])
    check("근력 후보에 스트레칭 없음", not leaked, f"{len(leaked)}건 누출" if leaked else "")

    print("\n3. 중복 제거")
    dup_found = []
    for slot in ec.SLOT_BODY_PARTS:
        names = [
            (e.get("name_en") or "").strip().lower()
            for e in ec.candidates_for_slot(slot, catalog, limit=999)
        ]
        if len(names) != len(set(names)):
            dup_found.append(slot)
    check("슬롯 후보에 이름 중복 없음", not dup_found, str(dup_found))

    print("\n4. CUT 유산소 (D3 실현 가능성)")
    cardio = ec.cardio_candidates(catalog, limit=999)
    check("유산소 후보 존재", len(cardio) >= 3, f"{len(cardio)}개")
    machine = [c for c in cardio if set(c.get("equipments") or []) - {"BODY WEIGHT"}]
    check("헬스장 장비 유산소 존재", len(machine) >= 2, f"{len(machine)}개")

    print("\n5. 정렬 우선순위")
    # 주동근 일치가 최우선 — 이두/삼두는 bodyParts 가 같아서(UPPER ARMS)
    # targetMuscles 로 갈라야 한다. 이게 없으면 이두 슬롯에 프레스가 뽑힌다 (실측).
    for slot in ("이두", "삼두"):
        primary = ec.SLOT_TARGET_MUSCLES[slot]
        top = ec.candidates_for_slot(slot, catalog, limit=4)
        hits = [bool(primary & set(c.get("target_muscles") or [])) for c in top]
        check(
            f"{slot} 상위 후보의 주동근 일치",
            all(hits),
            ", ".join(c["name_en"].strip() for c in top[:3]),
        )
    chest = ec.candidates_for_slot("가슴", catalog, limit=6)
    if chest:
        # 주동근 일치 그룹 안에서는 장비 운동이 맨몸보다 앞이어야 한다
        check(
            "가슴 1순위가 장비+주동근 일치",
            bool(set(chest[0].get("equipments") or []) - {"BODY WEIGHT"}),
            chest[0]["name_en"].strip(),
        )

    print("\n6. 진단 부위 → 슬롯 매핑")
    for part, slots in ec.PART_TO_SLOTS.items():
        unknown = [s for s in slots if s not in ec.SLOT_BODY_PARTS]
        if unknown:
            check(f"{part} 매핑", False, f"미정의 슬롯 {unknown}")
    check("모든 진단 부위가 슬롯에 연결됨", True, f"{len(ec.PART_TO_SLOTS)}부위")

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
