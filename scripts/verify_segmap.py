"""segmap 모듈을 실제 맵 샘플로 검증한다.

사용법:
    python scripts/verify_segmap.py <샘플 폴더>

샘플 폴더에 map.png / label_map.json / segmentation.json 이 있어야 한다
(담당 A의 scripts/smoke_e2e_segmentation.py --out 산출물).

⚠️ 맵 PNG 는 파일 그대로여야 한다. 메신저로 재전송하거나 편집 툴에서 다시
   저장하면 재압축돼 라벨 값이 섞인다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

from app.services import segmap  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(label: str, cond: bool, note: str = "") -> None:
    (passed if cond else failed).append(label)
    print(f"  [{'O' if cond else 'X'}] {label}" + (f"  — {note}" if note else ""))


def main(sample_dir: Path) -> int:
    label_map = json.loads((sample_dir / "label_map.json").read_text(encoding="utf-8"))
    seg = json.loads((sample_dir / "segmentation.json").read_text(encoding="utf-8"))
    map_bytes = (sample_dir / "map.png").read_bytes()

    print("=" * 68)
    print("segmap 검증")
    print("=" * 68)

    # ── 맵 로딩 ──────────────────────────────────────────────────────
    print("\n맵 파일")
    seg_map = segmap.load_map(map_bytes)
    check("8-bit 그레이스케일로 로드", seg_map.mode == "L", f"mode={seg_map.mode}")
    check(
        "크기가 명세와 일치",
        seg_map.size == (seg["map_width"], seg["map_height"]),
        f"{seg_map.size}",
    )

    counts = Counter(seg_map.get_flattened_data())
    check(
        "label_map 밖 픽셀 값 없음",
        all(str(v) in label_map for v in counts),
        f"고유값 {len(counts)}개",
    )

    # ── 라벨 대응 ────────────────────────────────────────────────────
    print("\n라벨 대응")
    name_to_value = segmap.invert_label_map(label_map)
    comparable = [p for p in seg["palette"] if p["is_comparable"]]
    check(
        "팔레트 class_name 이 전부 label_map 에 있음",
        all(p["class_name"] in name_to_value for p in comparable),
    )
    check(
        "label_value 가 label_map 과 일치",
        all(name_to_value[p["class_name"]] == p["label_value"] for p in comparable),
    )

    # ── 마스크 픽셀 수 ───────────────────────────────────────────────
    print("\n마스크 (맵 좌표계 픽셀 수 대조)")
    for p in comparable:
        values = segmap.part_label_values(p["class_name"], name_to_value)
        mask = segmap.class_mask(seg_map, values)
        on = sum(1 for v in mask.get_flattened_data() if v)
        # 의류 병합분이 있으면 더 클 수 있으므로 >= 로 본다
        check(
            f"{p['class_name']:18} {p['pixel_count']:6}px",
            on >= p["pixel_count"],
            f"마스크 {on}",
        )

    # ── 좌표 배율 ────────────────────────────────────────────────────
    print("\n좌표 배율")
    photo_size = (seg["photo_width"], seg["photo_height"])
    sx, sy = segmap.scales(photo_size, seg_map.size)
    check(
        "sx / sy 가 서로 다름 (종횡비 미보존)",
        abs(sx - sy) > 1e-6,
        f"sx={sx:.4f} sy={sy:.4f} 차이 {abs(sx - sy) / sy * 100:.1f}%",
    )
    torso_bb = next(p for p in comparable if p["class_name"] == "Torso")["bbox"]
    scaled = segmap.scale_bbox(torso_bb, sx, sy)
    wrong_x = round(torso_bb["x"] * sy)
    check(
        "bbox 변환이 단일 배율과 다름",
        scaled["x"] != wrong_x,
        f"올바름 x={scaled['x']} / 단일배율 x={wrong_x} ({abs(scaled['x'] - wrong_x)}px 차이)",
    )

    # ── 보간 금지 ────────────────────────────────────────────────────
    print("\n리사이즈 보간")
    orig_labels = set(counts)
    near = set(seg_map.resize(photo_size, Image.NEAREST).get_flattened_data())
    bili = set(seg_map.resize(photo_size, Image.BILINEAR).get_flattened_data())
    check("NEAREST 는 라벨 종류를 늘리지 않음", not (near - orig_labels), f"{len(near)}종")
    phantom = sorted(bili - orig_labels)
    check(
        "BILINEAR 은 없는 라벨을 만듦 (그래서 금지)",
        bool(phantom),
        f"{len(phantom)}종 생성: {[label_map.get(str(v)) for v in phantom[:4]]}",
    )

    # ── 하이라이트 ───────────────────────────────────────────────────
    print("\n하이라이트 생성")
    photo = Image.new("RGB", photo_size, (150, 140, 130))
    hi = segmap.build_highlight(photo, seg_map, "Torso", label_map)
    check("원본과 같은 크기", hi.size == photo_size, f"{hi.size}")
    check("JPEG 인코딩", len(segmap.encode_jpeg(hi)) > 0)

    print("\n" + "=" * 68)
    print(f"통과 {len(passed)} / 실패 {len(failed)}")
    for f in failed:
        print(f"  [X] {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1])))
