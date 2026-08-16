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

#: MediaPipe Pose Landmarker 의 33개 랜드마크. 인덱스 = 배열 위치.
#: 출처 — 공식 문서
#:   https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker
LANDMARK_NAMES: tuple[str, ...] = (
    "nose",  # 0 — 유일하게 짝이 없는 중앙 지점
    "left_eye_inner",  # 1
    "left_eye",  # 2
    "left_eye_outer",  # 3
    "right_eye_inner",  # 4
    "right_eye",  # 5
    "right_eye_outer",  # 6
    "left_ear",  # 7
    "right_ear",  # 8
    "mouth_left",  # 9
    "mouth_right",  # 10
    "left_shoulder",  # 11
    "right_shoulder",  # 12
    "left_elbow",  # 13
    "right_elbow",  # 14
    "left_wrist",  # 15
    "right_wrist",  # 16
    "left_pinky",  # 17
    "right_pinky",  # 18
    "left_index",  # 19
    "right_index",  # 20
    "left_thumb",  # 21
    "right_thumb",  # 22
    "left_hip",  # 23
    "right_hip",  # 24
    "left_knee",  # 25
    "right_knee",  # 26
    "left_ankle",  # 27
    "right_ankle",  # 28
    "left_heel",  # 29
    "right_heel",  # 30
    "left_foot_index",  # 31
    "right_foot_index",  # 32
)

#: 랜드마크 개수. 33개가 아니면 다른 모델의 출력이다.
LANDMARK_COUNT = len(LANDMARK_NAMES)


def _left_right_pairs() -> tuple[tuple[int, int], ...]:
    """이름에서 좌/우 짝을 뽑는다.

    ⚠️ 번호쌍을 손으로 적으면 오타가 나도 아무 데서도 안 걸린다. 좌우가 한 쌍만
       어긋나도 거울 사진 보정이 조용히 틀리므로, 공식 이름 목록에서 유도한다.
    """
    index_of = {name: i for i, name in enumerate(LANDMARK_NAMES)}
    pairs: list[tuple[int, int]] = []

    for name, i in index_of.items():
        if name.startswith("left_"):
            twin = "right_" + name[len("left_") :]
        elif name.endswith("_left"):
            twin = name[: -len("_left")] + "_right"
        else:
            continue  # nose 처럼 짝이 없는 중앙 지점
        if twin in index_of:
            pairs.append((i, index_of[twin]))

    return tuple(sorted(pairs))


