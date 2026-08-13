"""세그멘테이션 서비스 — Sapiens2 추론 또는 mock 분기."""

import io
from typing import Any

from fastapi.concurrency import run_in_threadpool
from PIL import Image

from app.config import settings

# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

_MOCK_SEG_RESULT: dict[str, Any] = {
    "keypoints": {
        "shoulder_width": 42.5,
        "hip_width": 38.0,
        "waist_width": 30.2,
        "height_px": 512,
    },
    "mask_url": None,
}


# ---------------------------------------------------------------------------
# Real inference (TODO: implement when model weights are available)
# ---------------------------------------------------------------------------

_model = None  # 싱글턴 — main.py lifespan에서 로드


def load_model() -> None:
    """모델 가중치를 로드하여 싱글턴에 저장한다. USE_MOCK=true면 호출하지 않는다."""
    global _model
    # TODO: Sapiens2 모델 로드 구현
    # 예: _model = SapiensModel.from_pretrained("models/sapiens2.pth")
    raise NotImplementedError("Sapiens2 모델 로드 미구현 — USE_MOCK=true 사용")


def _run_inference(image_bytes: bytes) -> dict[str, Any]:
    """동기 추론 함수 (run_in_threadpool로 호출할 것)."""
    # TODO: _model을 사용한 실제 추론 구현
    raise NotImplementedError("Sapiens2 추론 미구현")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def segment(image_bytes: bytes) -> dict[str, Any]:
    """이미지 바이트를 받아 세그멘테이션 결과 dict를 반환한다."""
    if settings.use_mock:
        return dict(_MOCK_SEG_RESULT)

    return await run_in_threadpool(_run_inference, image_bytes)


# ---------------------------------------------------------------------------
# Image preprocessing (mock·실제 공용)
# ---------------------------------------------------------------------------


def preprocess_image(raw_bytes: bytes, max_side: int = 1024) -> bytes:
    """HEIC→JPEG 변환 + 최대 max_side px 리사이즈 후 JPEG 바이트 반환."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass  # pillow-heif 없으면 HEIC는 그냥 실패

    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
