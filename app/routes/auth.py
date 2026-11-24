"""카카오 토큰 교환과 현재 리핏 세션 로그아웃."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.deps import bearer
from app.schemas.auth import (
    AUTH_RESPONSES, AuthErrorResponse, KakaoLoginRequest, LoginResponse,
    RefreshRequest, RefreshTokenRequest,
)
from app.services import auth, rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/kakao",
    response_model=LoginResponse,
    summary="카카오 로그인 및 리핏 토큰 발급",
    responses={
        **AUTH_RESPONSES,
        400: {"model": AuthErrorResponse, "description": "요청 형식 오류"},
        429: {"model": AuthErrorResponse, "description": "로그인 시도 횟수 초과"},
    },
)
async def kakao_login(body: KakaoLoginRequest, request: Request, response: Response) -> dict:
    """앱에서 카카오 SDK 로그인 후 accessToken을 전달합니다.

    백엔드는 카카오에서 토큰의 앱 ID·사용자·유효성을 확인합니다.
    같은 카카오 계정은 같은 리핏 사용자로 연결됩니다. 이후 기존 API에는 응답의
    리핏 토큰을 `Authorization: Bearer <access_token>`으로 보냅니다.
    리핏 토큰은 JWT가 아니며 원문을 보관해 그대로 사용합니다.
    refresh_token도 안전하게 보관하고 만료 시 /auth/refresh로 자동 갱신합니다.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_limit.check(
        f"kakao_login:{client_ip}",
        settings.auth_login_rate_limit,
        settings.auth_login_rate_window_sec,
    )
    info = await auth.verify_kakao_token(body.access_token.get_secret_value())
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return await run_in_threadpool(auth.create_login, info)


@router.post(
    "/refresh", response_model=LoginResponse, summary="로그인 유지 및 토큰 자동 갱신",
    responses={**AUTH_RESPONSES, 429: {"model": AuthErrorResponse, "description": "갱신 요청 제한"}},
)
async def refresh_login(body: RefreshRequest, request: Request, response: Response) -> dict:
    """카카오 재로그인 없이 두 토큰을 갱신합니다. 액세스 Bearer는 필요하지 않습니다.

    앱은 갱신을 한 번에 하나만 실행하고 새 토큰 쌍을 함께 저장합니다.
    전송 전 request_id를 보관하고 응답 유실 시 같은 refresh_token/request_id로 재시도합니다.
    이미 사용한 토큰을 다른 request_id로 재사용하면 해당 로그인 세션 전체를 폐기합니다.
    refresh_token은 시간 경과만으로 만료되지 않습니다.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_limit.check(f"auth_refresh:{client_ip}", 60, 60)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return await run_in_threadpool(auth.refresh, body.refresh_token.get_secret_value(), body.request_id)


@router.post(
    "/logout",
    status_code=204,
    summary="현재 기기의 리핏 로그인 세션 종료",
    responses=AUTH_RESPONSES,
    openapi_extra={"security": [{"RefitBearer": []}, {}]},
)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    body: RefreshTokenRequest | None = None,
) -> Response:
    """리프레시 토큰 본문 또는 리핏 Bearer로 현재 로그인 세션 전체를 폐기합니다.

    앱에서는 액세스 토큰 만료와 무관한 refresh_token 본문 방식을 권장합니다.
    둘 다 보내면 본문의 리프레시 토큰이 지정하는 세션만 종료합니다.
    이미 폐기된 토큰의 재시도도 204입니다. 다른 기기의 세션·사진·계정은 유지합니다.

    카카오 SDK 로그아웃과 앱의 토큰·개인정보 캐시 정리는 앱에서 별도로 수행합니다.
    카카오 계정 전체 로그아웃/연결 해제/리핏 회원 탈퇴 API가 아닙니다.
    """
    auth.logout(
        credentials.credentials if credentials and body is None else None,
        body.refresh_token.get_secret_value() if body else None,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
