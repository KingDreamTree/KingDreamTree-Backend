"""OpenAI 로 나가는 사진의 얼굴을 가린다 — 팟 전용.

    face_mask.apply(img, landmarks) -> (가린 이미지, 박스 | None)

왜 팟에서, 왜 OpenAI 행만 (2026-09-09 결정)
    · 얼굴을 **세게** 가린 사진을 세그(Sapiens)에 넣으면 뭉개진 얼굴을 배경으로 분류해
      인물 픽셀(모든 부위 비율의 분모)이 7~15% 줄고 부위 비율이 틀어진다 (실측,
      scripts/measure_face_blur.py). 약하게 가리면 세그는 그대로지만 얼굴 윤곽이 남는다.
    · 그래서 세그는 **안 가린 가공본**, OpenAI 로 가는 세 번(스크리닝·부위·종합)은
      **가린 복사본**을 쓴다. 복사본은 가공 직후 한 번만 만들어 처리 내내 메모리에 두고
      (photo_source 의 vlm 변형), 끝나면 가공본과 함께 지운다.
    · 흐림(블러)이 아니라 **단색으로 덮는다.** 스크리닝 프롬프트가 "초점이 안 맞아
      외곽선이 뭉개졌는가"를 보므로, 블러는 그 판정과 헷갈릴 수 있다. 덮인 사각형은
      의도적인 가림으로 읽히고 익명화도 완전하다. 프롬프트에도 "얼굴은 가려져 있다"를
      알린다 (app/prompts/photo_screening·part_diagnosis·overall_diagnosis).

얼굴 위치
    프론트가 등록 때 보낸 MediaPipe 포즈 랜드마크 중 얼굴 점(0 코, 1~6 눈, 7~8 귀,
    9~10 입)으로 잡는다. 랜드마크는 가공본(크롭 후) 기준으로 재정규화된 것을 받는다
    (images._recenter_landmarks). 귀~귀 폭을 얼굴 폭으로 보고 이마·턱까지 넓힌다.

⚠️ 얼굴 점이 안 보이면(뒷모습·전부 프레임 밖) 가릴 얼굴이 없다고 보고 그대로 둔다 —
   None 을 돌려주므로 호출부가 로그로 남긴다. 랜드마크 자체가 없는 사진은 pod 모드
   등록에서 막히므로(pose_landmarks 필수) 정상 경로에서는 오지 않는다.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFilter

#: MediaPipe Pose 33점 중 얼굴에 해당하는 인덱스
FACE_INDICES: tuple[int, ...] = tuple(range(11))
#: 이 값 미만은 "안 보임"으로 본다 (MediaPipe visibility 0~1)
MIN_VISIBILITY = 0.5
#: 덮는 색 (style=fill) — 살색·검정은 피한다 (살색은 얼굴로, 검정은 그림자·잘림으로 읽힐 수 있다)
FILL = (128, 128, 128)
#: 블러 반지름 = 박스 짧은 변 / BLUR_DIVISOR (style=blur). 6 = 강함 — 얼굴 형태가 사라진다.
#  스크리닝 판정은 이 강도에서도 원본과 같았다 (2026-09-09 실측). 세그에는 안 쓰므로 세게 해도 된다.
BLUR_DIVISOR = 6


def face_box(
    landmarks: list[dict[str, Any]] | None, size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """얼굴 점 → 픽셀 박스 (left, top, right, bottom). 점이 3개 미만이면 None."""
    if not landmarks:
        return None
    w, h = size
    xs: list[float] = []
    ys: list[float] = []
    for i, p in enumerate(landmarks):
        if not p:
            continue
        idx = int(p.get("index", i))
        if idx not in FACE_INDICES:
            continue
        if float(p.get("visibility", 1.0)) < MIN_VISIBILITY:
            continue
        x, y = float(p["x"]) * w, float(p["y"]) * h
        if not (0 <= x < w and 0 <= y < h):
            continue  # 프레임 밖 (재정규화로 0~1 을 벗어난 점)
        xs.append(x)
        ys.append(y)
    if len(xs) < 3:
        return None

    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    face_w = max(x1 - x0, (y1 - y0) * 0.8, 12.0)  # 귀~귀 폭. 옆모습이면 세로로 대신 잡는다
    pad_x = face_w * 0.25
    top = y0 - face_w * 0.75  # 눈 위로 이마·머리 윗부분
    bottom = y1 + face_w * 0.6  # 입 아래로 턱 끝까지 (0.45 는 턱선이 남았다 — 실물 확인)
    return (
        int(max(0, x0 - pad_x)),
        int(max(0, top)),
        int(min(w, x1 + pad_x)),
        int(min(h, bottom)),
    )


def apply(
    img: Image.Image, landmarks: list[dict[str, Any]] | None, style: str = "blur"
) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    """얼굴 박스를 가린 **새 이미지**를 돌려준다. 원본은 건드리지 않는다.

    style="blur" : 강한 가우시안 블러 — 피부색·머리 윤곽은 남고 이목구비만 사라진다.
                   회색 덮기(fill)는 GPT 가 몸을 보는 판단까지 바꿨다 (상완 등급·점수 이동,
                   2026-09-09 실측). 사진처럼 보이는 쪽이 진단을 덜 흔든다.
    style="fill" : 회색 사각형. 익명화는 가장 확실하나 위 이유로 기본이 아니다.
    """
    box = face_box(landmarks, img.size)
    if box is None or box[2] <= box[0] or box[3] <= box[1]:
        return img, None
    out = img.copy()
    if style == "fill":
        ImageDraw.Draw(out).rectangle(box, fill=FILL)
    else:
        region = out.crop(box)
        radius = max(3, min(box[2] - box[0], box[3] - box[1]) // BLUR_DIVISOR)
        out.paste(region.filter(ImageFilter.GaussianBlur(radius)), box)
    return out, box
