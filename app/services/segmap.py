"""라벨 맵 → 부위 마스크 → 하이라이트 이미지 (F08 VLM 입력 생성).

VLM 입력은 **크롭이 아니라 원본 + 하이라이트**다. 부위를 잘라내면 신체 비율의
맥락이 사라져 "어깨가 좁다" 같은 판단 자체가 불가능해진다.
→ `body_part_segment.crop_path` 는 NULL, `body-parts` 버킷은 쓰지 않는다.

━━ 실측으로 확인한 함정 3개 (2026-08-13, A 제공 샘플로 검증) ━━

1. **맵과 원본은 종횡비가 다르다.** 모델이 고정 크기로 리사이즈해 추론한다.
       원본 700x1049 (0.667)  →  맵 768x1024 (0.750)
       sx = 700/768 = 0.9115   sy = 1049/1024 = 1.0244   (11% 차이)
   배율 하나로 곱하면 Torso bbox 가 가로로 31px 어긋난다. x/y 를 따로 계산할 것.

2. **맵 리사이즈에 보간을 쓰면 안 된다. NEAREST 뿐이다.**
   같은 맵을 BILINEAR 로 늘렸더니 라벨 종류가 23 → 28 로 늘었다.
   없던 Apparel·Eyeglasses·Upper_Clothing·Sock 이 경계에서 생성됐다.
   **에러 없이 조용히 틀린다** — 존재하지 않는 부위가 진단에 들어간다.

3. **라벨 인덱스를 하드코딩하지 않는다.** 모델이 바뀌면 재배열된다.
   `segmentation.label_map` 에 행마다 박제돼 있으니 그걸 읽는다.
"""

from __future__ import annotations

import io
import logging
import statistics
from typing import TYPE_CHECKING, Any, Iterable

from PIL import Image

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger("services.segmap")

#: ❌ 고정 매핑(`Upper_Clothing → Torso`)은 **폐기했다** (2026-08-14).
#:
#:    긴팔에서 정확히 반대로 망가지기 때문이다 — 소매 픽셀이 전부 몸통으로
#:    들어가 **정작 살리려던 팔은 여전히 0** 이고 몸통만 부푼다. 병합으로
#:    구제하려던 케이스에서 병합이 목적을 무효화한다.
#:
#:    대신 `app/services/part_merge.py` 의 **가장 가까운 부위로 번지기**(BFS)를
#:    쓴다. 소매는 팔로, 긴바지는 허벅지와 종아리로 자연히 나뉘고 좌우도 자동이다.
#:
#: ⚠️ **병합 구현은 part_merge 하나뿐이어야 한다.** 통계(A)와 마스크(B)가
#:    서로 다른 병합을 쓰면 "숫자는 팔이 굵다는데 화면엔 몸통이 칠해지는"
#:    상태가 되고, **에러 없이 조용히 어긋난다.**

#: 하이라이트에서 비강조 영역을 얼마나 어둡게 할지 (0=검정, 1=원본)
DIM_FACTOR = 0.28

#: 오버레이에서 부위 색을 원본 위에 얼마나 진하게 얹을지 (0=원본, 1=단색)
#:
#: ⚠️ 1.0 으로 칠하면 부위 위치는 명확해지지만 **질감이 사라진다.**
#:    질감(근육 라인·음영)은 VLM 만 읽을 수 있는 유일한 정보다. 그걸 덮으면
#:    이미지를 보낼 이유 자체가 없어진다. 위치는 색으로, 형태는 원본으로 읽게 한다.
OVERLAY_ALPHA = 0.42


class SegMapError(RuntimeError):
    """맵을 읽을 수 없거나 부위가 맵에 없다."""


# --------------------------------------------------------------------------- #
# 로딩
# --------------------------------------------------------------------------- #


