"""Supabase Storage 업로드 래퍼 + mock 분기."""

from app.config import settings


async def upload_image(
    bucket: str, path: str, data: bytes, content_type: str = "image/jpeg"
) -> str:
    """이미지를 Supabase Storage에 업로드하고 공개 URL을 반환한다."""
    if settings.use_mock:
        return f"https://mock.supabase.co/storage/v1/object/public/{bucket}/{path}"

    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    client.storage.from_(bucket).upload(path, data, {"content-type": content_type})
    return client.storage.from_(bucket).get_public_url(path)
