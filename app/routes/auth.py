"""카카오 토큰 교환과 현재 리핏 세션 로그아웃."""

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.deps import CurrentAuthSession
from app.schemas.auth import AUTH_RESPONSES, AuthErrorResponse, KakaoLoginRequest, LoginResponse
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
    만료되면 SDK에서 유효한 카카오 토큰을 얻어 이 API를 다시 호출합니다.
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
    "/logout",
    status_code=204,
    summary="현재 기기의 리핏 로그인 세션 종료",
    responses=AUTH_RESPONSES,
)
def logout(session: CurrentAuthSession) -> Response:
    """현재 리핏 토큰을 즉시 폐기합니다. 다른 기기의 세션·사진·계정은 유지합니다.

    카카오 SDK 로그아웃과 앱의 토큰·개인정보 캐시 정리는 앱에서 별도로 수행합니다.
    카카오 계정 전체 로그아웃/연결 해제/리핏 회원 탈퇴 API가 아닙니다.
    """
    auth.logout(session)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
