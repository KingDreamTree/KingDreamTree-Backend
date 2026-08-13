"""F02 — 사용자 식별자 발급.

⚠️ 로그인이 아니다. `POST /users` 는 헤더 없이 호출되는 유일한 쓰기 엔드포인트다.
   발급된 UUID를 잃으면 복구 수단이 없다 (docs/api-spec-v2.md F01).
"""

from fastapi import APIRouter, status

from app.deps import CurrentUser
from app.schemas.user import UserResponse
from app.services import db

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
