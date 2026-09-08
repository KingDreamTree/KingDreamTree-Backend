"""OpenAI 로 나가는 사진의 얼굴을 가린다 — 팟 전용.

    face_mask.apply(img, landmarks, style) -> (가린 이미지, 박스 | None)

왜 팟에서, 왜 OpenAI 행만 (2026-09-09 결정)
    · 얼굴을 **세게** 가린 사진을 세그(Sapiens)에 넣으면 뭉개진 얼굴을 배경으로 분류해
      인물 픽셀(모든 부위 비율의 분모)이 7~15% 줄고 부위 비율이 틀어진다 (실측,
      scripts/measure_face_blur.py). 약하게 가리면 세그는 그대로지만 얼굴 윤곽이 남는다.
    · 그래서 세그는 **안 가린 가공본**, OpenAI 로 가는 세 번(스크리닝·부위·종합)은
      **가린 복사본**을 쓴다. 복사본은 가공 직후 한 번만 만들어 처리 내내 메모리에 두고
      (photo_source 의 vlm 변형), 끝나면 가공본과 함께 지운다.
    · 기본은 **얼굴 타원 안만 강한 블러**(style=blur). 회색 사각형(style=fill)도 남겨 뒀다.
      진단 동일성 실측(원본 2회·회색 2회·사각 블러·타원 블러, docs/pod-pipeline.md)에서
      어느 쪽도 원본 대비 흔들림 범위를 넘지 않았다. 블러가 기본인 이유는 사진처럼
      보여 "가림"이 품질 문제로 읽힐 여지가 적고, 타원이라 목·승모근 픽셀을 안 건드리기
      때문이다. 프롬프트에도 "얼굴은 일부러 가려져(흐리게 처리되어) 있다"를 알린다
      (app/prompts/photo_screening·part_diagnosis·overall_diagnosis).

얼굴 위치
    프론트가 등록 때 보낸 MediaPipe 포즈 랜드마크 중 얼굴 점(0 코, 1~6 눈, 7~8 귀,
    9~10 입)으로 잡는다. 랜드마크는 가공본(크롭 후) 기준으로 재정규화된 것을 받는다
    (images._recenter_landmarks). 귀~귀 폭을 얼굴 폭으로 보고 이마·턱까지 넓힌다.
    얼굴 점이 7개 미만(역광·모자·머리카락으로 눈·귀 가림)이면 코·입 몇 점만으로 박스가
    얼굴의 10% 크기로 쪼그라드는데도 "가렸다"고 나가던 구멍이 있었다(2026-09-09 검사).
    그래서 점이 부족하면 **어깨 폭(11·12)** 으로 얼굴 폭 하한을 잡고 점들의 중심에서
    위아래로 넓힌 보수 박스를 쓴다.

⚠️ 얼굴 점이 안 보이면(뒷모습·전부 프레임 밖) 가릴 얼굴이 없다고 보고 그대로 둔다 —
   None 을 돌려주므로 호출부가 로그로 남긴다. 랜드마크 자체가 없는 사진은 pod 모드
   등록에서 막히므로(pose_landmarks 필수) 정상 경로에서는 오지 않는다.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFilter

#: MediaPipe Pose 33점 중 얼굴에 해당하는 인덱스
FACE_INDICES: tuple[int, ...] = tuple(range(11))
#: 얼굴 점이 이만큼 보이면 정상 박스, 미만이면 어깨 폭 기준 보수 박스
FULL_FACE_POINTS = 7
#: 어깨(11, 12)
SHOULDER_INDICES: tuple[int, int] = (11, 12)
#: 이 값 미만은 "안 보임"으로 본다 (MediaPipe visibility 0~1)
MIN_VISIBILITY = 0.5
#: 얼굴 폭 하한 = 어깨 폭 × 이 값 (귀~귀 ≈ 어깨 폭의 0.37~0.45 — 정상 박스에는 안 걸린다)
FACE_TO_SHOULDER = 0.35
#: 덮는 색 (style=fill) — 살색·검정은 피한다 (살색은 얼굴로, 검정은 그림자·잘림으로 읽힐 수 있다)
FILL = (128, 128, 128)
#: 블러 반지름 = 박스 짧은 변 / BLUR_DIVISOR (style=blur). 6 = 강함 — 얼굴 형태가 사라진다.
#  스크리닝 판정은 이 강도에서도 원본과 같았다 (2026-09-09 실측). 세그에는 안 쓰므로 세게 해도 된다.
BLUR_DIVISOR = 6


def _points(
    landmarks: list[dict[str, Any]] | None,
    indices: tuple[int, ...],
    size: tuple[int, int],
) -> list[tuple[float, float]]:
    """지정 인덱스 중 보이고(visibility) 프레임 안에 있는 점들의 픽셀 좌표."""
    if not landmarks:
        return []
    w, h = size
    out: list[tuple[float, float]] = []
    for i, p in enumerate(landmarks):
        if not p:
            continue
        idx = int(p.get("index", i))
        if idx not in indices:
            continue
        if float(p.get("visibility", 1.0)) < MIN_VISIBILITY:
            continue
        x, y = float(p["x"]) * w, float(p["y"]) * h
        if not (0 <= x < w and 0 <= y < h):
            continue  # 프레임 밖 (재정규화로 0~1 을 벗어난 점)
        out.append((x, y))
    return out


def visible_face_points(
    landmarks: list[dict[str, Any]] | None, size: tuple[int, int]
) -> list[tuple[float, float]]:
    return _points(landmarks, FACE_INDICES, size)


def face_box(
    landmarks: list[dict[str, Any]] | None, size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """얼굴 점 → 픽셀 박스 (left, top, right, bottom). 점이 3개 미만이면 None."""
    pts = visible_face_points(landmarks, size)
    if len(pts) < 3:
        return None
    w, h = size
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    face_w = max(x1 - x0, (y1 - y0) * 0.8, 12.0)  # 귀~귀 폭. 옆모습이면 세로로 대신 잡는다

    if len(pts) < FULL_FACE_POINTS:
        # 보수 박스 — 눈·귀가 빠진 상태. 어깨 폭으로 얼굴 폭 하한을 잡고, 남은 점들의 중심에서
        # 좌우 대칭으로 벌린다. 어깨도 안 보이면 이미지 짧은 변의 12% 를 하한으로.
        shoulders = _points(landmarks, SHOULDER_INDICES, size)
        if len(shoulders) == 2:
            floor = abs(shoulders[0][0] - shoulders[1][0]) * FACE_TO_SHOULDER
        else:
            floor = min(w, h) * 0.12
        face_w = max(face_w, floor)
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        x0, x1 = cx - face_w / 2, cx + face_w / 2
        y0, y1 = cy - face_w * 0.35, cy + face_w * 0.35

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

    style="blur" : 박스에 내접하는 타원 안만 강한 가우시안 블러 — 피부색·머리 윤곽은 남고
                   이목구비만 사라진다. 목·어깨는 건드리지 않는다. **기본.**
    style="fill" : 회색 사각형. 익명화는 가장 확실하나 사진에 이물이 생긴다. 실측에서 진단
                   차이는 흔들림 범위 안이었지만 기본으로 두지 않는다.
    """
    box = face_box(landmarks, img.size)
    if box is None or box[2] <= box[0] or box[3] <= box[1]:
        return img, None
    out = img.copy()
    if style == "fill":
        ImageDraw.Draw(out).rectangle(box, fill=FILL)
    else:
        # ⚠️ 박스 전체가 아니라 박스에 내접하는 **타원 안만** 뭉갠다. 사각형은 아래 모서리가
        #    목 옆·승모근까지 내려와 승모근이 발달한 사람은 목과 경계가 흐려질 수 있다.
        #    타원은 턱 끝에서 좁아져 목·어깨 픽셀은 손대지 않는다 (2026-09-09 결정).
        region = out.crop(box)
        radius = max(3, min(region.size) // BLUR_DIVISOR)
        blurred = region.filter(ImageFilter.GaussianBlur(radius))
        mask = Image.new("L", region.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, region.size[0] - 1, region.size[1] - 1), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(2))  # 경계를 2px 만 부드럽게
        out.paste(blurred, box, mask)
    return out, box
