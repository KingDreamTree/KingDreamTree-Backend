"""라벨 매핑 점검 — 사람 사진 한 장으로.

클래스 목록 자체는 공식 문서에서 온다 (app/services/sapiens_labels.py).
이 스크립트는 그 목록이 **실제 추론 결과와 맞물리는지** 확인한다.

언제 쓰나
    * 새 체크포인트로 갈아탈 때 — 그 모델의 클래스 순서가 같은지
    * 결과가 이상할 때 — 부위가 뒤바뀐 건지 모델이 못 잡은 건지 가르기 위해

⚠️ **좌우는 문서에 안 적혀 있다.** Left/Right 가 피사체 기준인지 화면 기준인지는
   실제로 그려봐야 안다. 뒤집히면 진단이 통째로 반대가 되고 에러는 안 난다.
   이 스크립트의 좌우 점검과 오버레이가 그걸 잡기 위한 것이다.

사용법:
    python scripts/verify_labels.py --image path/to/person.jpg
    python scripts/verify_labels.py --image p.jpg --size 0.4b --out out/

⚠️ 사진 조건
    * 정면을 보고 서 있을 것  ← 좌우 판정이 여기에 의존한다
    * 상하체가 모두 나올 것
    * 팔다리가 드러날 것 (긴팔·긴바지면 팔다리 클래스가 안 잡혀 점검이 약해진다)

산출물
    <out>/overlay.png   원본 위에 부위별 색칠 + 이름 — 눈으로 확인용
    콘솔 리포트          해부학 점검 결과
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import sapiens_labels, segmenter  # noqa: E402

# 부위 구분용 색 (body_part.color_hex 와 별개 — 여기서는 전 클래스를 다 칠한다)
PALETTE = [
    (0, 0, 0),
    (76, 110, 245),
    (247, 103, 7),
    (255, 169, 77),
    (47, 158, 68),
    (105, 219, 124),
    (174, 62, 201),
    (218, 119, 242),
    (224, 49, 49),
    (255, 135, 135),
    (32, 201, 151),
    (255, 212, 59),
    (132, 94, 247),
    (240, 101, 149),
    (34, 139, 230),
    (250, 176, 5),
    (12, 166, 120),
    (190, 75, 219),
    (255, 146, 43),
    (81, 207, 102),
    (255, 107, 107),
    (77, 171, 247),
    (204, 93, 232),
    (255, 224, 102),
    (99, 230, 190),
    (255, 168, 168),
    (145, 167, 255),
    (238, 190, 250),
    (150, 150, 150),
]

#: 얼굴 세부 클래스 — 사람 픽셀의 몇 %를 넘을 수 없다.
#  입술이 몸의 3%를 차지하는 사진은 존재하지 않는다.
TINY_CLASSES = ("Lower_Lip", "Upper_Lip", "Lower_Teeth", "Upper_Teeth", "Tongue", "Eyeglass")
TINY_MAX_RATIO = 0.03

#: 좌우 대칭 쌍 — 정면 사진에서 픽셀 수가 이 배수를 넘게 차이나면 이상하다.
SYMMETRIC_PAIRS = (
    ("Left_Hand", "Right_Hand"),
    ("Left_Foot", "Right_Foot"),
    ("Left_Shoe", "Right_Shoe"),
    ("Left_Upper_Arm", "Right_Upper_Arm"),
    ("Left_Lower_Arm", "Right_Lower_Arm"),
    ("Left_Upper_Leg", "Right_Upper_Leg"),
    ("Left_Lower_Leg", "Right_Lower_Leg"),
)
SYMMETRY_MAX_RATIO = 3.0


def centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def analyze(labels: np.ndarray, names: tuple[str, ...]) -> dict[str, dict]:
    """클래스별 픽셀 수 / 중심점."""
    out: dict[str, dict] = {}
    present, counts = np.unique(labels, return_counts=True)
    for value, count in zip(present.tolist(), counts.tolist()):
        if value >= len(names):
            continue
        cx, cy = centroid(labels == value)
        out[names[value]] = {"index": value, "pixels": int(count), "cx": cx, "cy": cy}
    return out


def run_checks(
    stats: dict[str, dict], height: int, person_pixels: int
) -> list[tuple[str, bool | None, str, bool]]:
    """해부학적으로 말이 되는지 점검. (설명, 통과여부, 비고, 결정적인가)

    통과여부 None = 해당 클래스가 사진에 없어서 판정 불가.
    """
    checks: list[tuple[str, bool | None, str, bool]] = []

    def get(name: str) -> dict | None:
        s = stats.get(name)
        # 너무 작은 영역은 노이즈로 보고 판정에서 제외
        return s if s and s["pixels"] > 200 else None

    # ── 크기 상식 — 사진에 팔다리가 안 나와도 판정 가능 ────────────────────
    for name in TINY_CLASSES:
        s = stats.get(name)
        if not s or not person_pixels:
            continue
        ratio = s["pixels"] / person_pixels
        checks.append(
            (
                f"{name}가 충분히 작은가",
                ratio <= TINY_MAX_RATIO,
                f"사람 픽셀의 {ratio * 100:.1f}% (상한 {TINY_MAX_RATIO * 100:.0f}%)",
                True,
            )
        )

    # ── 좌우 대칭 ──────────────────────────────────────────────────────────
    for left, right in SYMMETRIC_PAIRS:
        a, b = get(left), get(right)
        if not a or not b:
            continue
        big, small = max(a["pixels"], b["pixels"]), min(a["pixels"], b["pixels"])
        ratio = big / small if small else float("inf")
        checks.append(
            (
                f"{left}/{right} 크기 대칭",
                ratio <= SYMMETRY_MAX_RATIO,
                f"{a['pixels']:,} vs {b['pixels']:,} ({ratio:.1f}배)",
                False,
            )
        )

    def vertical(upper: str, lower: str, label: str) -> None:
        a, b = get(upper), get(lower)
        if not a or not b:
            checks.append((label, None, f"{upper} 또는 {lower} 없음", False))
            return
        checks.append(
            (label, a["cy"] < b["cy"], f"{upper} y={a['cy']:.0f} vs {lower} y={b['cy']:.0f}", False)
        )

    vertical("Upper_Clothing", "Lower_Clothing", "상의가 하의보다 위")
    vertical("Hair", "Torso", "머리가 몸통보다 위")
    vertical("Face_Neck", "Torso", "얼굴·목이 몸통보다 위")
    vertical("Torso", "Left_Upper_Leg", "몸통이 허벅지보다 위")
    vertical("Left_Upper_Arm", "Left_Lower_Arm", "왼팔 상완이 전완보다 위")
    vertical("Right_Upper_Arm", "Right_Lower_Arm", "오른팔 상완이 전완보다 위")
    vertical("Left_Upper_Leg", "Left_Lower_Leg", "왼쪽 허벅지가 종아리보다 위")
    vertical("Right_Upper_Leg", "Right_Lower_Leg", "오른쪽 허벅지가 종아리보다 위")

    # ⚠️ 여기가 이 스크립트의 핵심이다. Left/Right 는 **피사체 기준**이라
    #    정면 사진에서 피사체의 왼쪽은 이미지의 오른쪽(x가 큼)에 나온다.
    #    문서에 안 적힌 유일한 항목이고, 틀리면 진단이 통째로 좌우 반대가 된다.
    for left, right, label in (
        ("Left_Upper_Arm", "Right_Upper_Arm", "좌우 상완 배치 (피사체 기준)"),
        ("Left_Upper_Leg", "Right_Upper_Leg", "좌우 허벅지 배치 (피사체 기준)"),
    ):
        a, b = get(left), get(right)
        if not a or not b:
            checks.append((label, None, f"{left} 또는 {right} 없음", True))
            continue
        checks.append(
            (label, a["cx"] > b["cx"], f"{left} x={a['cx']:.0f} vs {right} x={b['cx']:.0f}", True)
        )

    # 발/신발/양말은 화면 아래쪽 — 발이 화면 한가운데 있을 수 없다
    for name in ("Left_Foot", "Right_Foot", "Left_Shoe", "Right_Shoe", "Left_Sock", "Right_Sock"):
        s = get(name)
        if s:
            checks.append(
                (
                    f"{name}가 화면 아래쪽",
                    s["cy"] > height * 0.6,
                    f"y={s['cy']:.0f} / 높이 {height}",
                    True,
                )
            )

    # 머리카락·얼굴은 화면 위쪽
    for name in ("Hair", "Face_Neck"):
        s = get(name)
        if s:
            checks.append(
                (
                    f"{name}가 화면 위쪽",
                    s["cy"] < height * 0.6,
                    f"y={s['cy']:.0f} / 높이 {height}",
                    True,
                )
            )

    return checks


def _font(size: int):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_overlay(image: Image.Image, labels: np.ndarray, names: tuple[str, ...]) -> Image.Image:
    """원본 위에 부위별 색칠 + **부위 이름을 직접 그린다**.

    ⚠️ 색만 칠하면 좌우가 뒤집혔는지 알 수 없다. "왼팔 자리에 Left_Upper_Arm 이
       적혀 있는가"를 볼 수 있어야 판정이 된다.
    """
    from PIL import ImageDraw

    h, w = labels.shape
    base = image.resize((w, h), Image.LANCZOS).convert("RGB")

    color = np.zeros((h, w, 3), dtype=np.uint8)
    for value in np.unique(labels):
        if value == 0:
            continue
        color[labels == value] = PALETTE[int(value) % len(PALETTE)]

    alpha = np.where(labels[..., None] == 0, 0.0, 0.55)
    blended = Image.fromarray((np.asarray(base) * (1 - alpha) + color * alpha).astype(np.uint8))

    draw = ImageDraw.Draw(blended)
    font = _font(max(13, w // 45))

    present, counts = np.unique(labels, return_counts=True)
    for value, count in zip(present.tolist(), counts.tolist()):
        # 너무 작은 영역은 글자만 겹쳐서 지저분해진다
        if value == 0 or value >= len(names) or count < 400:
            continue
        cx, cy = centroid(labels == value)
        text = f"{value} {names[value]}"
        box = draw.textbbox((cx, cy), text, font=font, anchor="mm")
        draw.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=(0, 0, 0))
        draw.text((cx, cy), text, font=font, fill=(255, 255, 255), anchor="mm")

    return blended


def main() -> int:
    ap = argparse.ArgumentParser(description="라벨 매핑 점검")
    ap.add_argument("--image", required=True, help="정면·전신 사람 사진")
    ap.add_argument("--size", default=None, help="백본 크기 (기본: .env의 SAPIENS_SIZE)")
    ap.add_argument("--out", default="out", help="오버레이 저장 폴더")
    args = ap.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"[X] 사진을 찾을 수 없습니다: {img_path}")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("환경")
    print("=" * 70)
    for k, v in segmenter.describe_environment().items():
        print(f"  {k:<14}: {v}")

    image = segmenter.load_rgb(img_path.read_bytes())
    print(f"  {'입력 사진':<14}: {image.size[0]}x{image.size[1]}")

    print()
    print("추론 중...")
    labels, num_classes, ms = segmenter.infer_labels(image, args.size)
    h, w = labels.shape
    print(f"  맵 {w}x{h}, 클래스 {num_classes}개, {ms}ms")
    print(f"  검출된 인덱스: {sorted(np.unique(labels).tolist())}")

    names = sapiens_labels.LABEL_NAMES
    if num_classes != len(names):
        print()
        print(f"  [X] 모델이 {num_classes}클래스인데 라벨 목록은 {len(names)}개입니다.")
        print("      다른 모델일 수 있습니다 — 그 모델 문서에서 클래스 목록을 확인하세요.")
        return 1

    stats = analyze(labels, names)
    person_pixels = int(np.count_nonzero(labels != 0))

    print()
    print("=" * 70)
    print("검출된 클래스 (픽셀 많은 순)")
    print("=" * 70)
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["pixels"])[:14]:
        print(
            f"  {s['index']:>3}  {name:<18} {s['pixels']:>8,}px  "
            f"중심 ({s['cx']:.0f}, {s['cy']:.0f})"
        )

    checks = run_checks(stats, h, person_pixels)
    passed = sum(1 for _, ok, _, _ in checks if ok is True)
    failed = [c for c in checks if c[1] is False]
    skipped = sum(1 for _, ok, _, _ in checks if ok is None)

    print()
    print("=" * 70)
    print("해부학 점검")
    print("=" * 70)
    for label, ok, note, decisive in checks:
        mark = "O" if ok is True else ("X" if ok is False else "-")
        heavy = " ←결정적" if ok is False and decisive else ""
        print(f"  [{mark}] {label:<32} {note}{heavy}")
    print(f"  → 통과 {passed} / 실패 {len(failed)} / 판정불가 {skipped}")

    overlay = render_overlay(image, labels, names)
    path = out_dir / "overlay.png"
    overlay.save(path)

    print()
    print("=" * 70)
    if failed:
        print("결론: 매핑이 이 모델과 안 맞는 것으로 보입니다")
        print("=" * 70)
        for label, _, note, decisive in failed:
            print(f"  [X] {label} — {note}{' (결정적)' if decisive else ''}")
        print()
        print("  모델을 바꾼 게 아니라면 사진 조건을 먼저 의심하세요")
        print("  (긴팔·긴바지, 비스듬한 자세, 잘린 프레임).")
    else:
        print("결론: 문제 없음")
        print("=" * 70)

    print()
    print(f"  오버레이: {path}")
    print("  ⚠️ 좌우는 눈으로 확인하세요 — 피사체의 왼팔에 Left_Upper_Arm 이 적혀 있어야 합니다.")
    print("     (정면 사진에서 피사체의 왼팔은 이미지 오른쪽에 나옵니다)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
