"""포즈 페이로드 검증 + 촬영 판정.

⚠️ **서버는 MediaPipe를 돌리지 않는다.** 랜드마크 추출과 P/F 점수 계산은 프론트가 한다.
   서버가 하는 일은 두 가지다.
     (1) 받은 값의 형식·범위 검사
     (2) .env 임계값으로 통과/거부 판정

왜 이렇게 나눴나
    측정을 서버가 다시 하면 프론트와 MediaPipe 버전·구현이 달라 값이 어긋나고,
    "화면에서는 92%였는데 저장이 거부되는" 경험이 생긴다.
    반대로 판정까지 프론트에 맡기면 임계값이 프론트에 하드코딩돼
    THRESHOLD / F_MIN 을 .env 로 뺀 의미가 없어진다.
    **측정은 프론트, 정책은 서버.**

    대신 값 조작은 막지 못한다. 로그인이 없는 MVP에서 자기 사진 점수를 조작할
    동기가 없고(진단 품질만 나빠진다), 조작해도 남의 데이터에는 닿지 않는다.
    실서비스로 가면 이 판단을 다시 해야 한다.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.errors import invalid_request, multi_person_error, pose_mismatch
from app.schemas.enums import PoseScaleBasis

#: MediaPipe Pose 랜드마크 개수. 33개가 아니면 다른 모델의 출력이다.
LANDMARK_COUNT = 33

#: MediaPipe Pose 의 좌/우 대칭 인덱스 쌍.
#  0(코)만 중앙이고 나머지는 전부 짝이 있다.
#  ⚠️ 좌우 반전을 되돌릴 때 x좌표만 뒤집으면 안 된다. "왼쪽 어깨"라는 이름표까지
#     같이 바꿔야 한다 — 거울 사진에서 MediaPipe가 왼쪽이라고 부른 건 실제 오른쪽이다.
LR_PAIRS: tuple[tuple[int, int], ...] = (
    (1, 4),  # eye_inner
    (2, 5),  # eye
    (3, 6),  # eye_outer
    (7, 8),  # ear
    (9, 10),  # mouth
    (11, 12),  # shoulder
    (13, 14),  # elbow
    (15, 16),  # wrist
    (17, 18),  # pinky
    (19, 20),  # index
    (21, 22),  # thumb
    (23, 24),  # hip
    (25, 26),  # knee
    (27, 28),  # ankle
    (29, 30),  # heel
    (31, 32),  # foot_index
)


# --------------------------------------------------------------------------- #
# 파싱 / 검증
# --------------------------------------------------------------------------- #


def parse_landmarks(raw: str | None) -> list[dict[str, Any]]:
    """multipart 로 넘어온 JSON 문자열 → 랜드마크 목록.

    형식만 본다. "이 값이 진짜인가"는 확인할 수 없고, 확인할 필요도 없다(모듈 주석).
    """
    if not raw or not raw.strip():
        raise pose_mismatch(
            "사진에서 사람을 찾지 못했습니다. 전신이 보이도록 다시 촬영해주세요.",
            reason="NO_PERSON",
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise invalid_request("pose_landmarks 가 올바른 JSON이 아닙니다.") from None

    if not isinstance(parsed, list):
        raise invalid_request("pose_landmarks 는 배열이어야 합니다.")
    if not parsed:
        raise pose_mismatch(
            "사진에서 사람을 찾지 못했습니다. 전신이 보이도록 다시 촬영해주세요.",
            reason="NO_PERSON",
        )
    if len(parsed) != LANDMARK_COUNT:
        raise invalid_request(
            f"pose_landmarks 는 {LANDMARK_COUNT}개여야 합니다 (MediaPipe Pose 기준).",
            {"got": len(parsed), "expected": LANDMARK_COUNT},
        )

    out: list[dict[str, Any]] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise invalid_request(f"pose_landmarks[{i}] 가 객체가 아닙니다.")
        try:
            x = float(item["x"])
            y = float(item["y"])
        except (KeyError, TypeError, ValueError):
            raise invalid_request(f"pose_landmarks[{i}] 에 x/y 가 없습니다.") from None

        # ⚠️ 정규화 좌표(0~1)를 기대한다. 픽셀 좌표를 그대로 보내면 여기서 걸린다.
        #    MediaPipe는 화면 밖으로 살짝 나간 관절에 음수/1초과를 주므로 여유를 둔다.
        if not (-0.5 <= x <= 1.5 and -0.5 <= y <= 1.5):
            raise invalid_request(
                f"pose_landmarks[{i}] 좌표가 정규화 범위를 크게 벗어났습니다. "
                "픽셀 좌표가 아니라 0~1 정규화 좌표를 보내주세요.",
                {"index": i, "x": x, "y": y},
            )

        out.append(
            {
                "index": int(item.get("index", i)),
                "x": x,
                "y": y,
                "z": float(item.get("z", 0.0)),
                "visibility": float(item.get("visibility", 0.0)),
            }
        )
    return out


def unmirror_landmarks(landmarks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """거울 사진의 랜드마크를 비반전 기준으로 되돌린다.

    두 가지를 함께 해야 한다.
      1) x 좌표를 뒤집는다 (x → 1-x)
      2) 좌/우 이름표를 맞바꾼다 (LR_PAIRS)

    ⚠️ 1번만 하면 좌표는 맞는데 "왼쪽 어깨"가 여전히 오른쪽 어깨를 가리킨다.
       2번만 하면 그 반대다. 둘 다 해야 원래대로 돌아온다.
    """
    flipped = [{**lm, "x": 1.0 - lm["x"]} for lm in landmarks]

    for left, right in LR_PAIRS:
        if left < len(flipped) and right < len(flipped):
            flipped[left], flipped[right] = flipped[right], flipped[left]

    # index 필드는 배열 위치와 같은 의미이므로 스왑 후 다시 매긴다.
    for i, lm in enumerate(flipped):
        lm["index"] = i
    return flipped


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def ensure_single_person(multi_person: bool) -> None:
    if multi_person:
        raise multi_person_error()


def ensure_same_scale_basis(reference_basis: str | None, user_basis: str | None) -> None:
    """레퍼런스와 사용자가 같은 기준으로 쟀는지 확인한다.

    ⚠️ 각자 다른 기준(TORSO vs HIP_KNEE)으로 정규화한 점수는 비교가 무의미하다.
       레퍼런스 값을 강제하고, 사용자 쪽이 그 기준을 못 쟀으면 재촬영을 요구한다.
    """
    if reference_basis is None:
        return
    if user_basis != reference_basis:
        raise pose_mismatch(
            "몸이 화면에 다 나오도록 서주세요. 레퍼런스와 같은 기준으로 잴 수 없습니다.",
            reason="FRAMING",
            detail={"reference_scale_basis": reference_basis, "user_scale_basis": user_basis},
        )


def judge_user_photo(
    pose_similarity: float,
    framing_score: float,
    scale_basis: PoseScaleBasis | str,
    reference_scale_basis: str | None,
    multi_person: bool,
) -> None:
    """사용자 사진의 저장 가부를 판정한다. 통과하면 아무것도 반환하지 않는다.

    순서가 의미를 갖는다 — 프레이밍이 깨진 상태의 포즈 점수는 신뢰할 수 없으므로
    프레이밍을 먼저 본다. 안내 문구도 달라야 한다.
      "몸이 화면에 다 나오게 서주세요" vs "포즈를 맞춰주세요"
    """
    ensure_single_person(multi_person)
    ensure_same_scale_basis(reference_scale_basis, str(scale_basis))

    detail = {
        "pose_similarity": pose_similarity,
        "framing_score": framing_score,
        "threshold": settings.pose_threshold,
        "f_min": settings.framing_f_min,
    }

    if framing_score < settings.framing_f_min:
        raise pose_mismatch(
            "몸이 화면에 다 나오도록 서주세요.",
            reason="FRAMING",
            detail=detail,
        )

    if pose_similarity < settings.pose_threshold:
        raise pose_mismatch(
            "레퍼런스와 포즈가 충분히 일치하지 않습니다. 다시 촬영해주세요.",
            reason="POSE",
            detail=detail,
        )
