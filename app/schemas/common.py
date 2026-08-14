"""공통 응답 스키마."""

from pydantic import BaseModel, Field


# ⚠️ 에러 응답 스키마(ErrorBody/ErrorResponse)는 여기 두지 않는다.
#    형태를 실제로 만들어 내보내는 곳은 app/errors.py 의 ApiError.to_dict() 하나다.
#    같은 모양을 두 군데 적으면 한쪽만 고쳐져 어긋난다. (2026-08-14 제거)
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
