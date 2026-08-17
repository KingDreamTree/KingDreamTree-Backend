"""part_pairing 규칙 검증 — DB·GPU·키 불필요.

검증하는 것
    1. 미러 매핑이 대합(involution)인가 — mirror(mirror(x)) == x
    2. 좌우 개념이 없는 부위(Torso)는 자기 자신과 짝인가
    3. 교차 여부 도출 — CAPTURE 만 교차, UPLOAD·None·행 없음은 비교차
    4. 세션 비교의 집합 연산이 교차에서 옳은가 — 비대칭 포즈 시나리오
       (사용자 오른팔만 유효 + 레퍼런스 왼팔만 유효 → 교차면 비교 성립,
        비교차면 성립 안 함)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import part_pairing  # noqa: E402
from app.services.part_pairing import (  # noqa: E402
    is_cross_paired,
    mirror_class,
    reference_class_for,
)

failed: list[str] = []


def check(name: str, ok: bool, note: str = "") -> None:
    print(f"  [{'O' if ok else 'X'}] {name}" + (f"  — {note}" if note else ""))
    if not ok:
        failed.append(name)


print("1. 미러 매핑")
all_classes = set(part_pairing._MIRROR) | {"Torso", "Face_Neck", "Background"}
check(
    "mirror(mirror(x)) == x (대합)",
    all(mirror_class(mirror_class(c)) == c for c in all_classes),
)
check(
    "Left↔Right 쌍이 전부 양방향",
    all(part_pairing._MIRROR[v] == k for k, v in part_pairing._MIRROR.items()),
)
check("Torso 는 자기 자신과 짝", mirror_class("Torso") == "Torso")
check(
    "비교 대상 9부위 중 좌우쌍 8개가 매핑에 존재",
    len(part_pairing._MIRROR) == 8,
    f"{len(part_pairing._MIRROR)}개",
)

print("\n2. 교차 여부 도출 (사용자 사진 기준)")
check("CAPTURE → 교차", is_cross_paired({"capture_source": "CAPTURE"}))
check("UPLOAD → 비교차", not is_cross_paired({"capture_source": "UPLOAD"}))
check("capture_source 없음 → 비교차", not is_cross_paired({}))
check("사진 행 없음 → 비교차", not is_cross_paired(None))

print("\n3. 짝 선택")
check(
    "교차: 사용자 왼팔 ↔ 레퍼런스 오른팔",
    reference_class_for("Left_Upper_Arm", True) == "Right_Upper_Arm",
)
check(
    "비교차: 사용자 왼팔 ↔ 레퍼런스 왼팔",
    reference_class_for("Left_Upper_Arm", False) == "Left_Upper_Arm",
)
check("교차여도 Torso 는 Torso", reference_class_for("Torso", True) == "Torso")

print("\n4. 세션 비교 집합 연산 (라우트와 같은 수식)")
# 비대칭 시나리오: 레퍼런스는 왼팔을 들어 왼팔만 유효, 사용자는 (거울 매칭으로)
# 오른팔을 들어 오른팔만 유효. 몸통은 양쪽 다 유효.
ref_valid = {"Left_Upper_Arm", "Torso"}
user_valid = {"Right_Upper_Arm", "Torso"}

for cross, expect_both, label in (
    (True, {"Right_Upper_Arm", "Torso"}, "교차: 든 팔끼리 비교 성립"),
    (False, {"Torso"}, "비교차: 팔 비교 불성립(몸통만)"),
):
    ref_uf = {mirror_class(c) for c in ref_valid} if cross else ref_valid
    both = user_valid & ref_uf
    check(label, both == expect_both, f"both={sorted(both)}")

# 교차에서 reference_only 는 레퍼런스 자기 부위명으로 돌아와야 한다
ref_valid2 = {"Left_Upper_Arm", "Left_Lower_Arm", "Torso"}  # 전완은 레퍼런스만 유효
user_valid2 = {"Right_Upper_Arm", "Torso"}
ref_uf2 = {mirror_class(c) for c in ref_valid2}
ref_only = sorted(mirror_class(c) for c in (ref_uf2 - user_valid2))
check(
    "교차: reference_only 가 레퍼런스 부위명으로 표기",
    ref_only == ["Left_Lower_Arm"],
    f"{ref_only}",
)

print()
if failed:
    print(f"[X] 실패 {len(failed)}건: {', '.join(failed)}")
    sys.exit(1)
print("[O] part_pairing 규칙 전부 통과")
