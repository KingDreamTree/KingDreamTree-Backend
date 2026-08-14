"""API 에러 규약.

모든 에러 응답은 아래 형태로 통일한다.

    {"error": {"code": "POSE_MISMATCH", "message": "...", "detail": {...}}}

⚠️ `message`는 사용자에게 그대로 노출된다. 스택 트레이스·모델 경로·API 키를 넣지 말 것.
"""

from typing import Any


class ApiError(Exception):
    """도메인 에러. main.py의 예외 핸들러가 위 형태로 직렬화한다."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return {"error": body}


# --------------------------------------------------------------------------- #
# 자주 쓰는 것들
# --------------------------------------------------------------------------- #


def missing_user_id() -> ApiError:
    return ApiError(401, "MISSING_USER_ID", "X-User-Id 헤더가 필요합니다.")


def invalid_user_id() -> ApiError:
    """헤더는 왔는데 UUID 형식이 아니다.

    ⚠️ MISSING_USER_ID 와 나눈다. "헤더가 필요합니다"라고 하면 프론트는
       헤더를 안 보낸 줄 알고 엉뚱한 데를 고친다. 실제로는 저장된 값이 깨진 것이라
       재발급이 답이다.
    """
    return ApiError(401, "INVALID_USER_ID", "X-User-Id 형식이 올바르지 않습니다.")


def not_found(what: str = "리소스") -> ApiError:
    """⚠️ 소유권 불일치도 이걸 쓴다.

    403을 주면 "그 id는 존재한다"는 사실이 새어나가 열거 공격의 힌트가 된다.
    """
    return ApiError(404, "NOT_FOUND", f"{what}를 찾을 수 없습니다.")


def no_active_session() -> ApiError:
    """진행 중인 세션이 없다. **오류라기보다 상태다.**

    ⚠️ NOT_FOUND 와 코드를 나눈다. 둘 다 404 라서 코드가 같으면 프론트가
       "계정이 사라졌다(→ 재발급)"와 "아직 시작 안 했다(→ 시작 화면)"를
       구분하지 못한다. 앱 진입 때 어느 화면으로 보낼지가 여기서 갈린다.
    """
    return ApiError(404, "NO_ACTIVE_SESSION", "진행 중인 분석이 없습니다.")


def invalid_request(message: str, detail: dict[str, Any] | None = None) -> ApiError:
    return ApiError(400, "INVALID_REQUEST", message, detail)


def precondition_not_met(message: str, detail: dict[str, Any] | None = None) -> ApiError:
    return ApiError(409, "PRECONDITION_NOT_MET", message, detail)


def file_too_large(limit_bytes: int) -> ApiError:
    return ApiError(
        413,
        "FILE_TOO_LARGE",
        f"파일 크기는 {limit_bytes // 1024 // 1024}MB 이하여야 합니다.",
        {"limit_bytes": limit_bytes},
    )


def unsupported_media_type(got: str | None, allowed: list[str]) -> ApiError:
    return ApiError(
        415,
        "UNSUPPORTED_MEDIA_TYPE",
        "지원하지 않는 이미지 형식입니다.",
        {"got": got, "allowed": allowed},
    )


def active_session_exists(session_id: str) -> ApiError:
    """⚠️ detail.session_id 로 이어서 진행할 수 있게 알려준다.

    UNIQUE (user_id) WHERE status='ACTIVE' 제약 때문에 발생한다.
    """
    return ApiError(
        409,
        "ACTIVE_SESSION_EXISTS",
        "이미 진행 중인 분석이 있습니다.",
        {"session_id": session_id},
    )


def pose_mismatch(
    message: str,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> ApiError:
    """포즈·프레이밍 미달.

    ⚠️ reason 을 나누는 이유는 화면 안내 문구가 달라야 하기 때문이다.
       POSE("포즈를 맞춰주세요") / FRAMING("몸이 다 나오게 서주세요") /
       NO_PERSON("사람이 안 보입니다") 는 사용자가 취해야 할 행동이 각각 다르다.
    """
    return ApiError(422, "POSE_MISMATCH", message, {**(detail or {}), "reason": reason})


def multi_person_error() -> ApiError:
    return ApiError(
        422,
        "MULTI_PERSON",
        "사진에 여러 사람이 있습니다. 혼자 나오도록 촬영해주세요.",
    )


def internal_error() -> ApiError:
    """예상 못 한 서버 오류.

    ⚠️ 원인을 message 에 넣지 않는다. 스택 트레이스·쿼리·경로가 사용자 화면과
       프론트 로그로 새어나간다. 원인은 서버 로그에만 남긴다.
    """
    return ApiError(
        500, "INTERNAL_ERROR", "서버에서 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
    )


def unsuitable_photo(message: str, detail: dict[str, Any] | None = None) -> ApiError:
    """사진으로 실루엣 비교가 안 된다고 판정된 경우.

    ⚠️ POSE_MISMATCH 와 다른 코드를 쓴다. 사용자가 고쳐야 할 게 다르다 —
       포즈는 자세를 바꾸는 것이고, 이건 옷을 갈아입거나 다시 찍는 것이다.
    """
    return ApiError(422, "UNSUITABLE_PHOTO", message, detail)
