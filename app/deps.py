"""FastAPI 의존성 — 인증(없음)과 소유권 검증.

⚠️ 이 프로젝트에는 로그인이 없다. 요청마다 X-User-Id 헤더로 사용자를 식별한다.
   따라서 **모든 자식 리소스 조회는 analysis_session.user_id 까지 조인해
   X-User-Id와 일치하는지 확인해야 한다.** 한 군데라도 빠뜨리면
   session_id만 알면 남의 데이터가 열린다.

   그 검증을 라우터마다 손으로 쓰지 말고 여기 의존성으로만 통과시킬 것.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Path

from app.errors import missing_user_id, not_found
from app.services import db


async def get_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> UUID:
    """헤더에서 user_id를 꺼내 형식만 검증한다 (DB 조회 없음)."""
    if not x_user_id:
        raise missing_user_id()
    try:
        return UUID(x_user_id)
    except ValueError:
        raise missing_user_id() from None


UserId = Annotated[UUID, Depends(get_user_id)]


async def get_current_user(user_id: UserId) -> dict[str, Any]:
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
