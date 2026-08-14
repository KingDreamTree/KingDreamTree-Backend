"""Supabase Storage 래퍼.

설계 규약
  * 버킷은 전부 private. 조회는 signed URL로만.
  * ⚠️ **전체 URL을 반환하지 않는다.** DB에는 bucket + path만 저장하고
    URL은 조회 시점에 조립한다. 버킷을 옮겨도 기존 행이 안 깨지게 하기 위함.
  * ⚠️ FK CASCADE는 Storage 파일을 지우지 않는다. 삭제는 여기서 명시적으로.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.config import settings
from app.services.db import get_client

#: 유저 데이터가 들어가는 버킷 전부 — 유저 삭제 시 이 목록을 훑는다.
USER_BUCKETS: tuple[str, ...] = (
    settings.bucket_photos,
    settings.bucket_segmentations,
    settings.bucket_body_parts,
    settings.bucket_inbody_temp,
)


# --------------------------------------------------------------------------- #
# 업로드 / 삭제
# --------------------------------------------------------------------------- #


def upload(
    bucket: str,
    path: str,
    data: bytes,
    content_type: str = "image/jpeg",
    upsert: bool = True,
) -> str:
    """파일을 올리고 **path를 반환한다** (URL 아님).

    ⚠️ segmentations / body-parts 버킷은 image/png 만 허용하도록 버킷 설정이
       걸려 있다. 라벨 맵을 JPEG로 올리면 여기서 거부된다. 의도된 방어선이다.
    """
    get_client().storage.from_(bucket).upload(
        path,
        data,
        {"content-type": content_type, "upsert": "true" if upsert else "false"},
    )
    return path


def download(bucket: str, path: str) -> bytes:
    """파일 내려받기. 워커가 원본 사진을 가져올 때 쓴다.

    signed URL을 만들지 않고 service_role 키로 직접 받는다 — 워커는 서버 사이드다.
    """
    return get_client().storage.from_(bucket).download(path)


#: 한 번에 조회·삭제할 개수.
#  ⚠️ Supabase 의 list 기본값은 100 이고 **넘으면 조용히 잘린다** (에러가 아니다).
#     그대로 두면 유저 삭제에서 101번째 파일부터 남는다 — 사람 몸 사진이 남는다.
_PAGE = 100


def remove(bucket: str, paths: list[str]) -> None:
    """파일 삭제. 없는 경로가 섞여 있어도 에러로 보지 않는다.

    ⚠️ 한 번에 다 보내지 않고 나눠 보낸다. 경로 수백 개를 한 요청에 실으면
       본문 크기 제한에 걸릴 수 있고, 그때 **아무것도 안 지워진다.**
    """
    if not paths:
        return
    bucket_api = get_client().storage.from_(bucket)
    for i in range(0, len(paths), _PAGE):
        bucket_api.remove(paths[i : i + _PAGE])


def list_prefix(bucket: str, prefix: str) -> list[str]:
    """prefix 아래 모든 파일 경로를 재귀적으로 모은다."""
    client = get_client()
    found: list[str] = []
    stack = [prefix.rstrip("/")]

    while stack:
        current = stack.pop()
        offset = 0
        while True:
            try:
                entries: list[dict[str, Any]] = client.storage.from_(bucket).list(
                    current, {"limit": _PAGE, "offset": offset}
                )
            except Exception:  # noqa: BLE001 — 없는 폴더는 조용히 건너뛴다
                break
            if not entries:
                break

            for e in entries:
                name = e.get("name")
                if not name:
                    continue
                child = f"{current}/{name}" if current else name
                # ⚠️ 폴더와 파일을 id 로 가른다. 실제 응답을 찍어 확인한 것이다:
                #     {"name": "sub",     "id": null}                    ← 폴더
                #     {"name": "top.png", "id": "fa2baf0d-6a17-..."}     ← 파일
                #    여기가 틀리면 유저 삭제에서 하위 폴더를 안 훑어 사람 사진이 남는다.
                if e.get("id") is None:
                    stack.append(child)
                else:
                    found.append(child)

            # ⚠️ 한 페이지가 가득 찼으면 뒤가 더 있을 수 있다. 여기서 멈추면
            #    **에러 없이** 파일이 남는다.
            if len(entries) < _PAGE:
                break
            offset += _PAGE

    return found


def delete_prefix(bucket: str, prefix: str) -> int:
    """prefix 아래를 통째로 지우고 삭제한 개수를 반환한다."""
    paths = list_prefix(bucket, prefix)
    if paths:
        remove(bucket, paths)
    return len(paths)


def delete_user_files(user_id: UUID) -> dict[str, int]:
    """⚠️ 유저 삭제 시 **DB보다 먼저** 호출할 것.

    DB를 먼저 지우면 어느 경로를 지워야 하는지 알 수 없게 된다.
    경로 최상위를 {user_id}/ 로 나눈 이유가 이것이다.
    """
    return {bucket: delete_prefix(bucket, str(user_id)) for bucket in USER_BUCKETS}


# --------------------------------------------------------------------------- #
# signed URL
# --------------------------------------------------------------------------- #


def _expires(expires_in: int | None) -> int:
    limit = settings.signed_url_expires_sec
    if expires_in is None:
        return limit
    return max(1, min(expires_in, limit))


def expires_at(expires_in: int | None = None) -> datetime:
    """발급했을 때의 만료 시각. 배치 발급에서 URL마다 다시 계산하지 않기 위해 분리."""
    return datetime.now(timezone.utc) + timedelta(seconds=_expires(expires_in))


def signed_url(bucket: str, path: str, expires_in: int | None = None) -> tuple[str, datetime]:
    """단건 발급. (url, 만료시각)을 반환한다."""
    ttl = _expires(expires_in)
    res = get_client().storage.from_(bucket).create_signed_url(path, ttl)
    url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
    if not url:
        raise RuntimeError(f"signed URL 발급 실패: {bucket}/{path}")
    return url, datetime.now(timezone.utc) + timedelta(seconds=ttl)


def signed_urls(bucket: str, paths: list[str], expires_in: int | None = None) -> dict[str, str]:
    """같은 버킷의 여러 경로를 한 번에. {path: url} 반환."""
    if not paths:
        return {}
    ttl = _expires(expires_in)
    res = get_client().storage.from_(bucket).create_signed_urls(paths, ttl)

    out: dict[str, str] = {}
    for item, requested in zip(res or [], paths):
        url = item.get("signedURL") or item.get("signedUrl") or item.get("signed_url")
        # 응답의 path는 앞에 버킷명이 붙어 오는 경우가 있어 요청 순서로 매칭한다
        if url:
            out[requested] = url
    return out


# --------------------------------------------------------------------------- #
# 경로 규칙 — docs/db-design-v4.md §19
# --------------------------------------------------------------------------- #


def photo_path(user_id: UUID, session_id: UUID, kind: str) -> str:
    """photos/{user_id}/{session_id}/reference.jpg"""
    return f"{user_id}/{session_id}/{kind.lower()}.jpg"


def map_path(user_id: UUID, session_id: UUID, kind: str) -> str:
    """segmentations/{user_id}/{session_id}/reference/map.png"""
    return f"{user_id}/{session_id}/{kind.lower()}/map.png"


def crop_path(user_id: UUID, session_id: UUID, kind: str, class_name: str) -> str:
    """body-parts/{user_id}/{session_id}/reference/{class_name}.png

    ⚠️ class_id(정수)는 경로에 넣지 않는다. 모델 버전에 따라 재배열된다.
    """
    return f"{user_id}/{session_id}/{kind.lower()}/{class_name}.png"


def inbody_temp_path(user_id: UUID, inbody_id: UUID, n: int) -> str:
    """inbody-temp/{user_id}/{inbody_id}_{n}.jpg"""
    return f"{user_id}/{inbody_id}_{n}.jpg"


def owns_path(user_id: UUID, path: str) -> bool:
    """⚠️ 이것만으로는 부족하다.

    prefix가 맞아도 DB에 실제로 존재하는 경로인지 별도 확인이 필요하다.
    prefix 검증만 하면 임의 경로 탐색이 가능하다.
    """
    return path.startswith(f"{user_id}/")
