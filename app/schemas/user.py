"""사용자 식별자 스키마 (F02).

⚠️ 로그인이 아니다. 추측 불가능한 UUID 하나가 유일한 방어선이다.
   자세한 제약은 docs/api-spec-v2.md F01 참조.
"""

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    user_id: str = Field(description="이후 모든 요청의 X-User-Id 헤더 값")
    is_pro_user: bool
    created_at: str
