"""F14 — signed URL 배치 발급.

⚠️ 버킷은 전부 private 이고 DB에는 bucket + path 만 저장한다.
   전체 URL을 저장하지 않는 이유는 버킷을 옮기거나 토큰 정책이 바뀌어도
   기존 행이 안 깨지게 하기 위해서다. URL은 조회 시점에 조립한다.
"""

from collections import defaultdict

from fastapi import APIRouter

from app.config import settings
from app.deps import UserId
from app.errors import invalid_request
from app.schemas.common import SignedUrlRequest, SignedUrlResponse, SignedUrlResult
from app.services import db, storage

router = APIRouter(tags=["storage"])

#: 발급을 허용하는 버킷.
#  ⚠️ inbody-temp 는 제외한다. 서버가 OCR 처리 중에만 쓰는 임시 파일이고,
#     경로가 job.payload 안에만 있어 DB로 실재 여부를 확인할 수 없다.
ALLOWED_BUCKETS: frozenset[str] = frozenset(
    {
        settings.bucket_photos,
        settings.bucket_segmentations,
        settings.bucket_body_parts,
    }
)


@router.post(
    "/storage/signed-urls",
    response_model=SignedUrlResponse,
    summary=f"signed URL 배치 발급 (최대 {settings.signed_url_max_batch}개)",
)
async def create_signed_urls(user_id: UserId, body: SignedUrlRequest) -> SignedUrlResponse:
    """⚠️ 검증이 두 단계다.

    1. 경로가 `{user_id}/` 로 시작하는가
    2. 그 경로가 **DB에 실제로 등록된 파일인가**

    1번만 하면 임의 경로를 넣어 탐색할 수 있다. 예를 들어 자기 user_id 뒤에
    아무 문자열이나 붙여 존재 여부를 떠볼 수 있으므로 2번이 필요하다.
    """
    if not body.items:
        raise invalid_request("발급할 대상이 없습니다.")
    if len(body.items) > settings.signed_url_max_batch:
        raise invalid_request(
            f"한 번에 최대 {settings.signed_url_max_batch}개까지 발급할 수 있습니다.",
            {"got": len(body.items), "max": settings.signed_url_max_batch},
        )

    for item in body.items:
        if item.bucket not in ALLOWED_BUCKETS:
            raise invalid_request(
                "발급할 수 없는 버킷입니다.",
                {"bucket": item.bucket, "allowed": sorted(ALLOWED_BUCKETS)},
            )
        if not storage.owns_path(user_id, item.path):
            raise invalid_request("본인 경로가 아닙니다.", {"path": item.path})
        if not db.storage_path_exists(item.bucket, item.path):
            raise invalid_request("등록되지 않은 경로입니다.", {"path": item.path})

    # 같은 버킷끼리 묶어 한 번에 발급한다 — 30개면 왕복이 30번이 될 수 있다.
    by_bucket: dict[str, list[str]] = defaultdict(list)
    for item in body.items:
        by_bucket[item.bucket].append(item.path)

    urls: dict[tuple[str, str], str] = {}
    for bucket, paths in by_bucket.items():
        issued = storage.signed_urls(bucket, paths, body.expires_in)
        for path, url in issued.items():
            urls[(bucket, path)] = url

    # 만료 시각은 전부 같다. URL마다 다시 발급받아 확인하지 않는다.
    expires_at = storage.expires_at(body.expires_in).isoformat()

    results: list[SignedUrlResult] = []
    for item in body.items:
        url = urls.get((item.bucket, item.path))
        if url is None:
            raise invalid_request("signed URL 발급에 실패했습니다.", {"path": item.path})
        results.append(
            SignedUrlResult(bucket=item.bucket, path=item.path, url=url, expires_at=expires_at)
        )

    return SignedUrlResponse(items=results)
