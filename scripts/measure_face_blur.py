"""얼굴 블러가 결과를 바꾸는가 — 선행 실측 (이슈 3 "⚠️ 선행 검증").

    python scripts/measure_face_blur.py --pairs photos/123.jpg:photos/456.jpg photos/reference.jpg:photos/mybody.jpg
    python scripts/measure_face_blur.py --pairs ... --screen        # 스크리닝(OpenAI)까지 비교 (쌍당 GPT 2회)
    python scripts/measure_face_blur.py --pairs ... --out out/face-blur   # 블러본·맵 저장해서 눈으로 확인

무엇을 재나 (사진마다: 원본 vs 얼굴 블러본)
  1. 사피엔스 세그 — 비교 대상 9부위의 pixel_count / area_ratio / is_valid 가 같은가,
     person_pixel_count(분모)가 얼마나 바뀌나, 부위별 라벨 IoU
  2. (--screen) 사용자 사진 스크리닝 판정 — suitable / reason / rule 이 같은가
     ⚠️ 프롬프트가 "머리와 발끝은 보지 마라"고 하지만 blurry 관찰이 얼굴 블러를 오인할 수 있어 본다

얼굴 위치는 **원본 세그의 Face_Neck·Hair 라벨 bbox** 로 잡는다 (프론트는 포즈 랜드마크로
잡지만 여기엔 MediaPipe 가 없다 — 위치만 맞으면 픽셀 처리는 같다). 박스를 15% 키우고
가우시안 블러(반지름 = 박스 짧은 변 / 6)를 건다. 크롭은 하지 않는다 (설계 §7).

판정 기준 (보고서에 그대로 옮긴다)
  · 비교 대상 부위의 is_valid 가 하나라도 바뀌면 → "결과가 다르다"
  · area_ratio 상대 차이 최대값 < 3% 이고 IoU ≥ 0.95 → "같다"로 본다 (JPEG 재인코딩 잡음 수준)
  · person_pixel_count 변화 = 얼굴이 배경으로 빠진 정도. 기준 사진도 같이 블러하면 상쇄된다

⚠️ GPU + 1b 가중치 필요 (SAPIENS_* 는 .env). --screen 은 OpenAI 요금이 나간다.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import db, images, photo_screening, segmap, segmenter  # noqa: E402

FACE_CLASSES = ("Face_Neck", "Hair")


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def face_box(
    labels: np.ndarray,
    label_map: dict[str, str],
    img_size: tuple[int, int],
    mode: str = "head",
) -> tuple[int, int, int, int] | None:
    """얼굴 박스 → 원본 픽셀 좌표.

    head : Face_Neck·Hair 픽셀 bbox 를 15% 키운 것 — 목·어깨 윗부분까지 들어간다 (넓음)
    face : 위 박스에서 **머리 부분만** — Hair 위쪽부터 Face_Neck 높이의 60% 지점까지,
           확장 없음. 프론트가 포즈 랜드마크(눈·코·귀)로 잡는 박스에 가깝다
    """
    inv = {name: int(v) for v, name in label_map.items()}
    mask = np.zeros(labels.shape, dtype=bool)
    for c in FACE_CLASSES:
        if c in inv:
            mask |= labels == inv[c]
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    mh, mw = labels.shape
    w, h = img_size
    sx, sy = w / mw, h / mh
    x0, x1 = xs.min() * sx, (xs.max() + 1) * sx
    y0, y1 = ys.min() * sy, (ys.max() + 1) * sy
    if mode == "face":
        # 목 부분을 뺀다 — 세로는 위에서 60% 까지, 가로는 그 구간의 실제 폭만
        y_cut = y0 + (y1 - y0) * 0.6
        rows = ys * sy < y_cut
        if rows.any():
            x0, x1 = xs[rows].min() * sx, (xs[rows].max() + 1) * sx
        return (int(x0), int(y0), int(min(w, x1)), int(min(h, y_cut)))
    pad_x, pad_y = (x1 - x0) * 0.15, (y1 - y0) * 0.15
    return (
        int(max(0, x0 - pad_x)),
        int(max(0, y0 - pad_y)),
        int(min(w, x1 + pad_x)),
        int(min(h, y1 + pad_y)),
    )


def blur_face(img: Image.Image, box: tuple[int, int, int, int], divisor: int = 6) -> Image.Image:
    """박스 안을 가우시안 블러. 반지름 = 짧은 변 / divisor (6 = 강함, 12 = 약함)."""
    out = img.copy()
    region = out.crop(box)
    radius = max(3, min(box[2] - box[0], box[3] - box[1]) // divisor)
    out.paste(region.filter(ImageFilter.GaussianBlur(radius)), box)
    return out


def run_seg(jpeg: bytes, comparable: set[str], master: set[str]):
    result = segmenter.segment(jpeg, comparable=comparable, master_class_names=master)
    labels = np.array(segmap.load_map(result.map_png))
    return result, labels


def compare(orig, orig_labels, blur, blur_labels, comparable: set[str]) -> dict[str, Any]:
    by_o = {p.class_name: p for p in orig.parts}
    by_b = {p.class_name: p for p in blur.parts}
    inv = {name: int(v) for v, name in orig.label_map.items()}
    rows = []
    valid_changed = []
    max_rel = 0.0
    min_iou = 1.0
    for name in sorted(comparable):
        po, pb = by_o.get(name), by_b.get(name)
        if po is None and pb is None:
            continue
        vo, vb = bool(po and po.is_valid), bool(pb and pb.is_valid)
        if vo != vb:
            valid_changed.append(f"{name}: {vo}→{vb}")
        ao, ab = (po.area_ratio if po else 0.0), (pb.area_ratio if pb else 0.0)
        rel = abs(ab - ao) / ao if ao else (0.0 if ab == 0 else 1.0)
        max_rel = max(max_rel, rel)
        lv = inv.get(name)
        iou = None
        if lv is not None and orig_labels.shape == blur_labels.shape:
            a, b = orig_labels == lv, blur_labels == lv
            union = (a | b).sum()
            iou = float((a & b).sum() / union) if union else 1.0
            min_iou = min(min_iou, iou)
        rows.append(
            {
                "part": name,
                "px": (po.pixel_count if po else 0, pb.pixel_count if pb else 0),
                "area_ratio": (round(ao, 4), round(ab, 4)),
                "rel_diff_pct": round(rel * 100, 2),
                "valid": (vo, vb),
                "iou": None if iou is None else round(iou, 3),
            }
        )
    person_delta = (blur.person_pixel_count - orig.person_pixel_count) / max(1, orig.person_pixel_count)
    same = not valid_changed and max_rel < 0.03 and min_iou >= 0.95
    return {
        "rows": rows,
        "valid_changed": valid_changed,
        "max_area_rel_diff_pct": round(max_rel * 100, 2),
        "min_iou": round(min_iou, 3),
        "person_pixels": (orig.person_pixel_count, blur.person_pixel_count),
        "person_delta_pct": round(person_delta * 100, 2),
        "same": same,
    }


async def screen_pair(user_jpeg: bytes, ref_jpeg: bytes) -> dict[str, Any]:
    r = await photo_screening.screen(user_jpeg, ref_jpeg)
    return {"suitable": r.suitable, "reason": r.reason, "rule": r.rule, "confidence": r.confidence, "skipped": r.skipped, "blurry": (r.observed or {}).get("blurry")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True, help="ref.jpg:user.jpg ...")
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--box", choices=("head", "face"), default="face", help="head=Face_Neck·Hair 전체+15%% / face=머리 부분만(기본)")
    ap.add_argument("--blur", type=int, default=6, help="블러 반지름 = 박스 짧은 변 / 이 값 (6 강함, 12 약함)")
    args = ap.parse_args()
    print(f"박스 방식: {args.box} · 블러 반지름 = 짧은 변/{args.blur}")

    comparable = set(db.comparable_class_names())
    master = db.master_class_names()
    print(f"환경: {segmenter.describe_environment()}")
    print(f"비교 대상 {len(comparable)}부위\n")
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"photos": {}, "screening": []}
    prepared: dict[str, dict[str, bytes]] = {}

    all_same = True
    for pair in args.pairs:
        for path_str in pair.split(":"):
            if path_str in prepared:
                continue
            path = Path(path_str)
            img = images.load_rgb(path.read_bytes())
            img, _ = images.fit_to_model_aspect(img)  # 서버와 같은 3:4 크롭
            orig_jpeg, w, h = images.encode_photo(img)
            orig_img = Image.open(io.BytesIO(orig_jpeg)).convert("RGB")

            orig, orig_labels = run_seg(orig_jpeg, comparable, master)
            box = face_box(orig_labels, orig.label_map, orig_img.size, args.box)
            if box is None:
                print(f"{path}: 얼굴/머리 라벨을 못 찾음 — 건너뜀")
                continue
            blur_img = blur_face(orig_img, box, args.blur)
            blur_jpeg = to_jpeg(blur_img)
            blur, blur_labels = run_seg(blur_jpeg, comparable, master)

            cmp = compare(orig, orig_labels, blur, blur_labels, comparable)
            all_same &= cmp["same"]
            prepared[path_str] = {"orig": orig_jpeg, "blur": blur_jpeg}
            report["photos"][path_str] = {"face_box": box, **cmp}

            print(f"== {path} ({w}x{h}) 얼굴 박스 {box}")
            print(f"   인물 픽셀 {cmp['person_pixels'][0]} → {cmp['person_pixels'][1]} ({cmp['person_delta_pct']:+.2f}%)")
            for r in cmp["rows"]:
                flag = "" if r["valid"][0] == r["valid"][1] else "  ← is_valid 변경"
                print(f"   {r['part']:<16} px {r['px'][0]:>7}→{r['px'][1]:<7} ratio {r['area_ratio'][0]:.4f}→{r['area_ratio'][1]:.4f} ({r['rel_diff_pct']:.2f}%) IoU {r['iou']}{flag}")
            print(f"   → {'같음' if cmp['same'] else '다름'} (최대 면적비 차 {cmp['max_area_rel_diff_pct']}%, 최소 IoU {cmp['min_iou']})")
            if out_dir:
                stem = path.stem
                (out_dir / f"{stem}_blur.jpg").write_bytes(blur_jpeg)
                Image.fromarray((orig_labels * 8).astype("uint8")).save(out_dir / f"{stem}_map_orig.png")
                Image.fromarray((blur_labels * 8).astype("uint8")).save(out_dir / f"{stem}_map_blur.png")

        if args.screen:
            ref_s, user_s = pair.split(":")
            if ref_s in prepared and user_s in prepared:
                print(f"== 스크리닝 {user_s} vs {ref_s}")
                a = asyncio.run(screen_pair(prepared[user_s]["orig"], prepared[ref_s]["orig"]))
                b = asyncio.run(screen_pair(prepared[user_s]["blur"], prepared[ref_s]["blur"]))
                same = (a["suitable"], a["reason"]) == (b["suitable"], b["reason"])
                all_same &= same
                print(f"   원본: {a}\n   블러: {b}\n   → {'같음' if same else '다름'}")
                report["screening"].append({"pair": pair, "orig": a, "blur": b, "same": same})

    if out_dir:
        (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n보고서: {out_dir / 'report.json'}")
    print("\n결론:", "얼굴 블러가 결과를 바꾸지 않음" if all_same else "차이 있음 — 위 항목 확인")
    return 0 if all_same else 1


if __name__ == "__main__":
    sys.exit(main())
