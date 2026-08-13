"""Supabase DB CRUD 래퍼 + mock 분기."""

import uuid
from typing import Any

from app.config import settings

_MOCK_ID = "a1b2c3d4-0000-0000-0000-000000000000"


async def create_analysis(
    user_image_url: str,
    ref_image_url: str,
) -> str:
    """analysis 레코드를 생성하고 UUID를 반환한다."""
    if settings.use_mock:
        return _MOCK_ID

    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    data = {"user_image_url": user_image_url, "ref_image_url": ref_image_url}
    result = client.table("analysis").insert(data).execute()
    return result.data[0]["id"]


async def update_analysis(analysis_id: str, **fields: Any) -> None:
    """analysis 레코드를 부분 업데이트한다."""
    if settings.use_mock:
        return

    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    client.table("analysis").update(fields).eq("id", analysis_id).execute()
