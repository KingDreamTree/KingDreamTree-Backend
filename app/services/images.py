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

#: ⚠️ 압축 폭탄 방어. 10MB 파일도 디코딩하면 수십억 픽셀이 될 수 있다.
#   Pillow 기본값(~1.78억)보다 넉넉하지만 t3.large 메모리 안에서 버틸 선으로 낮춰 둔다.
Image.MAX_IMAGE_PIXELS = 80_000_000


class UnsupportedImageError(Exception):
    """이미지로 열 수 없는 업로드."""


_heif_registered = False


def _register_heif() -> None:
    """아이폰 HEIC 지원. pillow-heif 가 없으면 조용히 넘어간다(jpeg/png는 계속 된다)."""
    global _heif_registered
    if _heif_registered:
        return
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    _heif_registered = True


def load_rgb(raw_bytes: bytes) -> Image.Image:
    """업로드 바이트 → RGB PIL 이미지. HEIC도 처리한다.

    ⚠️ **형식 판별을 Content-Type 헤더에 맡기지 않는다.** 브라우저가 HEIC에
       빈 값이나 application/octet-stream 을 붙여 보내는 경우가 있어, 헤더로
       거르면 멀쩡한 아이폰 사진이 막힌다. 실제로 열리는지가 유일한 기준이다.
       (저장은 어차피 JPEG로 다시 인코딩하므로 입력 형식은 남지 않는다)

    ⚠️ EXIF Orientation을 반영한다. 스마트폰 사진은 센서 방향 그대로 저장되고
       "회전해서 보라"는 정보만 EXIF에 들어있는 경우가 많다. 이걸 무시하면
       옆으로 누운 사람이 모델에 들어가고, 세그멘테이션이 통째로 어긋난다.
    """
    _register_heif()

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        # exif_transpose 는 회전을 픽셀에 적용하고 EXIF 태그를 제거한다.
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")
    except Image.DecompressionBombError as e:
        raise UnsupportedImageError("이미지 해상도가 너무 큽니다.") from e
    except Exception as e:  # noqa: BLE001 — Pillow가 형식마다 다른 예외를 던진다
        raise UnsupportedImageError("이미지로 읽을 수 없는 파일입니다.") from e


def flip_horizontal(img: Image.Image) -> Image.Image:
    """좌우 반전.

    ⚠️ 거울로 찍은 사진을 "반전되지 않은 카메라 원본" 기준으로 되돌릴 때만 쓴다.
       이 프로젝트의 저장 이미지·랜드마크·라벨 맵은 전부 그 기준으로 통일돼 있고,
       어기면 왼팔↔오른팔이 뒤바뀐 채 **에러 없이** 진단까지 흘러간다.
    """
    return img.transpose(Image.FLIP_LEFT_RIGHT)


def encode_photo(img: Image.Image, mirrored: bool = False) -> tuple[bytes, int, int]:
    """디코딩된 이미지 → 저장용 JPEG. (bytes, width, height) 반환.

    mirrored=True 면 좌우를 되돌려 저장한다. **입구에서 한 번만 뒤집는다** —
    맵이나 bbox를 나중에 뒤집으면 좌표계가 꼬인다.
    """
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
