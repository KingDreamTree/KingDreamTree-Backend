"""거울 사진 랜드마크 되돌리기 검증.

⚠️ 이 프로젝트에서 좌우 반전은 **에러 없이** 진단을 통째로 뒤집는 사고다.
   pose.unmirror_landmarks 가 맞게 도는지 눈으로 확인할 수 있게 남겨둔다.

사용법:
    python scripts/verify_pose_mirror.py

무엇을 확인하나
    거울로 찍은 사진에서 MediaPipe가 내놓는 랜드마크를 만들어 놓고,
    unmirror_landmarks 를 태우면 비반전 원본과 정확히 같아지는지 본다.
    그리고 "x만 뒤집는" 흔한 실수가 실제로 틀린다는 것도 같이 보여준다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import pose  # noqa: E402


def build_real() -> list[dict]:
    """비반전 정면 사진에서 나올 법한 랜드마크.

    정면 기준 피사체의 왼쪽은 이미지에서 x가 큰 쪽이다.
    좌/우 이름표가 제대로 맞바뀌는지 보려면 y가 서로 달라야 한다.
    """
    lms = [{"index": i, "x": 0.5, "y": i / 100, "z": 0.0, "visibility": 0.9} for i in range(33)]
    lms[11].update(x=0.70, y=0.30)  # left_shoulder
    lms[12].update(x=0.30, y=0.35)  # right_shoulder
    return pose.parse_landmarks(json.dumps(lms))


def as_mediapipe_sees_mirrored(real: list[dict]) -> list[dict]:
    """거울 사진에 MediaPipe를 돌렸을 때 나올 값을 만든다.

    화면이 뒤집히고, MediaPipe는 정면 사람으로 보고 'x가 큰 쪽'을 left라 부르므로
    이름표까지 뒤바뀐 상태로 나온다.
    """
    out = [{**lm, "x": 1.0 - lm["x"]} for lm in real]
    for a, b in pose.LR_PAIRS:
        out[a], out[b] = out[b], out[a]
    for i, lm in enumerate(out):
        lm["index"] = i
    return out


def show(title: str, s: list[dict]) -> None:
    print(
        f"  {title:24} left_shoulder=(x{s[11]['x']:.2f}, y{s[11]['y']:.2f})  "
        f"right_shoulder=(x{s[12]['x']:.2f}, y{s[12]['y']:.2f})"
    )


def main() -> int:
    real = build_real()
    mirrored = as_mediapipe_sees_mirrored(real)
    restored = pose.unmirror_landmarks(mirrored)

    print("어깨 랜드마크로 본 변화")
    show("실제(비반전)", real)
    show("거울 사진에서 받은 값", mirrored)
    show("되돌린 뒤", restored)
    print()
    print("  ⚠️ 거울 쪽 left_shoulder의 y가 실제 right_shoulder의 y와 같다.")
    print("     x만 봐서는 멀쩡해 보이는 이유이고, 이게 조용히 망가지는 지점이다.")
    print()

    exact = all(
        abs(restored[i][k] - real[i][k]) < 1e-9
        for i in range(33)
        for k in ("x", "y", "z", "visibility")
    )
    indexed = all(restored[i]["index"] == i for i in range(33))

    # x만 뒤집는 흔한 실수 — 좌표는 맞는데 이름표가 반대쪽에 남는다
    naive = [{**lm, "x": 1.0 - lm["x"]} for lm in mirrored]
    naive_wrong = any(abs(naive[i]["y"] - real[i]["y"]) > 1e-9 for i in range(33))

    checks = (
        ("되돌린 값이 원본과 정확히 일치", exact),
        ("index가 배열 위치와 일치", indexed),
        ("x만 뒤집으면 틀린다(대조군)", naive_wrong),
    )
    for label, ok in checks:
        print(f"  [{'O' if ok else 'X'}] {label}")

    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
