"""React Native 카카오 SDK → 리핏 인증 계약."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.schemas.user import UserResponse


class KakaoLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: SecretStr = Field(
        min_length=1,
        max_length=4096,
        description="카카오 SDK가 발급한 accessToken. 사용자 ID·앱 키·리핏 토큰이 아닙니다.",
    )

    @field_validator("access_token")
    @classmethod
    def token_characters(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value.get_secret_value()):
            raise ValueError("액세스 토큰 형식이 올바르지 않습니다.")
        return value


class LoginResponse(BaseModel):
    access_token: str = Field(description="리핏 전용 불투명 토큰. 해석하지 말고 Bearer로 전달.")
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = Field(description="리핏 토큰 수명(초). 만료 후 카카오 토큰으로 재로그인.")
    expires_at: datetime = Field(description="리핏 토큰 만료 시각(UTC).")
    user: UserResponse


class AuthErrorDetail(BaseModel):
    code: str
    message: str
    detail: dict | None = None


class AuthErrorResponse(BaseModel):
    error: AuthErrorDetail


AUTH_RESPONSES = {
    401: {
        "model": AuthErrorResponse,
        "description": "인증 정보 누락·만료·폐기 또는 유효하지 않은 토큰",
    },
    503: {"model": AuthErrorResponse, "description": "인증 저장소를 일시적으로 사용할 수 없음"},
}
