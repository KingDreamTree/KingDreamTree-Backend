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
import logging

from typing import Any

from PIL import Image, ImageOps

from app.config import settings

#: ⚠️ 압축 폭탄 방어. 10MB 파일도 디코딩하면 수십억 픽셀이 될 수 있다.
#   t3.large 메모리 안에서 버틸 선.
#
#   ⚠️ **이 값만으로는 못 막는다.** Pillow 는 이 값을 넘으면 경고만 내고,
#      **2배**를 넘어야 예외를 던진다. 실측 — 2.8MB PNG(1억 화소)가 그대로 통과해
#      메모리 286MB 를 먹었다. 그래서 load_rgb 가 헤더 크기를 직접 본다.
MAX_PIXELS = 80_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


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
        # ⚠️ 조용히 넘어가면 아이폰 사진이 "읽을 수 없는 파일"로 막히는데
        #    원인이 화면 어디에도 안 나온다. 기동 로그에는 남긴다.
        logging.getLogger("services.images").warning(
            "pillow-heif 가 없어 HEIC(아이폰 기본 형식)을 열 수 없습니다. "
            "pip install pillow-heif"
        )
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

        # ⚠️ **픽셀을 만지기 전에** 크기를 본다. Image.open 은 헤더만 읽으므로
        #    여기서 거르면 큰 이미지가 메모리에 펼쳐지지 않는다.
        #    exif_transpose 와 convert 는 둘 다 실제 디코딩을 일으킨다.
        w, h = img.size
        if w * h > MAX_PIXELS:
            raise UnsupportedImageError(
                f"이미지 해상도가 너무 큽니다 ({w}x{h}). "
                f"{MAX_PIXELS // 1_000_000}백만 화소 이하로 줄여주세요."
            )

        # exif_transpose 는 회전을 픽셀에 적용하고 EXIF 태그를 제거한다.
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")
    except UnsupportedImageError:
        raise
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
        # ⚠️ max(1, …) — 1×100000 같은 극단 비율은 화소 수 검사를 통과하는데,
        #    int() 절사로 치수가 0 이 되면 resize 가 ValueError 로 500 을 낸다.
        #    (segmenter.resize 경로는 이미 같은 하한을 쓴다)
        w, h = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((w, h), Image.LANCZOS)

    buf = io.BytesIO()
    # ⚠️ EXIF를 싣지 않는다. 위치정보가 사람 사진에 붙어 나가면 안 된다.
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), w, h


# --------------------------------------------------------------------------- #
# Sapiens2 입력 규격 맞추기
# --------------------------------------------------------------------------- #

#: Sapiens2 세그 모델의 입력 비율 (preprocessor_config.json: height=1024, width=768).
MODEL_ASPECT = 768 / 1024  # 0.75 (가로/세로)

#: 이 오차 안이면 손대지 않는다. 폰 세로 사진(3:4)은 그대로 통과한다.
_ASPECT_TOL = 0.02


