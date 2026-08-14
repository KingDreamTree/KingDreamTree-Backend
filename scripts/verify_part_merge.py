"""옷 병합 규칙 검증 — 합성 라벨 맵으로.

사용법:
    python scripts/verify_part_merge.py

⚠️ GPU·DB 없이 돈다. 병합 로직만 본다.

무엇을 확인하나
    (1) 소매가 팔로 간다 — 하드코딩 매핑(`Upper_Clothing → Torso`)이 틀리는 지점
    (2) 긴바지가 허벅지와 종아리로 나뉜다
    (3) 좌우가 자동으로 갈린다 (경계선을 손으로 안 정해도 됨)
    (4) 어디에도 안 닿는 옷은 옷으로 남는다
    (5) 원본 배열이 변하지 않는다
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.part_merge import merge_clothing  # noqa: E402

# 실제 alpha 매핑의 인덱스를 그대로 쓴다
LABELS = {
    0: "Background",
    11: "Left_Upper_Arm",
    20: "Right_Upper_Arm",
    12: "Left_Upper_Leg",
    8: "Left_Lower_Leg",
    22: "Torso",
    23: "Upper_Clothing",
    13: "Lower_Clothing",
}
LABEL_MAP = {str(k): v for k, v in LABELS.items()}
TARGETS = {
    "Torso",
    "Left_Upper_Arm",
    "Right_Upper_Arm",
    "Left_Upper_Leg",
    "Left_Lower_Leg",
}

passed: list[str] = []
failed: list[str] = []


def check(label: str, cond: bool, note: str = "") -> None:
    (passed if cond else failed).append(label)
    print(f"  [{'O' if cond else 'X'}] {label}" + (f"  — {note}" if note else ""))


#: 긴팔 장면의 소매 영역 (y, x 범위) — 흡수 결과를 이 범위로 재서 본다
SLEEVE_RIGHT = (slice(10, 14), slice(4, 12))  # 화면 왼쪽 = 피사체의 오른팔
SLEEVE_LEFT = (slice(10, 14), slice(18, 26))  # 화면 오른쪽 = 피사체의 왼팔


def scene_long_sleeve() -> np.ndarray:
    """긴팔 셔츠, T자 포즈.

        몸통은 세로, 소매는 양옆으로 뻗는다. 어깨에서 손목까지 전부 옷이고
        손목 끝에만 살이 보인다 — 긴팔 사진에서 실제로 나오는 모양이다.

    ⚠️ 여기가 하드코딩 매핑(`Upper_Clothing → Torso`)이 무너지는 지점이다.
       그 규칙이면 소매 전체가 몸통으로 들어가고 팔은 손목 몇 px 로 남는다.
    """
    m = np.zeros((30, 30), dtype=np.uint8)
    m[6:24, 12:18] = 23  # 셔츠 몸통 (세로)
    m[10:14, 4:12] = 23  # 오른쪽 소매 (가로)
    m[10:14, 18:26] = 23  # 왼쪽 소매 (가로)
    m[7:10, 13:17] = 22  # 목 아래 가슴 일부 노출
    m[10:14, 2:4] = 20  # 오른팔 손목 노출
    m[10:14, 26:28] = 11  # 왼팔 손목 노출
    return m


def scene_long_pants() -> np.ndarray:
    """긴바지 — 허벅지와 종아리가 모두 덮이고 발목만 보인다."""
    m = np.zeros((40, 20), dtype=np.uint8)
    m[5:35, 5:15] = 13  # 바지
    m[5:8, 5:15] = 12  # 골반 아래 허벅지 일부 노출
    m[33:35, 5:15] = 8  # 발목 위 종아리 노출
    return m


def main() -> int:
    print("=" * 68)
    print("옷 병합 규칙 검증")
    print("=" * 68)

    # ── (1) 긴팔 ─────────────────────────────────────────────────────────
    print("\n긴팔 셔츠 — 소매가 어디로 가는가")
    src = scene_long_sleeve()
    before = src.copy()
    merged, contrib = merge_clothing(src, LABEL_MAP, TARGETS)

    print(f"      옷에서 흡수: {contrib}")

    # 소매 영역만 잘라서 "그 소매가 어느 부위로 갔는지" 본다
    for name, region, value, label in (
        ("왼팔", SLEEVE_LEFT, 11, "Left_Upper_Arm"),
        ("오른팔", SLEEVE_RIGHT, 20, "Right_Upper_Arm"),
    ):
        sleeve = merged[region]
        cloth = sleeve[np.isin(src[region], [23])]
        to_arm = int((cloth == value).sum())
        to_torso = int((cloth == 22).sum())
        total = int(cloth.size)
        print(f"      {name} 소매 {total}px → 팔 {to_arm} / 몸통 {to_torso}")
        check(
            f"{name} 소매의 절반 이상이 팔로 감",
            to_arm > total * 0.5,
            f"{to_arm}/{total} ({to_arm / total * 100:.0f}%)",
        )

    left, right = int((merged == 11).sum()), int((merged == 20).sum())
    check(
        "좌우가 뒤섞이지 않음",
        abs(left - right) <= max(left, right) * 0.2,
        f"왼팔 {left}px vs 오른팔 {right}px",
    )
    check("원본 배열 불변", np.array_equal(src, before))

    # ── (2) 긴바지 ────────────────────────────────────────────────────────
    print("\n긴바지 — 허벅지와 종아리로 나뉘는가")
    merged2, contrib2 = merge_clothing(scene_long_pants(), LABEL_MAP, TARGETS)
    print(f"      옷에서 흡수: {contrib2}")
    thigh = contrib2.get("Left_Upper_Leg", 0)
    calf = contrib2.get("Left_Lower_Leg", 0)
    check(
        "허벅지·종아리 둘 다에 분배됨", thigh > 0 and calf > 0, f"허벅지 {thigh}px, 종아리 {calf}px"
    )
    check("한쪽으로 몰리지 않음", min(thigh, calf) > max(thigh, calf) * 0.2, f"{thigh} : {calf}")

    # ── (3) 닿지 않는 옷 ──────────────────────────────────────────────────
    print("\n어디에도 안 닿는 옷")
    lonely = np.zeros((20, 20), dtype=np.uint8)
    lonely[5:15, 5:15] = 23  # 몸이 하나도 안 보이는 셔츠
    merged3, contrib3 = merge_clothing(lonely, LABEL_MAP, TARGETS)
    check(
        "옷으로 남는다 (억지로 배정하지 않음)",
        int((merged3 == 23).sum()) == 100 and not contrib3,
        f"남은 옷 {int((merged3 == 23).sum())}px",
    )

    # ── (4) 옷이 없을 때 ──────────────────────────────────────────────────
    print("\n옷이 없는 맵")
    bare = np.zeros((10, 10), dtype=np.uint8)
    bare[2:8, 2:8] = 22
    merged4, contrib4 = merge_clothing(bare, LABEL_MAP, TARGETS)
    check("그대로 통과", np.array_equal(merged4, bare) and not contrib4)

    print("\n" + "=" * 68)
    print(f"통과 {len(passed)} / 실패 {len(failed)}")
    for f in failed:
        print(f"  [X] {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
