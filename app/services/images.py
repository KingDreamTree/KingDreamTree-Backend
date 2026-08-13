"""사진 전처리 — 디코딩 / 좌우 반전 / 리사이즈 / 인코딩.

⚠️ **라벨 맵에는 쓰지 말 것.** 여기 있는 것들은 전부 원본 사진용이다.
   LANCZOS 보간과 JPEG 손실 압축이 라벨 값을 섞는다.
   맵은 segmenter.encode_map_png / resize_labels 를 쓴다.

이 모듈이 segmenter.py 와 분리돼 있는 이유
    segmenter.py 는 세그멘테이션 워커 전용이다(torch/transformers를 끌어온다).
    API 프로세스는 사진을 받아 저장만 하면 되므로, 그것 때문에 워커 모듈을
    import 하게 두면 안 된다. 사진 유틸만 여기로 뺀다.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

from app.config import settings

#: 업로드 허용 형식. HEIC는 확장자/타입이 제각각이라 여기 두지 않고 디코딩으로 판별한다.
ALLOWED_CONTENT_TYPES: tuple[str, ...] = ("image/jpeg", "image/png")


def load_rgb(raw_bytes: bytes) -> Image.Image:
    """업로드 바이트 → RGB PIL 이미지. HEIC도 처리한다.

    ⚠️ EXIF Orientation을 반영한다. 스마트폰 사진은 센서 방향 그대로 저장되고
       "회전해서 보라"는 정보만 EXIF에 들어있는 경우가 많다. 이걸 무시하면
       옆으로 누운 사람이 모델에 들어가고, 세그멘테이션이 통째로 어긋난다.
    """
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    img = Image.open(io.BytesIO(raw_bytes))
    # exif_transpose 는 회전을 픽셀에 적용하고 EXIF 태그를 제거한다.
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def flip_horizontal(img: Image.Image) -> Image.Image:
    """좌우 반전.

    ⚠️ 거울로 찍은 사진을 "반전되지 않은 카메라 원본" 기준으로 되돌릴 때만 쓴다.
       이 프로젝트의 저장 이미지·랜드마크·라벨 맵은 전부 그 기준으로 통일돼 있고,
       어기면 왼팔↔오른팔이 뒤바뀐 채 **에러 없이** 진단까지 흘러간다.
    """
    return img.transpose(Image.FLIP_LEFT_RIGHT)


def prepare_photo(raw_bytes: bytes, mirrored: bool = False) -> tuple[bytes, int, int]:
    """업로드 바이트 → 저장용 JPEG. (bytes, width, height) 반환.

    mirrored=True 면 좌우를 되돌려 저장한다. **입구에서 한 번만 뒤집는다** —
    맵이나 bbox를 나중에 뒤집으면 좌표계가 꼬인다.
    """
    img = load_rgb(raw_bytes)
    if mirrored:
        img = flip_horizontal(img)

    max_side = settings.max_image_side
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        w, h = int(w * scale), int(h * scale)
        img = img.resize((w, h), Image.LANCZOS)

    buf = io.BytesIO()
    # ⚠️ EXIF를 싣지 않는다. 위치정보가 사람 사진에 붙어 나가면 안 된다.
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), w, h
