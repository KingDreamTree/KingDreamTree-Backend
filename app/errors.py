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


def not_found(what: str = "리소스") -> ApiError:
    """⚠️ 소유권 불일치도 이걸 쓴다.

    403을 주면 "그 id는 존재한다"는 사실이 새어나가 열거 공격의 힌트가 된다.
    """
    return ApiError(404, "NOT_FOUND", f"{what}를 찾을 수 없습니다.")


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
