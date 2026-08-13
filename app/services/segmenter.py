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
from app.services import sapiens_labels

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

    kwargs: dict[str, Any] = {"dtype": dtype}
    offloading = settings.sapiens_offload and device == "cuda"
    if offloading:
        # ⚠️ VRAM보다 큰 모델을 돌리기 위한 설정.
        #    accelerate가 들어가는 레이어만 GPU에 올리고 나머지는 CPU RAM에 둔다.
        #    레이어가 CPU↔GPU를 오가므로 느리다. 운영에서는 끄는 게 맞다.
        #    GPU 여유를 남기지 않으면 활성값 자리가 없어 OOM이 난다.
        budget = settings.sapiens_gpu_max_gib
        if budget <= 0:
            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            budget = round(total * 0.9, 1)
        kwargs["device_map"] = "auto"
        kwargs["max_memory"] = {0: f"{budget}GiB", "cpu": "48GiB"}

    try:
        model = AutoModelForSemanticSegmentation.from_pretrained(path, **kwargs)
    except TypeError:  # transformers 4.x 는 dtype 대신 torch_dtype
        kwargs["torch_dtype"] = kwargs.pop("dtype")
        model = AutoModelForSemanticSegmentation.from_pretrained(path, **kwargs)

    if not offloading:
        # device_map을 쓴 경우 accelerate가 이미 배치했으므로 .to()를 부르면 안 된다.
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
    label_order: str | None = None,
) -> SegmentationResult:
    """이미지 바이트 → 라벨 맵 PNG + 부위별 통계.

    comparable          : body_part 에서 is_comparable=true 인 class_name 집합
    master_class_names  : body_part 전체 class_name 집합 (라벨 검증용)
    """
    order = label_order or sapiens_labels.ensure_verified()
    size = size or settings.sapiens_size

    image = load_rgb(image_bytes)
    labels, num_classes, elapsed_ms = infer_labels(image, size)

    label_map = sapiens_labels.build_label_map(num_classes, order)

    # ⚠️ 조용히 넘어가면 seed 불일치를 못 잡는다.
    unknown = sapiens_labels.check_against_master(label_map, master_class_names)
    if unknown:
        raise ValueError(
            f"label_map에 body_part 마스터에 없는 클래스가 있습니다: {', '.join(unknown)}. "
            "seed(scripts/seed_body_parts.py)와 라벨 매핑을 맞추세요."
        )

    h, w = labels.shape
    person_pixel_count = int(np.count_nonzero(labels != 0))

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
        parts=compute_parts(labels, label_map, person_pixel_count, comparable),
    )


# --------------------------------------------------------------------------- #
# 이미지 전처리 (원본 사진용 — ⚠️ 라벨 맵에는 쓰지 말 것)
# --------------------------------------------------------------------------- #


def load_rgb(raw_bytes: bytes) -> Image.Image:
    """업로드 바이트 → RGB PIL 이미지. HEIC도 처리."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image.open(io.BytesIO(raw_bytes)).convert("RGB")


def preprocess_photo(raw_bytes: bytes, max_side: int | None = None) -> tuple[bytes, int, int]:
    """원본 사진 저장용 — HEIC 변환 + 긴 변 제한 + JPEG 인코딩.

    ⚠️ **라벨 맵에는 절대 쓰지 마세요.** LANCZOS 보간과 JPEG 손실 압축이
       라벨 값을 섞습니다. 맵은 encode_map_png / resize_labels 를 쓸 것.
    """
    max_side = max_side or settings.max_image_side
    img = load_rgb(raw_bytes)
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        w, h = int(w * scale), int(h * scale)
        img = img.resize((w, h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), w, h


def describe_environment() -> dict[str, Any]:
    """기동 로그용 — 어떤 장비/정밀도로 도는지 남긴다."""
    import torch

    device = resolve_device()
    info: dict[str, Any] = {
        "size": settings.sapiens_size,
        "device": device,
        "dtype": str(resolve_dtype(device)),
        "torch": torch.__version__,
        "label_order": sapiens_labels.VERIFIED_ORDER or "(미검증)",
    }
    if device == "cuda" and torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        info["gpu"] = p.name
        info["vram_gb"] = round(p.total_memory / 1024**3, 1)
    return info