def load_map(map_bytes: bytes) -> Image.Image:
    """라벨 맵 PNG → 8-bit 그레이스케일 이미지. 픽셀 값이 곧 라벨이다.

    ⚠️ convert("L") 을 부르지 않는다. 이미 L 이 아니면 그건 맵이 아니라
       뭔가 잘못된 파일이므로, 조용히 변환하지 말고 실패시킨다.
    """
    try:
        img = Image.open(io.BytesIO(map_bytes))
    except Exception as e:  # noqa: BLE001
        raise SegMapError("라벨 맵을 이미지로 열 수 없습니다.") from e

    if img.mode != "L":
        raise SegMapError(
            f"라벨 맵이 8-bit 그레이스케일이 아닙니다 (mode={img.mode}). "
            "재압축되었거나 잘못된 파일입니다."
        )
    return img


def invert_label_map(label_map: dict[str, str]) -> dict[str, int]:
    """{"22": "Torso"} → {"Torso": 22}. segmentation 행의 label_map 을 넣는다."""
    return {name: int(value) for value, name in label_map.items()}


# --------------------------------------------------------------------------- #
# 마스크
# --------------------------------------------------------------------------- #


def class_mask(
    seg_map: Image.Image,
    label_values: Iterable[int],
) -> Image.Image:
    """주어진 라벨 값들을 켠 이진 마스크 (맵 좌표계, mode="L", 0 또는 255)."""
    targets = set(label_values)
    if not targets:
        raise SegMapError("마스크로 만들 라벨 값이 없습니다.")
    return seg_map.point(lambda v: 255 if v in targets else 0, mode="L")


def part_label_values(
    class_name: str,
    name_to_value: dict[str, int],
    merge_clothing: bool = True,
) -> list[int]:
    """비교 부위 하나가 쓸 라벨 값 목록.

    ⚠️ 의류 병합은 **여기서 하지 않는다.** `apply_clothing_merge()` 로 라벨 배열
       자체를 먼저 병합한 뒤 이 함수를 부른다 (병합 구현은 part_merge 하나뿐).

    merge_clothing 인자는 호출부 호환을 위해 남겨두었지만 아무 일도 하지 않는다.
    """
    if class_name not in name_to_value:
        raise SegMapError(f"'{class_name}' 이 label_map 에 없습니다.")
    return [name_to_value[class_name]]


def apply_clothing_merge(
    labels: "np.ndarray",
    label_map: dict[str, str],
    targets: set[str],
) -> tuple["np.ndarray", dict[str, int]]:
    """옷 픽셀을 인접 비교 부위로 흡수한 라벨 배열을 돌려준다.

    구현은 `app/services/part_merge.py` (담당 A) 하나뿐이다. 여기서는
    **위임만** 한다 — 두 벌로 구현하면 통계와 마스크가 조용히 어긋난다.

    Returns:
        (병합된 라벨 배열, {클래스명: 옷에서 흡수한 픽셀 수})

    ⚠️ 두 번째 값을 반드시 같이 쓸 것. 병합은 헐렁한 옷 실루엣도 유효한 부위로
       만들어버리므로, 어디까지가 실제 노출인지는 이 숫자로만 알 수 있다.
       진단 프롬프트의 confidence 근거로 넘긴다.

    part_merge 가 아직 머지되지 않았으면 병합 없이 원본을 그대로 돌려준다
    (A의 `feature/clothing-merge` 머지 전까지의 과도기).
    """
    try:
        from app.services.part_merge import merge_clothing as _merge
    except ImportError:
        log.warning("part_merge 미도입 — 의류 병합 없이 진행합니다.")
        return labels.copy(), {}

    return _merge(labels, label_map, targets)


# --------------------------------------------------------------------------- #
# 좌표 변환 — ⚠️ 여기가 조용히 틀리는 지점
# --------------------------------------------------------------------------- #


def scales(photo_size: tuple[int, int], map_size: tuple[int, int]) -> tuple[float, float]:
    """맵 좌표 → 원본 좌표 배율 (sx, sy).

    ⚠️ **반드시 둘을 따로 쓴다.** 종횡비가 보존되지 않는다 (모듈 주석 §1).
    """
    (pw, ph), (mw, mh) = photo_size, map_size
    return pw / mw, ph / mh


