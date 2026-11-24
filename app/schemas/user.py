"""로그인한 리핏 사용자. UUID는 식별자이며 인증 수단이 아니다."""

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    user_id: str = Field(description="리핏 내부 사용자 UUID. 인증에는 Bearer 토큰을 사용합니다.")
    is_pro_user: bool
    created_at: str
