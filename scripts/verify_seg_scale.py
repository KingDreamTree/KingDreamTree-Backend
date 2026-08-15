"""부위 유효 판정이 **촬영 거리에 흔들리지 않는가.**

    python scripts/verify_seg_scale.py

⚠️ 같은 사람을 멀리서 찍으면 모든 부위의 픽셀 수가 함께 줄어든다. 유효/무효가
   절대 픽셀로 갈리면 **옷·자세가 똑같아도 거리만으로 부위가 죽는다.**
   이 서비스는 레퍼런스와 사용자 두 장을 서로 비교하므로, 그 왜곡이 곧바로
   "무엇을 비교할지"를 바꾼다.

실측(2026-08-15) — 실제 두 사진에서 seg_min_pixels=1500 이 각각
   레퍼런스 몸의 0.80% · 사용자 몸의 0.34% 였다. 같은 값이 2.4배 다른 의미였고,
   멀리 찍은 쪽에서는 비율 기준(0.5%)보다 엄격해 그쪽만 부위가 죽었다.
   그래서 절대 픽셀은 노이즈 바닥(200)으로 내리고 비율을 진짜 관문으로 삼았다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.segmenter import compute_parts  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failed: list[str] = []


def check(label: str, cond: bool, note: str = "") -> None:
    print(f"  {PASS if cond else FAIL} {label}{(' — ' + note) if note else ''}")
    if not cond:
        _failed.append(label)


def body(scale: float) -> np.ndarray:
    """같은 비례의 몸을 scale 배로 그린다. 1.0 = 가까이, 0.4 = 멀리."""
    canvas = np.zeros((1024, 1024), dtype=np.uint8)

    def box(value: int, cx: float, cy: float, w: float, h: float) -> None:
        # 화면 중앙 기준 비율 좌표 → scale 을 곱해 크기만 바꾼다
        x0 = int(512 + (cx - w / 2) * scale)
        x1 = int(512 + (cx + w / 2) * scale)
        y0 = int(512 + (cy - h / 2) * scale)
        y1 = int(512 + (cy + h / 2) * scale)
        canvas[y0:y1, x0:x1] = value

    box(1, 0, -100, 300, 400)  # Torso
    box(2, -220, -100, 90, 300)  # Left_Upper_Arm
    box(3, 220, -100, 90, 300)  # Right_Upper_Arm
    # ⚠️ 이 부위 크기가 이 테스트의 핵심이다. **실측값에 맞췄다** — 레퍼런스 사진의
    #    Right_Lower_Leg 가 2,175px / 몸의 1.16% 였다. 비율로는 넉넉히 통과하지만
    #    2.5배 멀어지면 약 350px 이라 옛 절대 기준(1500)에는 걸린다.
    #    부위를 더 크게 잡으면 경계를 안 건드려 테스트가 아무것도 못 잡는다.
    box(4, -240, 220, 40, 57)  # Left_Lower_Arm — 몸의 약 1.2%
    return canvas


LABEL_MAP = {
    "1": "Torso",
    "2": "Left_Upper_Arm",
    "3": "Right_Upper_Arm",
    "4": "Left_Lower_Arm",
}
COMPARABLE = set(LABEL_MAP.values())


def validity(scale: float) -> dict[str, bool]:
    labels = body(scale)
    person = int(np.count_nonzero(labels))
    parts = compute_parts(labels, LABEL_MAP, person, COMPARABLE)
    return {p.class_name: p.is_valid for p in parts}


def main() -> int:
    print("부위 유효 판정의 거리 불변성\n")

    near = validity(1.0)
    far = validity(0.4)  # 2.5배 멀리 = 면적 1/6.25 (FRAMING_HARD_MIN 이 허용하는 한계)

    print(f"  가까이: {near}")
    print(f"  멀리  : {far}\n")

    check(
        "거리가 2.5배 달라도 유효 판정이 같다",
        near == far,
        f"차이: { {k: (near[k], far[k]) for k in near if near.get(k) != far.get(k)} }",
    )
    check("가까이서 전 부위 유효", all(near.values()), str(near))
    check("멀리서도 전 부위 유효", all(far.values()), str(far))

    # 진짜 작은 것은 여전히 걸러야 한다 — 불변성이 둔감함이 되면 안 된다.
    labels = body(1.0)
    labels[0:12, 0:12] = 4  # Left_Lower_Arm 을 144px 짜리 얼룩으로 대체
    labels[labels == 4] = 4
    tiny = np.zeros_like(labels)
    tiny[labels == 1] = 1
    tiny[0:10, 0:10] = 4  # 100px
    person = int(np.count_nonzero(tiny))
    parts = {p.class_name: p for p in compute_parts(tiny, LABEL_MAP, person, COMPARABLE)}
    check(
        "100px 짜리 얼룩은 여전히 무효",
        not parts["Left_Lower_Arm"].is_valid,
        parts["Left_Lower_Arm"].invalid_reason or "유효로 통과함",
    )

    print()
    if _failed:
        print(f"실패 {len(_failed)}건: {', '.join(_failed)}")
        return 1
    print("문제 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
