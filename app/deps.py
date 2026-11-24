"""FastAPI 공통 Bearer 인증과 리소스 소유권 검증."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.errors import not_found
from app.services import auth, db

bearer = HTTPBearer(
    auto_error=False,
    scheme_name="RefitBearer",
    description="POST /api/v1/auth/kakao에서 발급받은 리핏 access_token. 카카오 토큰이 아닙니다.",
)


def get_auth_session(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> auth.AuthSession:
    if credentials is None:
        raise auth.unauthorized("MISSING_ACCESS_TOKEN")
    return auth.authenticate(credentials.credentials)


CurrentAuthSession = Annotated[auth.AuthSession, Depends(get_auth_session)]


def get_user_id(session: CurrentAuthSession) -> UUID:
    """검증된 서버 세션에서만 내부 사용자 ID를 얻는다. X-User-Id는 받지 않는다."""
    return session.user_id


UserId = Annotated[UUID, Depends(get_user_id)]


def get_current_user(user_id: UserId) -> dict[str, Any]:
    """user_id가 실제로 존재하는지까지 확인한다."""
    user = db.get_user(user_id)
    if user is None:
        raise not_found("사용자")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


async def get_owned_session(
    user_id: UserId,
    session_id: Annotated[UUID, Path()],
) -> dict[str, Any]:
    """경로의 session_id가 이 사용자 것인지 확인한다.

    ⚠️ 소유권 불일치도 404를 반환한다. 403을 주면 "그 session_id는 존재한다"는
       사실이 새어나가 열거 공격의 힌트가 된다.
    """
    session = db.get_session(session_id)
    if session is None or str(session["user_id"]) != str(user_id):
        raise not_found("세션")
    return session


OwnedSession = Annotated[dict[str, Any], Depends(get_owned_session)]


async def get_owned_photo(
    user_id: UserId,
    photo_id: Annotated[UUID, Path()],
) -> dict[str, Any]:
    """photo → analysis_session → user_id 로 거슬러 올라가 확인한다."""
    photo = db.get_photo_by_id(photo_id)
    if photo is None:
        raise not_found("사진")
    session = db.get_session(UUID(str(photo["session_id"])))
    if session is None or str(session["user_id"]) != str(user_id):
        raise not_found("사진")
    return photo


OwnedPhoto = Annotated[dict[str, Any], Depends(get_owned_photo)]