def fit_to_model_aspect(
    img: Image.Image,
    landmarks: list[dict[str, Any]] | None = None,
) -> tuple[Image.Image, list[dict[str, Any]] | None]:
    """사진을 Sapiens2 입력 비율(3:4)로 센터 크롭한다. 랜드마크도 같이 옮긴다.

    ⚠️ **왜 필요한가.** 전처리기가 `do_pad=false` 라 비율을 보존하지 않고
       768x1024 로 **강제로 늘린다**. 16:9 노트북 웹캠을 넣으면 사람이 가로로
       2.37 배 눌린 채 모델에 들어가고, 그 형태는 학습 분포 밖이라 부위 경계가
       무너진다 (실측: 상완이 실선 수준으로 얇아진다).
       레퍼런스는 대개 폰 세로(3:4)라 왜곡이 0 이다 — 그래서 "레퍼런스는 되는데
       사용자 사진만 안 되는" 증상으로 나타난다.

    ⚠️ **왜 패딩이 아니라 크롭인가.** 전처리기의 `do_pad=True` 경로를 켜면 비율은
       보존되지만 캔버스의 58% 가 검은 여백이 되어 사람 픽셀이 반토막 난다
       (상완 72px → 30px). 게다가 맵에 레터박스가 남아 `segmap.scales()` 의
       단순 배율 가정이 깨진다. 크롭은 캔버스를 100% 쓰고 좌표 매핑도 그대로다.

    ⚠️ **사람 중심으로 자른다.** 화면 중앙 기준으로 자르면 한쪽에 서 있는 사람이
       잘린다. 랜드마크가 있으면 그 중심을 크롭 중심으로 쓴다.

    ⚠️ **거울 되돌리기 뒤에 불러야 한다.** 이미지와 랜드마크가 같은 좌표계여야
       같은 박스로 자를 수 있다.

    Returns:
        (크롭된 이미지, 재정규화된 랜드마크). 이미 규격이면 입력을 그대로 돌려준다.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return img, landmarks
    if abs(w / h - MODEL_ASPECT) <= _ASPECT_TOL:
        return img, landmarks  # 이미 3:4 — 손대지 않는다

    if w / h > MODEL_ASPECT:  # 가로가 넓다 → 좌우를 자른다
        cw, ch = round(h * MODEL_ASPECT), h
    else:  # 세로가 길다 → 위아래를 자른다
        cw, ch = w, round(w / MODEL_ASPECT)
    cw, ch = max(1, min(cw, w)), max(1, min(ch, h))

    cx, cy = _focus(landmarks, w, h)
    left = _clamp(round(cx - cw / 2), 0, w - cw)
    top = _clamp(round(cy - ch / 2), 0, h - ch)
    box = (left, top, left + cw, top + ch)

    return img.crop(box), _recenter_landmarks(landmarks, box, (w, h))


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi))


def _focus(landmarks: list[dict[str, Any]] | None, w: int, h: int) -> tuple[float, float]:
    """크롭 중심 (픽셀). 랜드마크가 있으면 인물 bbox 중심, 없으면 화면 중앙.

    ⚠️ visibility 가 낮은 점은 좌표가 추측값이라 뺀다 — 넣으면 화면 밖의 엉뚱한
       추정치가 중심을 끌고 간다.
    """
    if landmarks:
        xs = [p["x"] for p in landmarks if p and float(p.get("visibility", 1.0)) >= 0.5]
        ys = [p["y"] for p in landmarks if p and float(p.get("visibility", 1.0)) >= 0.5]
        if xs and ys:
            return (min(xs) + max(xs)) / 2 * w, (min(ys) + max(ys)) / 2 * h
    return w / 2, h / 2


def _recenter_landmarks(
    landmarks: list[dict[str, Any]] | None,
    box: tuple[int, int, int, int],
    size: tuple[int, int],
) -> list[dict[str, Any]] | None:
    """정규화 좌표(0~1)를 크롭 기준으로 다시 정규화한다.

    ⚠️ 이걸 빼면 사진만 잘리고 좌표는 원본 기준으로 남아 **조용히 어긋난다.**
       자세 점수도, 나중의 좌표 사용도 전부 틀리는데 에러는 안 난다.
    ⚠️ 크롭 밖으로 나간 점은 0~1 을 벗어난다. 잘라내지 않고 그대로 둔다 —
       visibility 와 함께 "프레임 밖"이라는 사실 자체가 정보다.
    """
    if not landmarks:
        return landmarks
    left, top, right, bottom = box
    w, h = size
    cw, ch = max(1, right - left), max(1, bottom - top)
    out: list[dict[str, Any]] = []
    for p in landmarks:
        if not p:
            out.append(p)
            continue
        out.append({**p, "x": (p["x"] * w - left) / cw, "y": (p["y"] * h - top) / ch})
    return out
