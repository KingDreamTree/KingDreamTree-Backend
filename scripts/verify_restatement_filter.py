"""differences 재진술 필터 점검.

    python scripts/verify_restatement_filter.py

⚠️ 케이스는 **2026-08-15 실제 진단 출력**이다. 지어낸 문장이 아니다.
   임계값을 만질 때 이 파일부터 돌릴 것 — 재진술과 진짜 관찰의 유사도가
   0.63~0.68 대 0.15~0.22 로 갈렸고, _RESTATE_RATIO 0.45 는 그 사이다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.vlm import _coerce_part, _drop_restatements  # noqa: E402

# (differences 항목, assessment, 남아야 하는가)
CASES = [
    # ── 실제로 나갔던 재진술 — 버려야 한다 ──
    (
        "왼팔 상완이 목표 체형보다 가늘어 보입니다.",
        "왼팔 상완이 목표 체형에 비해 눈에 띄게 가늡니다. 인바디로도 왼팔 전체 근육량이"
        " 평균의 89.9% 수준입니다. 덤벨 컬 같은 운동으로 팔 근육을 키워보세요.",
        False,
    ),
    (
        "오른팔 상완이 목표 체형보다 가늘어 보입니다.",
        "오른팔 상완도 목표 체형에 비해 가는 편입니다. 인바디에 따르면 오른팔 전체"
        " 근육량이 평균의 90.6% 수준입니다.",
        False,
    ),
    (
        "오른팔 전완이 목표 체형보다 가늘어 보입니다.",
        "오른팔 전완은 목표 체형에 비해 살짝 가는 정도입니다. 팔뚝 운동을 추가해 쥐는"
        " 힘을 강화하세요.",
        False,
    ),
    # ── 진짜 관찰 — 살아야 한다 ──
    (
        "어깨선에서 팔로 이어지는 라인이 완만합니다.",
        "왼팔 상완이 목표 체형에 비해 눈에 띄게 가늡니다. 덤벨 컬 같은 운동으로 팔"
        " 근육을 키워보세요.",
        True,
    ),
    (
        "팔꿈치 아래 근육 경계가 흐릿합니다.",
        "오른팔 전완은 목표 체형에 비해 살짝 가는 정도입니다.",
        True,
    ),
    (
        "왼쪽 어깨가 오른쪽보다 살짝 내려가 있습니다.",
        "몸통은 목표 체형에 비해 두께가 부족합니다.",
        True,
    ),
]


def main() -> int:
    failed = []

    for diff, assessment, should_keep in CASES:
        kept = _drop_restatements([diff], assessment)
        ok = bool(kept) == should_keep
        mark = "O" if ok else "X"
        verb = "유지" if should_keep else "제거"
        print(f"  [{mark}] {verb}되어야 함: {diff[:40]}")
        if not ok:
            failed.append(diff)

    # assessment 가 없으면 지우지 않는다 (비교 대상이 없다).
    if _drop_restatements(["아무 말"], None) != ["아무 말"]:
        failed.append("assessment=None 인데 항목이 사라짐")
        print("  [X] assessment 없으면 그대로 둬야 함")
    else:
        print("  [O] assessment 없으면 그대로 둠")

    # 파서를 통과할 때도 실제로 걸러지는지 (배선 확인).
    part = _coerce_part(
        {
            "class_name": "Left_Upper_Arm",
            "differences": [CASES[0][0], CASES[3][0]],
            "assessment": CASES[0][1],
            "gap_level": "SIGNIFICANT",
            "priority": 1,
            "confidence": "HIGH",
        },
        {"Left_Upper_Arm"},
    )
    if part is None or len(part["differences"]) != 1:
        failed.append("_coerce_part 경로에서 안 걸러짐")
        print(f"  [X] 파서 경로: {part and part['differences']}")
    else:
        print("  [O] 파서 경로에서도 재진술만 제거됨")

    print()
    if failed:
        print(f"실패 {len(failed)}건")
        return 1
    print("문제 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
