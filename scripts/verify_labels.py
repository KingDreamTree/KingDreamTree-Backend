"""Sapiens2 라벨 인덱스 ↔ 부위 매핑 실측 검증.

config.json의 id2label이 "LABEL_0"..."LABEL_28" 플레이스홀더라, 어느 픽셀 값이
어느 부위인지 모델 파일만으로는 알 수 없다. 사람 사진 한 장을 돌려서
해부학적으로 말이 되는 후보를 고른다.

사용법:
    python scripts/verify_labels.py --image path/to/person.jpg
    python scripts/verify_labels.py --image p.jpg --size 0.4b --out out/

⚠️ 사진 조건
    * 정면을 보고 서 있을 것  ← 좌우 판정이 여기에 의존한다
    * 상하체가 모두 나올 것
    * 팔다리가 드러날 것 (긴팔·긴바지면 팔다리 클래스가 안 잡혀 검증이 약해진다)

산출물
    <out>/overlay_<후보>.png   원본 위에 부위별 색칠 — 눈으로 확인용
    콘솔 리포트                 후보별 해부학 점검 결과
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

# 후보 구분용 색 (body_part.color_hex 와 별개 — 여기서는 전 클래스를 다 칠한다)
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
        name = names[value]
        cx, cy = centroid(labels == value)
        out[name] = {"index": value, "pixels": int(count), "cx": cx, "cy": cy}
    return out


def run_checks(stats: dict[str, dict], height: int) -> list[tuple[str, bool | None, str]]:
    """해부학적으로 말이 되는지 점검. (설명, 통과여부, 비고)

    통과여부 None = 해당 클래스가 사진에 없어서 판정 불가.
    """
    checks: list[tuple[str, bool | None, str]] = []

    def get(name: str) -> dict | None:
        s = stats.get(name)
        # 너무 작은 영역은 노이즈로 보고 판정에서 제외
        return s if s and s["pixels"] > 200 else None

    def vertical(upper: str, lower: str, label: str) -> None:
        a, b = get(upper), get(lower)
        if not a or not b:
            checks.append((label, None, f"{upper} 또는 {lower} 없음"))
            return
        ok = a["cy"] < b["cy"]
        checks.append((label, ok, f"{upper} y={a['cy']:.0f} vs {lower} y={b['cy']:.0f}"))

    vertical("Hair", "Torso", "머리가 몸통보다 위")
    vertical("Face_Neck", "Torso", "얼굴·목이 몸통보다 위")
    vertical("Torso", "Left_Upper_Leg", "몸통이 허벅지보다 위")
    vertical("Left_Upper_Arm", "Left_Lower_Arm", "왼팔 상완이 전완보다 위")
    vertical("Right_Upper_Arm", "Right_Lower_Arm", "오른팔 상완이 전완보다 위")
    vertical("Left_Upper_Leg", "Left_Lower_Leg", "왼쪽 허벅지가 종아리보다 위")
    vertical("Right_Upper_Leg", "Right_Lower_Leg", "오른쪽 허벅지가 종아리보다 위")

    # ⚠️ 정면 기준. 피사체의 "왼쪽"은 이미지에서는 오른쪽(x가 큼)에 나온다.
    for left, right, label in (
        ("Left_Upper_Arm", "Right_Upper_Arm", "좌우 상완 배치 (정면 기준)"),
        ("Left_Upper_Leg", "Right_Upper_Leg", "좌우 허벅지 배치 (정면 기준)"),
    ):
        a, b = get(left), get(right)
        if not a or not b:
            checks.append((label, None, f"{left} 또는 {right} 없음"))
            continue
        ok = a["cx"] > b["cx"]
        checks.append((label, ok, f"{left} x={a['cx']:.0f} vs {right} x={b['cx']:.0f}"))

    # 발/신발은 화면 아래쪽
    for name in ("Left_Foot", "Right_Foot", "Left_Shoe", "Right_Shoe"):
        s = get(name)
        if s:
            ok = s["cy"] > height * 0.6
            checks.append((f"{name}가 화면 아래쪽", ok, f"y={s['cy']:.0f} / 높이 {height}"))

    return checks


def render_overlay(image: Image.Image, labels: np.ndarray, names: tuple[str, ...]) -> Image.Image:
    """원본 위에 부위별 색칠. 눈으로 확인하는 게 최종 판정이다."""
    h, w = labels.shape
    base = image.resize((w, h), Image.LANCZOS).convert("RGB")

    color = np.zeros((h, w, 3), dtype=np.uint8)
    for value in np.unique(labels):
        if value == 0:
            continue
        color[labels == value] = PALETTE[int(value) % len(PALETTE)]

    alpha = np.where(labels[..., None] == 0, 0.0, 0.55)
    blended = (np.asarray(base) * (1 - alpha) + color * alpha).astype(np.uint8)
    return Image.fromarray(blended)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sapiens2 라벨 매핑 실측 검증")
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

    if num_classes != 29:
        print(f"  ⚠️ 29개가 아닙니다 ({num_classes}). 후보 매핑이 안 맞을 수 있습니다.")

    results: dict[str, int] = {}

    for cand, names in sapiens_labels.CANDIDATES.items():
        if len(names) != num_classes:
            print(f"\n[건너뜀] 후보 '{cand}' — 길이 {len(names)} ≠ 클래스 {num_classes}")
            continue

        print()
        print("=" * 70)
        print(f"후보: {cand}")
        print("=" * 70)

        stats = analyze(labels, names)
        print("  검출된 클래스 (픽셀 많은 순):")
        for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["pixels"])[:12]:
            print(
                f"    {s['index']:>3}  {name:<18} {s['pixels']:>8,}px  "
                f"중심 ({s['cx']:.0f}, {s['cy']:.0f})"
            )

        checks = run_checks(stats, h)
        passed = sum(1 for _, ok, _ in checks if ok is True)
        failed = sum(1 for _, ok, _ in checks if ok is False)
        skipped = sum(1 for _, ok, _ in checks if ok is None)

        print()
        print("  해부학 점검:")
        for label, ok, note in checks:
            mark = "O" if ok is True else ("X" if ok is False else "-")
            print(f"    [{mark}] {label:<28} {note}")
        print(f"  → 통과 {passed} / 실패 {failed} / 판정불가 {skipped}")
        results[cand] = passed - failed * 2  # 실패에 가중치

        overlay = render_overlay(image, labels, names)
        path = out_dir / f"overlay_{cand}.png"
        overlay.save(path)
        print(f"  오버레이: {path}")

    print()
    print("=" * 70)
    print("결론")
    print("=" * 70)
    if not results:
        print("  판정 가능한 후보가 없습니다.")
        return 1

    best = max(results, key=lambda k: results[k])
    ordered = sorted(results.items(), key=lambda kv: -kv[1])
    for cand, score in ordered:
        print(f"  {cand:<10} 점수 {score}")

    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        print()
        print("  ⚠️ 점수가 같습니다. 오버레이 이미지를 직접 보고 판단하세요.")
        print("     특히 팔다리 색이 좌우로 뒤바뀌지 않았는지 확인할 것.")
        return 1

    print()
    print(f"  → '{best}' 가 유력합니다.")
    print()
    print("  ⚠️ 점수만 믿지 말고 오버레이를 눈으로 확인하세요.")
    print("     확인되면 app/services/sapiens_labels.py 에 반영:")
    print(f'       VERIFIED_ORDER = "{best}"')
    print(f'       VERIFIED_WITH = "{segmenter.model_version(args.size)}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