def scale_bbox(bbox: dict[str, int], sx: float, sy: float) -> dict[str, int]:
    """맵 좌표계 bbox → 원본 좌표계. segmentation.json 의 bbox 는 전부 맵 기준이다."""
    return {
        "x": round(bbox["x"] * sx),
        "y": round(bbox["y"] * sy),
        "w": round(bbox["w"] * sx),
        "h": round(bbox["h"] * sy),
    }


def resize_mask(mask: Image.Image, photo_size: tuple[int, int]) -> Image.Image:
    """마스크를 원본 크기로 확대한다.

    ⚠️ **NEAREST 고정.** 인자로 빼지 않는다 — 다른 값을 넣을 수 있게 두면
       언젠가 누가 넣고, 그러면 라벨이 섞인다 (모듈 주석 §2).
    """
    return mask.resize(photo_size, Image.NEAREST)


# --------------------------------------------------------------------------- #
# 하이라이트
# --------------------------------------------------------------------------- #


def build_highlight(
    photo: Image.Image,
    seg_map: Image.Image,
    class_name: str,
    label_map: dict[str, str],
    merge_clothing: bool = True,
    dim: float = DIM_FACTOR,
) -> Image.Image:
    """원본 사진에서 해당 부위만 밝게, 나머지는 어둡게 한 이미지를 만든다.

    Args:
        photo: 원본 사진 (RGB)
        seg_map: load_map() 결과
        class_name: 비교 부위 이름 (예: "Torso")
        label_map: segmentation 행의 label_map ({"22": "Torso", ...})

    ⚠️ 크롭하지 않는다. 전신이 보여야 VLM 이 비율을 판단할 수 있다.
    """
    photo = photo.convert("RGB")
    name_to_value = invert_label_map(label_map)
    values = part_label_values(class_name, name_to_value, merge_clothing)

    mask = resize_mask(class_mask(seg_map, values), photo.size)
    dimmed = Image.eval(photo, lambda v: int(v * dim))
    return Image.composite(photo, dimmed, mask)


def hex_to_rgb(color_hex: str) -> tuple[int, int, int]:
    """'#F76707' → (247, 103, 7). body_part.color_hex 를 그대로 받는다."""
    h = color_hex.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def build_overlay(
    photo: Image.Image,
    seg_map: Image.Image,
    parts: list[tuple[str, str]],
    label_map: dict[str, str],
    merge_clothing: bool = True,
    dim: float = DIM_FACTOR,
    alpha: float = OVERLAY_ALPHA,
) -> tuple[Image.Image, list[str]]:
    """비교 대상 **전 부위**를 각자의 색으로 칠한 한 장을 만든다.

    부위마다 하이라이트를 한 장씩 만들면 같은 원본을 부위 수만큼 VLM 에 올리게 된다.
    같은 정보를 9번 보내는 셈이라 토큰만 늘고 얻는 게 없다. 색으로 구분하면
    **한 장에 전 부위가 들어가고, VLM 이 부위들을 서로 비교할 수 있다** —
    "어깨가 좁다"는 골반 대비 판단이므로 애초에 부위 하나만 봐서는 나올 수 없다.

    Args:
        parts: [(class_name, color_hex), ...] — body_part 마스터에서 읽은 순서 그대로.

    Returns:
        (오버레이 이미지, 실제로 칠해진 class_name 목록)
        맵에 없는 부위는 조용히 빠지고 목록에도 들어가지 않는다. 이 목록이
        프롬프트 범례(legend)와 정확히 일치해야 VLM 이 색을 오해하지 않는다.

    ⚠️ 크롭하지 않는다. 전신이 보여야 비율 판단이 가능하다 (build_highlight 와 같은 이유).
    """
    photo = photo.convert("RGB")
    name_to_value = invert_label_map(label_map)

    tint = Image.new("RGB", photo.size, (0, 0, 0))
    union = Image.new("L", photo.size, 0)
    painted: list[str] = []

    for class_name, color_hex in parts:
        try:
            values = part_label_values(class_name, name_to_value, merge_clothing)
        except SegMapError:
            continue  # 맵에 없는 부위 — 범례에서도 빠져야 한다
        mask = resize_mask(class_mask(seg_map, values), photo.size)
        tint.paste(hex_to_rgb(color_hex), mask=mask)
        union.paste(255, mask=mask)
        painted.append(class_name)

    if not painted:
        raise SegMapError("맵에서 비교 대상 부위를 하나도 찾지 못했습니다.")

    tinted = Image.blend(photo, tint, alpha)
    dimmed = Image.eval(photo, lambda v: int(v * dim))
    return Image.composite(tinted, dimmed, union), painted


