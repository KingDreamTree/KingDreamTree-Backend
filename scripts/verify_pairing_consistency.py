#!/usr/bin/env python
"""세션 비교 API(A)와 부위 진단(B)이 **같은 짝**을 쓰는지 대조한다.

왜 이 검증이 따로 필요한가
    verify_part_pairing.py 는 part_pairing 모듈의 규칙 자체를 본다. 그런데 이번
    사고의 형태는 규칙이 틀리는 게 아니라 **한쪽만 규칙을 거치는 것**이다.
    세션 비교는 교차로 짝짓고 부위 진단은 같은 이름으로 짝지으면, 왼팔 카드에
    오른팔 근거가 붙는데 **에러는 나지 않는다.** 그 조용한 어긋남만 잡는다.

    handover-b-20260817.md §3-1 의 "검증" 항목을 코드로 옮긴 것이다.

    DB 없이 돈다 — 두 경로의 짝짓기 수식을 같은 입력에 태워 결과를 대조한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import part_pairing  # noqa: E402

FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"  [{'O' if ok else 'X'}] {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED += 1


#: 비대칭 포즈: 레퍼런스는 왼팔을 들었고, 거울을 보고 따라 한 사용자는 오른팔을 들었다.
#  두 사진 모두 그 팔만 유효하게 잡혔다고 본다.
REF_VALID = {"Right_Upper_Arm", "Torso"}      # 레퍼런스에서 유효한 부위
USER_VALID = {"Left_Upper_Arm", "Torso"}      # 사용자에서 유효한 부위


def route_pairs(cross: bool) -> set[tuple[str, str]]:
    """세션 비교 API(routes/segmentation.py)가 만드는 (사용자, 레퍼런스) 쌍."""
    ref_user_frame = {part_pairing.mirror_class(c) for c in REF_VALID} if cross else REF_VALID
    both = USER_VALID & ref_user_frame
    return {(c, part_pairing.reference_class_for(c, cross)) for c in both}


def diagnosis_pairs(cross: bool) -> set[tuple[str, str]]:
    """부위 진단(diagnosis_repo.build_comparison_context)이 만드는 쌍.

    같은 교집합 수식을 재현한다 — 사용자 부위명으로 순회하며 레퍼런스만 짝을 바꾼다.
    """
    pairs = set()
    for name in sorted(USER_VALID | {part_pairing.mirror_class(c) for c in REF_VALID}):
        ref_name = part_pairing.reference_class_for(name, cross)
        if name in USER_VALID and ref_name in REF_VALID:
            pairs.add((name, ref_name))
    return pairs


def main() -> int:
    print("세션 비교(A) ↔ 부위 진단(B) 짝짓기 대조\n")
    print("입력: 레퍼런스=왼팔 듦 / 사용자=거울 보고 오른팔 듦 (비대칭 포즈)")
    print(f"      레퍼런스 유효={sorted(REF_VALID)}  사용자 유효={sorted(USER_VALID)}\n")

    print("1. 교차 세션 (CAPTURE — 실시간 촬영)")
    a, b = route_pairs(True), diagnosis_pairs(True)
    check("두 경로의 쌍이 완전히 같다", a == b, f"세션비교={sorted(a)} 진단={sorted(b)}")
    check(
        "든 팔끼리 짝지어졌다 (사용자 왼팔 ↔ 레퍼런스 오른팔)",
        ("Left_Upper_Arm", "Right_Upper_Arm") in a,
        sorted(a),
    )

    print("\n2. 비교차 세션 (UPLOAD — 갤러리)")
    a, b = route_pairs(False), diagnosis_pairs(False)
    check("두 경로의 쌍이 완전히 같다", a == b, f"세션비교={sorted(a)} 진단={sorted(b)}")
    check(
        "팔은 비교 대상이 아니다 (서로 다른 팔이 유효)",
        not any("Arm" in u for u, _ in a),
        sorted(a),
    )

    print("\n3. 좌표계 규칙")
    pairs = route_pairs(True)
    check(
        "쌍의 첫 값(진단문에 쓰는 이름)은 전부 사용자 부위다",
        all(u in USER_VALID for u, _ in pairs),
        sorted(u for u, _ in pairs),
    )
    check(
        "쌍의 둘째 값은 전부 레퍼런스 부위다",
        all(r in REF_VALID for _, r in pairs),
        sorted(r for _, r in pairs),
    )

    print("\n" + ("[O] 두 경로가 같은 짝을 쓴다" if not FAILED else f"[X] {FAILED}건 불일치"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
