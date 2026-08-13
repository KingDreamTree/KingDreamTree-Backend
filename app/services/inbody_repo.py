"""inbody / inbody_segment 쿼리 — 담당 B 소유.

app/services/db.py 는 공유 파일이라 B 전용 쿼리를 여기로 분리한다
(db.rows_for_session 주석 참고 — "테이블마다 전용 함수를 만들면 B 영역까지 비대해진다").
공용 클라이언트만 db.get_client()로 빌려 쓴다.
"""

from typing import Any
from uuid import UUID

from app.services.db import get_client, get_session


def create_inbody(session_id: UUID, device_type: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"session_id": str(session_id)}
    if device_type:
        row["device_type"] = device_type
    return get_client().table("inbody").insert(row).execute().data[0]


def get_inbody(inbody_id: UUID) -> dict[str, Any] | None:
    rows = get_client().table("inbody").select("*").eq("inbody_id", str(inbody_id)).execute().data
    return rows[0] if rows else None


def get_owned_inbody(inbody_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    """inbody → analysis_session → user_id 로 소유권을 확인한다.

    ⚠️ 불일치도 None(→404). 403을 주면 id 존재가 새어나간다 (deps.py 규약).
    """
    row = get_inbody(inbody_id)
    if row is None:
        return None
    session = get_session(UUID(str(row["session_id"])))
    if session is None or str(session["user_id"]) != str(user_id):
        return None
    return row


def list_for_session(session_id: UUID) -> list[dict[str, Any]]:
    return (
        get_client()
        .table("inbody")
        .select("inbody_id,status,device_type,measured_at,created_at")
        .eq("session_id", str(session_id))
        .order("created_at", desc=True)
        .execute()
        .data
    )


def update_inbody(inbody_id: UUID, patch: dict[str, Any]) -> dict[str, Any]:
    """⚠️ raw_ocr 을 patch에 넣는 곳은 OCR 핸들러 한 곳뿐이어야 한다.

    사용자 수정(PATCH 라우트)은 절대 raw_ocr 을 덮지 않는다 — 원본과 수정본을
    구분해야 OCR 정확도를 평가할 수 있다 (api-spec F07).
    """
    return (
        get_client()
        .table("inbody")
        .update(patch)
        .eq("inbody_id", str(inbody_id))
        .execute()
        .data[0]
    )


def delete_inbody(inbody_id: UUID) -> None:
    """행 삭제. inbody_segment 는 CASCADE. ⚠️ 임시 이미지는 호출부가 지운다."""
    get_client().table("inbody").delete().eq("inbody_id", str(inbody_id)).execute()


# --------------------------------------------------------------------------- #
# inbody_segment
# --------------------------------------------------------------------------- #


def list_segments(inbody_id: UUID) -> list[dict[str, Any]]:
    return (
        get_client()
        .table("inbody_segment")
        .select("segment,lean_mass,fat_mass")
        .eq("inbody_id", str(inbody_id))
        .execute()
        .data
    )


def replace_segments(inbody_id: UUID, rows: list[dict[str, Any]]) -> None:
    """부위별 행을 통째로 교체한다.

    UNIQUE (inbody_id, segment) 라 재처리 시 기존 행이 있으면 INSERT가 터진다.
    잡 재시도(attempts>1)에서도 같은 경로를 타므로 delete → insert 로 통일.
    """
    client = get_client()
    client.table("inbody_segment").delete().eq("inbody_id", str(inbody_id)).execute()
    if rows:
        client.table("inbody_segment").insert(
            [{**r, "inbody_id": str(inbody_id)} for r in rows]
        ).execute()


def upsert_segments(inbody_id: UUID, rows: list[dict[str, Any]]) -> None:
    """사용자 수정용 — 보낸 부위만 갱신하고 나머지는 유지한다."""
    if not rows:
        return
    get_client().table("inbody_segment").upsert(
        [{**r, "inbody_id": str(inbody_id)} for r in rows],
        on_conflict="inbody_id,segment",
    ).execute()