#: VLM 에 보낼 사진의 긴 변 상한.
#:
#: GPT-4o 는 어차피 내부에서 축소한 뒤 512px 타일로 쪼개 토큰을 매긴다. 4096px
#: 원본을 그대로 올려도 모델이 보는 것은 같고 업로드량만 커진다.
VLM_MAX_SIDE = 1024


def fit_for_vlm(img: Image.Image, max_side: int = VLM_MAX_SIDE) -> Image.Image:
    """VLM 전송용으로 긴 변을 제한한다. 이미 작으면 그대로 반환한다.

    ⚠️ 여기서는 LANCZOS 를 쓴다 — **라벨 맵의 NEAREST 규칙(모듈 주석 §2)과
       혼동하지 말 것.** 이건 사람이 보는 사진이라 보간이 화질에 이롭고,
       라벨처럼 "값이 곧 의미"인 데이터가 아니다. 반대로 맵에 LANCZOS 를 쓰면
       없던 부위가 생긴다.
    """
    if max(img.size) <= max_side:
        return img
    out = img.copy()
    out.thumbnail((max_side, max_side), Image.LANCZOS)
    return out


def encode_jpeg(img: Image.Image, quality: int = 88) -> bytes:
    """VLM 전송용 JPEG 인코딩.

    ⚠️ 하이라이트 **결과물**에만 쓴다. 라벨 맵에는 절대 쓰지 말 것 —
       손실 압축이 라벨 값을 섞는다 (services/images.py 와 같은 규약).
    """
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 진단 프롬프트용 통계
# --------------------------------------------------------------------------- #


