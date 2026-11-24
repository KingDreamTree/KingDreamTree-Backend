"""로그인한 사용자 조회 및 회원 탈퇴. 익명 UUID 발급은 종료했다."""

import logging
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.deps import CurrentUser
from app.errors import ApiError
from app.schemas.auth import AUTH_RESPONSES
from app.schemas.user import UserResponse
from app.services import db, storage

log = logging.getLogger("routes.users")

router = APIRouter(tags=["users"], responses=AUTH_RESPONSES)


@router.post(
    "/users",
    response_model=UserResponse,
    deprecated=True,
    summary="로그인한 사용자 확인 (GET /users/me 사용 권장)",
)
def create_user(user: CurrentUser) -> UserResponse:
    """익명 사용자를 생성하지 않습니다. 계정 생성은 카카오 로그인에 포함됩니다."""
    return UserResponse(**user)


@router.get(
    "/users/me",
    response_model=UserResponse,
    summary="로그인한 내 정보 조회",
)
async def get_me(user: CurrentUser) -> UserResponse:
    """저장된 리핏 토큰으로 앱 진입 시 로그인 상태와 내 정보를 확인합니다."""
    return UserResponse(**user)


@router.delete(
    "/users/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="계정 + 전 데이터 삭제",
)
async def delete_me(user: CurrentUser) -> Response:
    """⚠️ **Storage 를 먼저, DB 를 나중에** 지운다.

    FK CASCADE 는 Storage 파일을 지우지 않는다. 경로 최상위를 `{user_id}/` 로
    나눠둔 이유가 이것이고, DB 를 먼저 지우면 어느 경로를 지워야 하는지 알 수
    없게 된다.

    ⚠️ 사람 사진을 다루므로 soft delete 를 쓰지 않는다. 삭제 요청은 실제로 지운다.
    """
    user_id = UUID(str(user["user_id"]))

    try:
        removed = storage.delete_user_files(user_id)
    except Exception as e:  # noqa: BLE001
        # ⚠️ Storage 삭제가 실패하면 DB는 건드리지 않는다.
        #    행이 사라지면 남은 파일을 추적할 방법이 없어진다.
        log.exception("Storage 삭제 실패 — DB는 유지한다: user_id=%s", user_id)
        raise ApiError(500, "INTERNAL_ERROR", "데이터 삭제 중 문제가 발생했습니다.") from e

    log.info("Storage 삭제 완료 user_id=%s %s", user_id, removed)
    db.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
