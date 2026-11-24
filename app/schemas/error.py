"""API 공통 오류 본문을 문서화하는 스키마."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
