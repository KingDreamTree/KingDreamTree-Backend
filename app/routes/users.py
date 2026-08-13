"""F02 — 사용자 식별자 발급.

⚠️ 로그인이 아니다. `POST /users` 는 헤더 없이 호출되는 유일한 쓰기 엔드포인트다.
   발급된 UUID를 잃으면 복구 수단이 없다 (docs/api-spec-v2.md F01).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.deps import CurrentUser
from app.errors import ApiError
from app.schemas.user import UserResponse
from app.services import db, storage

log = logging.getLogger("routes.users")

router = APIRouter(tags=["users"])


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="사용자 식별자 발급 (헤더 불필요)",
)
async def create_user() -> UserResponse:
    row = db.create_user()
    return UserResponse(**row)


@router.get(
    "/users/me",
    response_model=UserResponse,
    summary="저장된 식별자 유효성 확인",
)
async def get_me(user: CurrentUser) -> UserResponse:
    """⚠️ 앱 진입 시 먼저 호출할 것.

    DB가 초기화됐는데 프론트 로컬 스토리지에 옛 id가 남아 있으면
    이후 모든 요청이 404로 떨어진다. 여기서 한 번에 걸러낸다.
    """
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
