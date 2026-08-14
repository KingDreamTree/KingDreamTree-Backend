"""Sapiens2 세그멘테이션 — 라벨 맵 생성 + 부위별 통계.

⚠️ 이 모듈은 세그멘테이션 워커 전용이다. API 프로세스에서 import 하지 말 것.
   모델이 VRAM/RAM을 크게 차지하므로 API가 같이 들고 있으면 안 된다.

⚠️ 담당 B는 이 파일을 import 하지 않는다. 계약은 DB(segmentation / body_part_segment)뿐이다.

산출물
    map.png   8-bit 그레이스케일 PNG. 픽셀 값 = 라벨 인덱스
    통계      검출된 **모든** 클래스에 대한 pixel_count / area_ratio / bbox / is_valid
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from app.config import settings
from app.schemas.enums import InvalidReason
from app.services import part_merge, sapiens_labels

# ⚠️ 사진 유틸(load_rgb / encode_photo)은 app/services/images.py 에 있다.
#    API 프로세스가 사진 처리 때문에 이 모듈(=torch를 끌어오는 워커 전용 모듈)을
#    import 하게 두면 안 되기 때문이다. 여기서는 재수출만 한다 —
#    scripts/verify_labels.py 처럼 segmenter.load_rgb 를 쓰던 곳은 그대로 동작한다.
from app.services.images import encode_photo, load_rgb  # noqa: F401

MODEL_NAME = "sapiens2"

# --------------------------------------------------------------------------- #
# 결과 타입
# --------------------------------------------------------------------------- #


@dataclass
class PartStat:
    """부위 하나의 통계. body_part_segment 한 행에 대응."""

    class_name: str
    label_value: int
    pixel_count: int
    area_ratio: float
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    is_truncated: bool
    is_valid: bool
    invalid_reason: str | None = None
    #: 이 부위 픽셀 중 옷에서 흡수한 것 (병합을 껐으면 항상 0)
    #: ⚠️ 이 값이 크면 "노출된 살"이 아니라 "옷 실루엣"을 재고 있는 것이다.
    clothing_pixel_count: int = 0


@dataclass
class SegmentationResult:
    """segmentation 한 행 + 딸린 body_part_segment 들."""

    map_png: bytes
    map_width: int
    map_height: int
    label_map: dict[str, str]
    model_name: str
    model_version: str
    person_pixel_count: int
    person_area_ratio: float
    detected_class_count: int
    inference_ms: int
    parts: list[PartStat] = field(default_factory=list)

    #: 옷 병합 **전** 기준으로 유효했던 비교 대상 수.
    #: ⚠️ parts 의 유효 수와 크게 차이나면 "옷을 많이 입었다"는 뜻이다.
    #:    병합은 헐렁한 옷 실루엣도 유효하게 만들어버리므로, 재촬영 판단은
    #:    이 두 숫자를 같이 봐야 한다.
    valid_comparable_raw: int = 0


# --------------------------------------------------------------------------- #
# 모델 로드 (싱글턴)
# --------------------------------------------------------------------------- #

_model = None
_processor = None
_loaded_key: str | None = None


def model_path(size: str | None = None) -> str:
    import os

    return os.path.join(settings.model_dir, f"sapiens2-seg-{size or settings.sapiens_size}")


def resolve_device() -> str:
    import torch

    if settings.sapiens_device != "auto":
        return settings.sapiens_device
    return "cuda" if torch.cuda.is_available() else "cpu"


def resolve_dtype(device: str):
    import torch

    if settings.sapiens_dtype != "auto":
        return getattr(torch, settings.sapiens_dtype)
    # ⚠️ CPU float16은 대부분 더 느리다. CPU에서는 float32.
    return torch.float16 if device == "cuda" else torch.float32


def load_model(size: str | None = None):
    """가중치를 로드해 싱글턴에 담는다. 이미 같은 설정으로 로드돼 있으면 재사용."""
    global _model, _processor, _loaded_key

    size = size or settings.sapiens_size
    device = resolve_device()
    key = f"{size}|{device}|{settings.sapiens_dtype}"
    if _loaded_key == key and _model is not None:
        return _model, _processor

    import os

    import torch
    from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation

    path = model_path(size)
    if not os.path.isdir(path):
        raise FileNotFoundError(
            f"가중치를 찾을 수 없습니다: {path}\n"
            f"  python scripts/download_sapiens.py --size {size}"
        )

    dtype = resolve_dtype(device)
    processor = AutoImageProcessor.from_pretrained(path)

    # ⚠️ CPU 오프로딩(accelerate device_map) 분기는 2026-08-14 에 뺐다.
    #    VRAM 보다 큰 모델을 돌리려는 설정이었는데 우리 두 환경 어디서도 안 탔다.
    #    되살릴 일이 생기면 device_map="auto" + max_memory 를 여기에 넣고,
    #    **아래 model.to(device) 를 건너뛰어야 한다** — accelerate 가 이미 배치했는데
    #    .to() 를 부르면 배치가 깨진다. docs/removed-code.md 참고.
    kwargs: dict[str, Any] = {"dtype": dtype}

    try:
        model = AutoModelForSemanticSegmentation.from_pretrained(path, **kwargs)
    except TypeError:  # transformers 4.x 는 dtype 대신 torch_dtype
        kwargs["torch_dtype"] = kwargs.pop("dtype")
        model = AutoModelForSemanticSegmentation.from_pretrained(path, **kwargs)

    model.to(device)
    model.eval()

    _model, _processor, _loaded_key = model, processor, key
    return model, processor


def model_version(size: str | None = None) -> str:
    """segmentation.model_version 에 저장할 식별자."""
    return f"sapiens2-seg-{size or settings.sapiens_size}"


# --------------------------------------------------------------------------- #
# 추론
# --------------------------------------------------------------------------- #


def infer_labels(image: Image.Image, size: str | None = None) -> tuple[np.ndarray, int, int]:
    """RGB 이미지 → (라벨 배열 H×W uint8, 클래스 수, 추론 소요 ms).

    ⚠️ 로짓을 먼저 원하는 해상도로 bilinear 업샘플한 뒤 argmax 한다.
       argmax를 먼저 하고 라벨을 리사이즈하면 보간이 라벨 값을 섞어
       존재하지 않는 클래스를 만들어낸다. 순서가 중요하다.
    """
    import torch
    import torch.nn.functional as F

    model, processor = load_model(size)
    device = resolve_device()

    started = time.perf_counter()
    inputs = processor(images=image, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.inference_mode():
        outputs = model(**inputs)

    logits = outputs.logits  # (1, C, h, w)
    num_classes = int(logits.shape[1])

    # 모델 입력 해상도(preprocessor_config의 size)를 그대로 맵 해상도로 쓴다.
    target_h, target_w = int(inputs["pixel_values"].shape[-2]), int(
        inputs["pixel_values"].shape[-1]
    )
    if logits.shape[-2:] != (target_h, target_w):
        logits = F.interpolate(
            logits.float(), size=(target_h, target_w), mode="bilinear", align_corners=False
        )

    labels = logits.argmax(dim=1)[0].to(torch.uint8).cpu().numpy()
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if num_classes > 255:
        raise ValueError(f"클래스 수 {num_classes}는 8-bit PNG에 담을 수 없습니다.")

    return labels, num_classes, elapsed_ms


# --------------------------------------------------------------------------- #
# 맵 PNG 인코딩
# --------------------------------------------------------------------------- #


def encode_map_png(labels: np.ndarray) -> bytes:
    """라벨 배열 → 8-bit 그레이스케일 무손실 PNG.

    ⚠️ 규칙 (어기면 에러 없이 값이 바뀐다)
       * PNG만. JPEG·손실 WebP 금지 — 손실 압축이 인접 라벨을 섞는다
       * mode="L" (8-bit 그레이스케일). 알파 없음, ICC 프로파일 없음
       * 팔레트(P) 모드 금지 — 브라우저가 RGB로 펼쳐 값 복원이 한 단계 늘어난다
    """
    if labels.dtype != np.uint8:
        raise ValueError(f"라벨 배열은 uint8이어야 합니다 (현재 {labels.dtype})")

    img = Image.fromarray(labels, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def resize_labels(labels: np.ndarray, width: int, height: int) -> np.ndarray:
    """⚠️ 라벨 배열 리사이즈는 NEAREST만. 보간은 없는 클래스를 만들어낸다."""
    img = Image.fromarray(labels, mode="L")
    return np.asarray(img.resize((width, height), Image.NEAREST), dtype=np.uint8)


# --------------------------------------------------------------------------- #
# 부위별 통계
# --------------------------------------------------------------------------- #


def compute_parts(
    labels: np.ndarray,
    label_map: dict[str, str],
    person_pixel_count: int,
    comparable: set[str],
) -> list[PartStat]:
    """검출된 **모든** 클래스에 대해 통계를 낸다.

    ⚠️ 유효 부위만 만들면 결과 화면에서 "왼팔은 왜 빠졌지?"에 답할 수 없다.
       is_valid=False + invalid_reason 이 남아야 "옷에 가려져 노출이 부족합니다"를
       안내할 수 있다.
    """
    h, w = labels.shape
    stats: list[PartStat] = []

    present, counts = np.unique(labels, return_counts=True)

    for value, count in zip(present.tolist(), counts.tolist()):
        class_name = label_map.get(str(value))
        if class_name is None:
            # label_map에 없는 값 — 매핑 길이 검증을 통과했다면 나올 수 없다.
            continue
        if class_name == "Background":
            continue

        mask = labels == value
        ys, xs = np.nonzero(mask)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())

        area_ratio = (count / person_pixel_count) if person_pixel_count else 0.0
        is_truncated = x0 == 0 or y0 == 0 or x1 == w - 1 or y1 == h - 1

        invalid_reason: str | None = None
        if class_name not in comparable:
            invalid_reason = InvalidReason.NOT_COMPARABLE
        elif count < settings.seg_min_pixels:
            invalid_reason = InvalidReason.TOO_SMALL
        elif area_ratio < settings.seg_min_ratio:
            invalid_reason = InvalidReason.TOO_SMALL_RATIO

        stats.append(
            PartStat(
                class_name=class_name,
                label_value=int(value),
                pixel_count=int(count),
                # ⚠️ 원값을 그대로 저장한다. is_valid는 캐시일 뿐이고 원값이 진실이다.
                #    임계값을 나중에 올려도 기존 데이터를 재판정할 수 있어야 한다.
                area_ratio=min(area_ratio, 1.0),
                bbox_x=x0,
                bbox_y=y0,
                bbox_w=x1 - x0 + 1,
                bbox_h=y1 - y0 + 1,
                is_truncated=is_truncated,
                is_valid=invalid_reason is None,
                invalid_reason=invalid_reason,
            )
        )

    return stats


# --------------------------------------------------------------------------- #
# 공개 API
# --------------------------------------------------------------------------- #


def segment(
    image_bytes: bytes,
    comparable: set[str],
    master_class_names: set[str],
    size: str | None = None,
) -> SegmentationResult:
    """이미지 바이트 → 라벨 맵 PNG + 부위별 통계.

    comparable          : body_part 에서 is_comparable=true 인 class_name 집합
    master_class_names  : body_part 전체 class_name 집합 (라벨 검증용)
    """
    size = size or settings.sapiens_size
    # ⚠️ 이 라벨 목록이 적용되는 모델인지 확인한다. 다른 체크포인트는
    #    클래스 순서가 다를 수 있고, 틀려도 에러 없이 진행된다.
    sapiens_labels.ensure_supported(model_version(size))

    image = load_rgb(image_bytes)
    labels, num_classes, elapsed_ms = infer_labels(image, size)

    # ⚠️ 맵 상한을 여기서 적용한다. **통계를 내기 전에** 줄여야 bbox·pixel_count가
    #    저장되는 맵과 같은 좌표계가 된다. 나중에 줄이면 전부 어긋난다.
    #    ⚠️ 라벨 배열이므로 NEAREST 외에는 절대 쓰면 안 된다 (보간이 없는 클래스를 만든다).
    h, w = labels.shape
    if max(h, w) > settings.map_max_side:
        scale = settings.map_max_side / max(h, w)
        labels = resize_labels(labels, max(1, round(w * scale)), max(1, round(h * scale)))

    label_map = sapiens_labels.build_label_map(num_classes)

    # ⚠️ 조용히 넘어가면 seed 불일치를 못 잡는다.
    unknown = sapiens_labels.check_against_master(label_map, master_class_names)
    if unknown:
        raise ValueError(
            f"label_map에 body_part 마스터에 없는 클래스가 있습니다: {', '.join(unknown)}. "
            "seed(scripts/seed_body_parts.py)와 라벨 매핑을 맞추세요."
        )

    h, w = labels.shape
    person_pixel_count = int(np.count_nonzero(labels != 0))

    # ⚠️ 옷 병합은 **is_valid 판정보다 먼저** 일어나야 한다. 나중에 병합하면
    #    이미 TOO_SMALL 로 무효 처리된 부위를 되살릴 수 없다.
    #    (긴팔이면 상완 노출이 1,200px 대로 떨어져 기준 1,500px 에 걸린다)
    #
    # ⚠️ 저장하는 맵은 **원본**이다. 병합된 배열을 저장하면 label_map 에는 있는
    #    클래스가 맵에는 없는 상태가 되고, 규칙을 바꿀 때 재추론이 필요해진다.
    #    읽는 쪽(담당 B 하이라이트)은 같은 part_merge 함수를 태워서 맞춘다.
    stat_labels = labels
    contribution: dict[str, int] = {}
    valid_raw = 0

    if settings.seg_merge_clothing:
        raw_parts = compute_parts(labels, label_map, person_pixel_count, comparable)
        valid_raw = sum(1 for p in raw_parts if p.is_valid)
        stat_labels, contribution = part_merge.merge_clothing(labels, label_map, comparable)

    parts = compute_parts(stat_labels, label_map, person_pixel_count, comparable)
    for p in parts:
        p.clothing_pixel_count = contribution.get(p.class_name, 0)
    if not settings.seg_merge_clothing:
        valid_raw = sum(1 for p in parts if p.is_valid)

    return SegmentationResult(
        map_png=encode_map_png(labels),
        map_width=w,
        map_height=h,
        label_map=label_map,
        model_name=MODEL_NAME,
        model_version=model_version(size),
        person_pixel_count=person_pixel_count,
        person_area_ratio=min(person_pixel_count / (w * h), 1.0),
        detected_class_count=int(len(np.unique(labels))),
        inference_ms=elapsed_ms,
        parts=parts,
        valid_comparable_raw=valid_raw,
    )


def describe_environment() -> dict[str, Any]:
    """기동 로그용 — 어떤 장비/정밀도로 도는지 남긴다."""
    import torch

    device = resolve_device()
    info: dict[str, Any] = {
        "size": settings.sapiens_size,
        "device": device,
        "dtype": str(resolve_dtype(device)),
        "torch": torch.__version__,
        "labels": f"{len(sapiens_labels.LABEL_NAMES)}클래스",
    }
    if device == "cuda" and torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        info["gpu"] = p.name
        info["vram_gb"] = round(p.total_memory / 1024**3, 1)
    return info