#: 좌/우 대칭 쌍. 0(코)만 짝이 없고 나머지 32개가 16쌍을 이룬다.
#: ⚠️ 좌우 반전을 되돌릴 때 x좌표만 뒤집으면 안 된다. "왼쪽 어깨"라는 이름표까지
#:    같이 바꿔야 한다 — 거울 사진에서 MediaPipe가 왼쪽이라고 부른 건 실제 오른쪽이다.
LR_PAIRS: tuple[tuple[int, int], ...] = _left_right_pairs()


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
        #
        #    경계가 ±10 으로 넓은 이유 — MediaPipe 는 **화면에 없는 관절도 추측해서
        #    좌표를 준다.** 골반에서 잘린 상체 사진이면 발목이 y≈2~3 으로 나온다
        #    (실사례: 레퍼런스 상체 사진이 [27]=발목에서 반려됐다). 그 좌표는
        #    visibility 가 낮아 이후 모든 계산에서 어차피 빠지는 값이라, 이것 때문에
        #    업로드를 막으면 "전신 사진만 올릴 수 있는" 의도치 않은 제약이 된다 —
        #    HIP_KNEE 기준(하체 전용)과 2차 검사의 부분 신체 허용 규칙이 무의미해진다.
        #    픽셀 좌표 실수는 수백~수천 단위라 ±10 로도 확실히 걸린다. 두 경우의
        #    규모 차이가 커서 이 사이 어디에 선을 그어도 동작은 같다.
        if not (-10.0 <= x <= 10.0 and -10.0 <= y <= 10.0):
            raise invalid_request(
                f"pose_landmarks[{i}] 좌표가 정규화 범위를 크게 벗어났습니다. "
                "픽셀 좌표가 아니라 0~1 정규화 좌표를 보내주세요.",
                {"index": i, "x": x, "y": y},
            )

        out.append(
            {
                # ⚠️ 들어온 index 를 믿지 않고 배열 위치로 덮어쓴다. 뒤집기 경로에서는
                #    이미 다시 매기고 있었는데 일반 경로만 입력값을 그대로 써서,
                #    같은 필드가 경로에 따라 의미가 달라지고 있었다.
                "index": i,
                "x": x,
                "y": y,
                "z": float(item.get("z", 0.0)),
                # ⚠️ visibility 누락 기본값은 1.0(보임) — 프론트 규약(pose-score.js
                #    vis(): 안 주면 1)과 같아야 한다. 0.0 으로 저장하면 이 랜드마크를
                #    GET /photos/reference 로 되돌려받은 촬영 화면이 **모든 관절을
                #    "안 보임"으로 걸러** 전신이 다 나와도 영구 판정불가가 된다.
                "visibility": float(item.get("visibility", 1.0)),
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


def _ensure_range(name: str, value: float, low: float, high: float) -> None:
    """점수가 약속한 범위 안인지 확인한다.

    ⚠️ 이 검사가 없으면 **틀린 값이 판정을 그냥 통과한다.** 실제로 네 경우가 그랬다.
         framing_score 를 0~100 으로 착각해 65 를 보냄  → 하한(0.65)을 여유롭게 넘어 통과
         pose_similarity=150                          → 통과
         pose_similarity=NaN                          → NaN 비교는 전부 False 라 통과
         facing_delta=-5                              → 통과
       그리고 저장 단계에서 DB CHECK 에 걸려 **500** 이 난다. 사용자에게는 원인을
       알 수 없는 서버 오류로 보이고, 프론트는 자기 단위 실수를 눈치채지 못한다.

    ⚠️ `not (low <= value <= high)` 로 쓴다. NaN 은 어떤 비교에도 False 라
       이 형태여야 걸린다 (`value < low or value > high` 로 쓰면 NaN 이 빠져나간다).
    """
    if not (low <= value <= high):
        raise invalid_request(
            f"{name} 는 {low}~{high} 범위여야 합니다.",
            {"field": name, "got": value, "min": low, "max": high},
        )


def ensure_single_person(multi_person: bool) -> None:
    if multi_person:
        raise multi_person_error()


def ensure_observation_ranges(
    pose_oks: float | None = None,
    person_area_ratio: float | None = None,
) -> None:
    """관찰용 필드의 범위 검사 — 판정에는 안 쓰지만 저장은 되므로 여기서 잡는다.

    ⚠️ 이 검사가 없으면 단위 착오(oks 를 0~100 으로 보내는 등)가 판정을 통과한
       **뒤** DB CHECK 에서 500 으로 터진다. 그 시점엔 이전 사진이 이미 교체돼
       있어 사용자가 무사진 상태로 남는다 — 판정 전에 400 으로 돌려보내야
       프론트가 자기 실수를 알 수 있다.
    """
    if pose_oks is not None:
        _ensure_range("pose_oks", pose_oks, 0.0, 1.0)
    if person_area_ratio is not None:
        _ensure_range("pose_person_area_ratio", person_area_ratio, 0.0, 1.0)


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


def criteria() -> dict[str, float | int]:
    """프론트가 실시간 화면에서 쓸 판정 기준.

    ⚠️ 프론트에 숫자를 하드코딩하면 서버에서 조정한 순간 어긋난다 —
       화면에서는 통과인데 저장이 거부되는 상황이 생긴다. 값을 내려준다.
    """
    return {
        "tol_deg": settings.pose_tol_deg,
        "hard_tol_deg": settings.pose_hard_tol_deg,
        "threshold": settings.pose_threshold,
        "f_min": settings.framing_f_min,
        "f_hard": settings.framing_hard_min,
        "min_seg_ratio": settings.pose_min_seg_ratio,
        "min_visible_angles": settings.pose_min_visible_angles,
        "min_visibility": settings.pose_min_visibility,
        "min_ref_coverage": settings.pose_min_ref_coverage,
        "n_hold": settings.pose_n_hold,
    }


def judge_user_photo(
    pose_similarity: float,
    framing_score: float,
    scale_basis: PoseScaleBasis | str,
    reference_scale_basis: str | None,
    multi_person: bool,
    facing_delta: float = 0.0,
) -> None:
    """사용자 사진의 저장 가부를 판정한다. 통과하면 아무것도 반환하지 않는다.

    산식은 docs/pose-scoring.md 참조. 여기서는 **임계값 비교만** 한다.

    ⚠️ **순서가 의미를 갖는다.** 프레이밍이 깨진 상태의 자세 점수는 믿을 수 없고,
       안내 문구가 각각 달라야 한다 — 사용자가 취해야 할 행동이 다르기 때문이다.
         FRAMING "비슷한 거리에서 다시" / POSE "포즈를 맞춰주세요"

    ⚠️ 거리(framing)는 **f_hard 로만 막는다.** f_min 은 촬영 화면 유도용이다.
       거리 차이는 몸통 길이 정규화로 상쇄되므로, 유도선에서 막으면 고쳐도
       이득이 없는 이유로 사용자를 돌려보내게 된다. 자세한 근거는 config.py 참조.

    ⚠️ facing_delta(몸통 방향 차이)는 **더 이상 판정에 쓰지 않는다** (2026-08-14).
       어깨폭 잡음으로 오발이 잦아 "육안으로 비슷한데 반려"를 만들었다.
       값은 계속 받아 저장한다 — "돌아간 사진이 실제로 진단을 얼마나 망치나"를
       나중에 데이터로 확인하고, 필요하면 관문을 되살리기 위해서다.
    """
    # ⚠️ 임계값 비교보다 **먼저** 범위를 본다. 단위를 착각한 값은 "미달"이 아니라
    #    "잘못 보낸 것"이고, 사용자에게 재촬영을 시킬 일이 아니라 프론트가 고칠 일이다.
    _ensure_range("pose_similarity", pose_similarity, 0.0, 100.0)
    _ensure_range("framing_score", framing_score, 0.0, 1.0)
    # ⚠️ 상한이 1.0 이었는데 **버그였다.** facing_delta 는 레퍼런스 비율로
    #    나눈 값이라, 레퍼런스가 많이 돌아가 있으면(비율이 작으면) 1 을 넘는다.
    #    측면 레퍼런스에 정면으로 선 사용자가 "방향이 다릅니다" 대신
    #    "잘못 보낸 값" 400 을 받고 있었다 — 고칠 수 없는 에러를 본 셈이다.
    #
    #    10.0 은 판정 기준이 아니라 **단위 착오를 잡는 sanity bound** 다.
    #    (퍼센트로 보내면 25 가 오므로 여전히 걸린다)
    _ensure_range("facing_delta", facing_delta, 0.0, 10.0)

    ensure_single_person(multi_person)
    ensure_same_scale_basis(reference_scale_basis, str(scale_basis))

    detail = {
        "pose_similarity": pose_similarity,
        "framing_score": framing_score,
        "facing_delta": facing_delta,  # 관찰용 — 판정에는 안 쓴다
        "threshold": settings.pose_threshold,
        "f_min": settings.framing_f_min,
        "f_hard": settings.framing_hard_min,
    }

    # ⚠️ **거리는 유도 기준(f_min)이 아니라 거부 기준(f_hard)으로 막는다.**
    #    부위 굵기를 몸통 길이로 나눠 비교하므로 거리 차이는 계산에서 상쇄된다 —
    #    2배 가까이 서면 팔뚝도 2배, 몸통도 2배라 비율은 같다.
    #    거리가 진짜 문제가 되는 두 경우는 각각 다른 곳이 잡는다:
    #      원근 왜곡 → 2차 검사 PERSPECTIVE_MISMATCH / 픽셀 부족 → 부위별 TOO_SMALL
    #    여기서 f_min 으로 막으면 **고쳐도 이득이 없는 이유로 사용자를 돌려보낸다.**
    #    f_min 은 촬영 화면 유도에만 쓴다(GET /pose-criteria 로 내려준다).
    if framing_score < settings.framing_hard_min:
        raise pose_mismatch(
            "레퍼런스와 촬영 거리가 너무 다릅니다. 비슷한 거리에서 다시 촬영해주세요.",
            reason="FRAMING",
            detail=detail,
        )

    # ⚠️ FACING(몸통 방향) 관문은 여기 있었다 — 2026-08-14 에 뺐다.
    #    어깨폭/몸통길이 비율이 잡음에 민감해 육안으로 비슷한 사진을 반려했다.
    #    facing_delta 는 계속 받아 저장하므로(위 detail), 데이터가 쌓이면
    #    "돌아간 사진이 진단을 망치는 선"을 실측해서 관문을 되살릴 수 있다.

    if pose_similarity < settings.pose_threshold:
        raise pose_mismatch(
            "레퍼런스와 포즈가 충분히 일치하지 않습니다. 다시 촬영해주세요.",
            reason="POSE",
            detail=detail,
        )
