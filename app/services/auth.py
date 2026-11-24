"""카카오 토큰 검증 및 즉시 폐기 가능한 리핏 Bearer 세션.

카카오 토큰 원문은 카카오 검증 요청에만 사용한다. 리핏 토큰 원문도 DB에 저장하지 않는다.
SDK/앱이 보낸 사용자 ID를 신뢰하거나 기존 익명 기록에 자동 연결하지 않는다.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.errors import ApiError
from app.services import db

KAKAO_TOKEN_INFO_URL = "https://kapi.kakao.com/v1/user/access_token_info"


class KakaoTokenInfo(BaseModel):
    id: int = Field(strict=True, gt=0, le=9223372036854775807)
    app_id: int = Field(strict=True, gt=0)
    expires_in: int = Field(strict=True, ge=0)


@dataclass(frozen=True)
class AuthSession:
    user_id: UUID
    token_hash: str


def unauthorized(code: str = "INVALID_ACCESS_TOKEN") -> ApiError:
    return ApiError(401, code, "로그인이 필요합니다. 다시 로그인해 주세요.")


def unavailable() -> ApiError:
    return ApiError(503, "AUTH_UNAVAILABLE", "로그인 서비스를 잠시 사용할 수 없습니다.")


async def verify_kakao_token(token: str) -> KakaoTokenInfo:
    if settings.kakao_app_id is None:
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "카카오 로그인 설정이 아직 완료되지 않았습니다.")
    try:
        async with httpx.AsyncClient(
            timeout=settings.kakao_timeout_sec, follow_redirects=False
        ) as client:
            response = await client.get(
                KAKAO_TOKEN_INFO_URL, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError:
        raise unavailable() from None

    if response.status_code == 401:
        raise unauthorized("INVALID_KAKAO_TOKEN")
    # 카카오의 내부 장애는 HTTP 400 / code=-1로도 온다. 잘못된 로그인과 구분한다.
    if response.status_code == 400:
        try:
            error = response.json()
        except ValueError:
            raise unavailable() from None
        if isinstance(error, dict) and error.get("code") in (-2, -401):
            raise unauthorized("INVALID_KAKAO_TOKEN")
    if response.status_code != 200:
        raise unavailable()
    try:
        info = KakaoTokenInfo.model_validate(response.json())
    except (ValueError, ValidationError):
        raise unavailable() from None
    if info.app_id != settings.kakao_app_id or info.expires_in == 0:
        raise unauthorized("INVALID_KAKAO_TOKEN")
    return info


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_login(info: KakaoTokenInfo) -> dict:
    token = "refit_" + secrets.token_urlsafe(32)
    try:
        row = (
            db.get_client()
            .rpc(
                "refit_kakao_login",
                {
                    "p_app_id": info.app_id,
                    "p_subject": str(info.id),
                    "p_token_hash": token_hash(token),
                    "p_ttl_seconds": settings.auth_session_ttl_sec,
                },
            )
            .execute()
            .data[0]
        )
    except Exception:
        # DB 예외에는 쿼리 등이 포함될 수 있으므로 인증 정보를 로그/응답에 옮기지 않는다.
        raise unavailable() from None
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": settings.auth_session_ttl_sec,
        "expires_at": row["expires_at"],
        "user": {key: row[key] for key in ("user_id", "is_pro_user", "created_at")},
    }


def authenticate(token: str) -> AuthSession:
    if not re.fullmatch(r"refit_[A-Za-z0-9_-]{43}", token):
        raise unauthorized()
    digest = token_hash(token)
    try:
        rows = db.get_client().rpc("refit_auth_session", {"p_token_hash": digest}).execute().data
    except Exception:
        raise unavailable() from None
    if not rows:
        raise unauthorized()
    return AuthSession(user_id=UUID(str(rows[0]["user_id"])), token_hash=digest)


def logout(session: AuthSession) -> None:
    try:
        db.get_client().table("auth_session").delete().eq(
            "token_hash", session.token_hash
        ).execute()
    except Exception:
        raise unavailable() from None