def part_stats(segment_row: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    """body_part_segment 행 → 프롬프트에 넣을 수치 (원본 좌표계 bbox 포함)."""
    bbox = {k: segment_row.get(f"bbox_{k}") for k in ("x", "y", "w", "h")}
    scaled = scale_bbox(bbox, sx, sy) if all(v is not None for v in bbox.values()) else None
    return {
        "class_name": segment_row["class_name"],
        "pixel_count": segment_row.get("pixel_count"),
        "area_ratio": segment_row.get("area_ratio"),
        "bbox": scaled,
        "is_truncated": segment_row.get("is_truncated"),
    }


# --------------------------------------------------------------------------- #
# 스케일 불변 수치 — ⚠️ VLM 에게 재게 하지 않고 코드가 계산한다
# --------------------------------------------------------------------------- #
#
# 원칙: **셀 수 있는 것은 코드가, 볼 수밖에 없는 것은 VLM 이.**
#
# 픽셀 수·너비·면적은 세그멘테이션 맵에 이미 정확히 들어 있다. 그걸 VLM 에게
# 눈대중으로 재게 하면 정확한 값을 부정확한 방법으로 다시 구하는 셈이고,
# 틀려도 그럴듯해서 검증이 안 된다. VLM 에게는 수치를 **주고**, 대신 수치로
# 표현되지 않는 것(근육 라인·실루엣·자세·좌우 균형의 시각적 인상)만 맡긴다.
#
# ⚠️ 두 사진은 촬영 거리·해상도가 다르다. 절대 픽셀을 그대로 비교하면
#    "한 발 앞에 서서 찍었다"가 "근육량이 늘었다"로 읽힌다. 전부 인물 기준
#    비율로 정규화해서 비교 가능하게 만든다.


def person_bounds(
    rows: list[dict[str, Any]],
    person_classes: set[str],
) -> dict[str, Any] | None:
    """인물 전체의 픽셀 합과 bbox 합집합. 정규화의 분모가 된다.

    Args:
        person_classes: 인물로 칠 클래스 (part_group != 'OTHER'). 배경·의류를
                        분모에 넣으면 헐렁한 옷을 입었을 때 분모가 부풀어
                        모든 부위가 작아 보인다.
    """
    body = [r for r in rows if r["class_name"] in person_classes]
    if not body:
        return None

    total_px = sum(int(r.get("pixel_count") or 0) for r in body)
    if total_px <= 0:
        return None

    x0 = min(int(r["bbox_x"]) for r in body)
    y0 = min(int(r["bbox_y"]) for r in body)
    x1 = max(int(r["bbox_x"]) + int(r["bbox_w"]) for r in body)
    y1 = max(int(r["bbox_y"]) + int(r["bbox_h"]) for r in body)

    return {
        "total_px": total_px,
        "width": max(1, x1 - x0),
        "height": max(1, y1 - y0),
    }


def normalized_stats(row: dict[str, Any], bounds: dict[str, Any]) -> dict[str, float]:
    """부위 행 → 인물 기준 비율. 촬영 거리·해상도와 무관해진다.

    area_share   부위 픽셀 / 인물 전체 픽셀   (체형에서 이 부위가 차지하는 몫)
    width_share  부위 bbox 너비 / 인물 너비
    height_share 부위 bbox 높이 / 인물 높이
    aspect       부위 너비 / 높이             (그 자체로 스케일 불변)
    """
    w, h = int(row["bbox_w"]), int(row["bbox_h"])
    px = int(row.get("pixel_count") or 0)

    # 옷에서 흡수된 픽셀의 비중. 컬럼(clothing_pixel_count)이 아직 없으면 None —
    # NULL(미계산)과 0(계산했고 흡수 없음)을 구분해야 구버전 행을 오독하지 않는다.
    clothing = row.get("clothing_pixel_count")
    clothing_ratio = round(int(clothing) / px, 4) if clothing is not None and px > 0 else None

    return {
        "area_share": round(px / bounds["total_px"], 4),
        "width_share": round(w / bounds["width"], 4),
        "height_share": round(h / bounds["height"], 4),
        "aspect": round(w / max(1, h), 3),
        "clothing_ratio": clothing_ratio,
    }


def _pct_diff(user: float, ref: float) -> float | None:
    """레퍼런스 대비 몇 % 차이인가. ref 가 0이면 비교 자체가 무의미하므로 None."""
    if ref <= 0:
        return None
    return round((user / ref - 1) * 100, 1)


def compare_parts(
    ref_rows: dict[str, dict[str, Any]],
    user_rows: dict[str, dict[str, Any]],
    class_names: list[str],
    person_classes: set[str],
) -> dict[str, Any]:
    """비교 대상 부위별로 레퍼런스↔사용자 정규화 수치와 차이(%)를 낸다.

    Returns:
        {"parts": {class_name: {...}}, "bounds": {...}}
        bounds 가 None 이면(인물을 못 찾음) parts 는 빈 dict 다 — 이 경우
        수치 없이 이미지만으로 진행한다. 진단을 막지는 않는다.
    """
    ref_bounds = person_bounds(list(ref_rows.values()), person_classes)
    user_bounds = person_bounds(list(user_rows.values()), person_classes)
    if ref_bounds is None or user_bounds is None:
        return {"parts": {}, "bounds": None}

    parts: dict[str, Any] = {}
    for name in class_names:
        ref, user = ref_rows.get(name), user_rows.get(name)
        if ref is None or user is None:
            continue
        r, u = normalized_stats(ref, ref_bounds), normalized_stats(user, user_bounds)
        parts[name] = {
            "reference": r,
            "user": u,
            "diff_pct": {
                "area_share": _pct_diff(u["area_share"], r["area_share"]),
                "width_share": _pct_diff(u["width_share"], r["width_share"]),
                "height_share": _pct_diff(u["height_share"], r["height_share"]),
            },
            "user_truncated": bool(user.get("is_truncated")),
        }

    return {
        "parts": parts,
        "bounds": {"reference": ref_bounds, "user": user_bounds},
        "framing_bias": framing_bias(parts),
    }


def framing_bias(parts: dict[str, Any]) -> dict[str, Any] | None:
    """잘림(프레이밍) 편향 지표. **판정하지 않고 기록만 한다.**

    왜 필요한가
        `area_share` 의 분모는 인물 전체 픽셀이다. 발이나 머리가 잘리면 분모가
        줄어 **모든 부위 share 가 같은 방향으로 같은 비율만큼 밀린다.**

        원래는 A 의 프레이밍 게이트가 막아줬지만, 2026-08-14 에 게이트가
        bbox Jaccard → **몸통 길이 비율**로 바뀌면서 잘림을 못 잡는다.
        몸통 길이는 무릎까지 잘려도 1.0 이 나온다. (게이트 변경 자체는 옳다 —
        옛 방식은 팔다리를 움직인 것을 프레이밍 문제로 오인해, 물러서도 고칠 수
        없는 안내를 띄웠다.)
        → 지금 잘림을 보는 곳은 여기뿐이다.

    어떻게 구분하는가
        실제 체형 차이는 부위마다 방향이 갈린다. 잘림은 **전 부위를 같은 방향으로**
        민다. 그래서 "중앙값이 0에서 멀고 + 대부분이 같은 부호" 가 잘림의 서명이다.

    ⚠️ **임계값을 두지 않는다.** 근거 데이터가 없는 상태에서 컷오프를 만들면
       `clothing_ratio > 0.5` · `상완/전완 1.1~1.3` 과 같은 실수가 된다.
       실호출 로그가 쌓인 뒤에 자른다.
    """
    diffs = [
        p["diff_pct"]["area_share"]
        for p in parts.values()
        if p["diff_pct"]["area_share"] is not None
    ]
    if len(diffs) < 3:
        return None

    median = statistics.median(diffs)
    # 부호가 중앙값과 같은 부위 수 — 잘림이면 거의 전부가 같은 쪽이다.
    same_direction = sum(1 for d in diffs if (d >= 0) == (median >= 0))

    return {
        "median_area_diff_pct": round(median, 2),
        "same_direction": same_direction,
        "parts": len(diffs),
        "note": (
            "전 부위가 같은 방향으로 밀렸다면 체형 차이가 아니라 촬영 잘림일 수 있음. "
            "판정하지 않고 기록만 함."
        ),
    }


def symmetry(
    rows: dict[str, dict[str, Any]],
    class_names: list[str],
    person_classes: set[str],
) -> dict[str, dict[str, Any]]:
    """한 사람 안에서 좌우 같은 부위의 면적 차이. 레퍼런스와 무관한 자기 기준 지표.

    좌우 쌍은 접두사 규칙(Left_/Right_)으로 찾는다. 부위 목록이 늘어도
    코드를 고칠 일이 없어야 한다 (work-b.md §5: 부위명 하드코딩 금지).

    Returns:
        {쌍 이름: {"diff_pct": float, "larger": "LEFT"|"RIGHT"|"EQUAL"}}

    ⚠️ **방향(larger)이 빠지면 좌·우 문장이 똑같아진다.** 차이 %만 주면 VLM 이
       "어느 쪽이 더 큰지"를 모르므로 쌍의 두 항목을 구분할 근거가 없고,
       수치가 비슷한 좌우 쌍은 복사한 듯한 문장이 된다 (실호출로 확인).

    ⚠️ 경고용 참고값이다. 자세·각도로도 쉽게 10~20% 가 나므로 자동 판정에 쓰지 않는다
       (인바디 좌우 대칭 30% 규칙과 같은 취급 — 경고만, 자동 수정 금지).
    """
    bounds = person_bounds(list(rows.values()), person_classes)
    if bounds is None:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for name in class_names:
        if not name.startswith("Left_"):
            continue
        mirror = "Right_" + name[len("Left_") :]
        if mirror not in rows or name not in rows:
            continue
        left = normalized_stats(rows[name], bounds)["area_share"]
        right = normalized_stats(rows[mirror], bounds)["area_share"]
        base = max(left, right)
        if base > 0:
            out[name[len("Left_") :]] = {
                "diff_pct": round(abs(left - right) / base * 100, 1),
                "larger": "LEFT" if left > right else ("RIGHT" if right > left else "EQUAL"),
            }
    return out
