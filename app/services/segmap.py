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
from typing import Any, Iterable

from PIL import Image

#: 의류 클래스를 어느 비교 부위에 합칠지.
#:
#: ⚠️ 실측(A 샘플, 몸에 붙는 상의 착용)에서 `Upper_Clothing` 은 **0px** 이었고
#:    상의가 통째로 `Torso` 로 분류됐다. 즉 이 병합은 흔한 경우 no-op 이다.
#:    그래도 남겨두는 이유는 헐렁한 옷·다른 각도에서 옷이 별도 클래스로 잡히는
#:    경우가 있기 때문이고, 비용이 마스크 OR 한 번뿐이라서다.
#:    ⚠️ 좌우로 갈라야 하는 Lower_Clothing 은 중심선 분할이 필요해 MVP 범위 밖이다.
#:       (허벅지는 반바지 아래로 대부분 노출되므로 실용상 문제가 적다)
CLOTHING_MERGE: dict[str, str] = {
    "Upper_Clothing": "Torso",
}

#: 하이라이트에서 비강조 영역을 얼마나 어둡게 할지 (0=검정, 1=원본)
DIM_FACTOR = 0.28


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
    """비교 부위 하나가 쓸 라벨 값 목록 (의류 병합 포함).

    맵에 없는 클래스는 조용히 건너뛴다 — 옷을 안 입었으면 의류 클래스가
    애초에 없는 게 정상이다.
    """
    values: list[int] = []
    if class_name in name_to_value:
        values.append(name_to_value[class_name])

    if merge_clothing:
        for clothing, target in CLOTHING_MERGE.items():
            if target == class_name and clothing in name_to_value:
                values.append(name_to_value[clothing])

    if not values:
        raise SegMapError(f"'{class_name}' 이 label_map 에 없습니다.")
    return values


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
