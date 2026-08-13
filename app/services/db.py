"""Supabase 클라이언트 + 공통 쿼리.

⚠️ 클라이언트는 모듈 레벨 싱글턴이다. 요청마다 create_client를 부르면
   t3.large에서 커넥션이 쌓인다.
"""

from functools import lru_cache
from typing import Any
from uuid import UUID

from supabase import Client, create_client

from app.config import settings
from app.schemas.enums import PhotoKind, SessionStatus


@lru_cache(maxsize=1)
def get_client() -> Client:
    """service_role(secret) 키로 만든 Supabase 클라이언트. RLS를 우회한다."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 설정되지 않았습니다. .env를 확인하세요."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #


def create_user() -> dict[str, Any]:
    return get_client().table("users").insert({}).execute().data[0]


def get_user(user_id: UUID) -> dict[str, Any] | None:
    rows = get_client().table("users").select("*").eq("user_id", str(user_id)).execute().data
    return rows[0] if rows else None


def delete_user(user_id: UUID) -> None:
    """⚠️ Storage 파일은 CASCADE로 지워지지 않는다. storage.delete_user_files를 먼저 부를 것."""
    get_client().table("users").delete().eq("user_id", str(user_id)).execute()


# --------------------------------------------------------------------------- #
# analysis_session — 모든 소유권 검증의 기준점
# --------------------------------------------------------------------------- #


def get_session(session_id: UUID) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table("analysis_session")
        .select("*")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )
    return rows[0] if rows else None


def get_active_session(user_id: UUID) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table("analysis_session")
        .select("*")
        .eq("user_id", str(user_id))
        .eq("status", SessionStatus.ACTIVE)
        .execute()
        .data
    )
    return rows[0] if rows else None


def create_session(user_id: UUID) -> dict[str, Any]:
    """⚠️ UNIQUE (user_id) WHERE status='ACTIVE' 때문에 ACTIVE가 이미 있으면 실패한다.

    호출부에서 get_active_session으로 먼저 확인하고 409를 반환할 것.
    """
    return (
        get_client().table("analysis_session").insert({"user_id": str(user_id)}).execute().data[0]
    )


# --------------------------------------------------------------------------- #
# photo / segmentation
# --------------------------------------------------------------------------- #


def get_photo(session_id: UUID, kind: PhotoKind) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table("photo")
        .select("*")
        .eq("session_id", str(session_id))
        .eq("kind", kind)
        .execute()
        .data
    )
    return rows[0] if rows else None


def get_photo_by_id(photo_id: UUID) -> dict[str, Any] | None:
    rows = get_client().table("photo").select("*").eq("photo_id", str(photo_id)).execute().data
    return rows[0] if rows else None


def get_segmentation(photo_id: UUID) -> dict[str, Any] | None:
    """행의 존재 = 세그멘테이션 완료. 진행/실패 상태는 job이 소스다."""
    rows = (
        get_client().table("segmentation").select("*").eq("photo_id", str(photo_id)).execute().data
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# body_part 마스터
# --------------------------------------------------------------------------- #


def list_body_parts() -> list[dict[str, Any]]:
    """⚠️ 워커의 SKIN_CLASSES 상수 대신 이걸 쓴다. 코드와 DB가 어긋나지 않게."""
    return get_client().table("body_part").select("*").order("display_order").execute().data


def master_class_names() -> set[str]:
    """라벨 맵 검증용 — body_part 전체 class_name."""
    rows = get_client().table("body_part").select("class_name").execute().data
    return {r["class_name"] for r in rows}


def replace_segmentation(
    photo_id: UUID, segmentation: dict[str, Any], parts: list[dict[str, Any]]
) -> dict[str, Any]:
    """세그멘테이션 결과를 통째로 교체한다.

    ⚠️ UNIQUE(photo_id) 때문에 재추론 시 기존 행이 있으면 INSERT가 실패한다.
       Storage 파일 삭제는 호출부(핸들러) 책임 — 여기서는 DB만 다룬다.
    """
    client = get_client()
    client.table("segmentation").delete().eq("photo_id", str(photo_id)).execute()

    row = client.table("segmentation").insert({**segmentation, "photo_id": str(photo_id)})
    created = row.execute().data[0]

    if parts:
        client.table("body_part_segment").insert(
            [{**p, "segmentation_id": created["segmentation_id"]} for p in parts]
        ).execute()

    return created


def comparable_class_names() -> list[str]:
    rows = (
        get_client()
        .table("body_part")
        .select("class_name")
        .eq("is_comparable", True)
        .order("display_order")
        .execute()
        .data
    )
    return [r["class_name"] for r in rows]
