"""공통 응답 스키마."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(description="에러 코드", examples=["POSE_MISMATCH"])
    message: str = Field(description="사용자에게 그대로 보여줘도 되는 문구")
    detail: dict[str, Any] | None = Field(default=None, description="추가 정보")


class ErrorResponse(BaseModel):
    """모든 에러 응답의 형태."""

    error: ErrorBody


class SignedUrlItem(BaseModel):
    bucket: str
    path: str


class SignedUrlRequest(BaseModel):
    items: list[SignedUrlItem] = Field(description="발급할 대상 목록")
    expires_in: int | None = Field(
        default=None, description="만료 초. 미지정 시 서버 기본값(3600), 상한도 3600"
    )


class SignedUrlResult(SignedUrlItem):
    url: str
    expires_at: str


class SignedUrlResponse(BaseModel):
    items: list[SignedUrlResult]
