"""F07 — 인바디 결과지 인식.

처리 분할 (photos.py 와 같은 이유)
    업로드·검증   동기   — 파일이 열리는지, 장수 제한은 바로 답해야 한다
    OCR 추출      비동기 — GPT-4o Vision 호출 수 초. 202 + job_id 폴링

⚠️ 인바디는 **선택 입력**이다. 이 플로우 전체가 실패해도 분석(F08)은
   인바디 없이 진행된다 — 여기서 사용자를 막지 말 것.

⚠️ 결과지 이미지는 영구 저장하지 않는다 (db-design-v4 §7).
   inbody-temp 에 올렸다가 OCR DONE 직후 지운다. FAILED 면 재처리를 위해 남긴다.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile, status

from app.config import settings
from app.deps import OwnedSession, UserId
from app.errors import file_too_large, invalid_request, not_found, unsupported_media_type
from app.schemas.enums import DomainStatus, JobKind
from app.schemas.inbody import (
    InbodyCreateResponse,
    InbodyDetailResponse,
    InbodyListItem,
    InbodyPatchRequest,
    InbodySegmentDto,
)
from app.services import images, inbody_repo, ocr, storage
from app.worker import queue

log = logging.getLogger("routes.inbody")

router = APIRouter(tags=["inbody"])

#: PATCH 로 고칠 수 있는 필드. raw_ocr·status·validation 은 사용자가 못 건드린다.
_PATCHABLE_FIELDS = frozenset(
    {
        "device_type",
        "measured_at",
        "age",
        "gender",
        "height",
        "weight",
        "bmi",
        "body_fat_mass",
        "body_fat_percentage",
        "skeletal_muscle_mass",
        "fat_free_mass",
        "bmr_kcal",
    }
)


# --------------------------------------------------------------------------- #
# 소유권 의존성 — deps.py 는 공유 파일이라 여기 둔다
# --------------------------------------------------------------------------- #


async def get_owned_inbody(
    user_id: UserId,
    inbody_id: Annotated[UUID, Path()],
) -> dict[str, Any]:
    row = inbody_repo.get_owned_inbody(inbody_id, user_id)
    if row is None:
        raise not_found("인바디")
    return row


OwnedInbody = Annotated[dict[str, Any], Depends(get_owned_inbody)]


# --------------------------------------------------------------------------- #
# 업로드 → OCR 큐잉
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/inbody",
    response_model=InbodyCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="결과지 업로드 → OCR 큐잉 (요청 1건 = 결과지 1건)",
)
async def upload_inbody(
    user_id: UserId,
    session: OwnedSession,
    files: Annotated[list[UploadFile], File(description="결과지 이미지 1~5장 (한 건의 여러 페이지)")],
    device_type: Annotated[str | None, Form()] = None,
) -> InbodyCreateResponse:
    if not 1 <= len(files) <= settings.inbody_max_files:
        raise invalid_request(
            f"결과지 이미지는 1~{settings.inbody_max_files}장이어야 합니다.",
            {"got": len(files)},
        )

    # 전부 열리는지 먼저 확인한다 — 3장 중 2장만 올라간 상태를 만들지 않기 위해.
    # ⚠️ encode_photo 재인코딩으로 EXIF(위치정보)를 벗긴다. 임시라도 원본을 올리지 않는다.
    pages: list[bytes] = []
    for f in files:
        raw = await f.read()
        if len(raw) > settings.max_upload_bytes:
            raise file_too_large(settings.max_upload_bytes)
        try:
            img = images.load_rgb(raw)
        except images.UnsupportedImageError as e:
            raise unsupported_media_type(f.content_type, ["jpeg", "png", "heic", "webp"]) from e
        jpeg, _, _ = images.encode_photo(img)
        pages.append(jpeg)

    session_id = UUID(str(session["session_id"]))
    row = inbody_repo.create_inbody(session_id, device_type)
    inbody_id = UUID(str(row["inbody_id"]))

    paths = []
    for n, jpeg in enumerate(pages, start=1):
        path = storage.inbody_temp_path(user_id, inbody_id, n)
        storage.upload(settings.bucket_inbody_temp, path, jpeg, "image/jpeg")
        paths.append(path)

    # 임시 경로는 DB 컬럼이 아니라 job.payload 에만 둔다 (db-design-v4 §7).
    job = queue.enqueue(
        session_id,
        JobKind.OCR_INBODY,
        {"inbody_id": str(inbody_id), "bucket": settings.bucket_inbody_temp, "paths": paths},
    )

    return InbodyCreateResponse(
        inbody_id=inbody_id, job_id=job["job_id"], status=DomainStatus.PENDING
    )


# --------------------------------------------------------------------------- #
# 조회
# --------------------------------------------------------------------------- #


@router.get(
    "/sessions/{session_id}/inbody",
    response_model=list[InbodyListItem],
    summary="이 세션에 업로드한 결과지 목록",
)
async def list_inbody(session: OwnedSession) -> list[InbodyListItem]:
    rows = inbody_repo.list_for_session(UUID(str(session["session_id"])))
    return [InbodyListItem(**r) for r in rows]


@router.get(
    "/inbody/{inbody_id}",
    response_model=InbodyDetailResponse,
    summary="추출값 + 검증 등급 (확인 화면)",
)
async def get_inbody(row: OwnedInbody) -> InbodyDetailResponse:
    return _detail(row)


def _detail(row: dict[str, Any]) -> InbodyDetailResponse:
    segments = inbody_repo.list_segments(UUID(str(row["inbody_id"])))
    return InbodyDetailResponse(
        inbody_id=row["inbody_id"],
        status=row["status"],
        device_type=row.get("device_type"),
        measured_at=row.get("measured_at"),
        fields=ocr.to_columns(row),
        # ⚠️ SMI는 VLM이 계산하지 않는다 — 백엔드가 컬럼값으로 직접 계산.
        #    row 기준이라 사용자가 PATCH로 고친 값이 즉시 반영된다.
        smi=ocr.calc_smi(row),
        segments=[InbodySegmentDto(**s) for s in segments],
        validation=ocr.to_field_validation(row.get("validation")),
        raw_ocr=row.get("raw_ocr"),
        verified_at=row.get("verified_at"),
    )


# --------------------------------------------------------------------------- #
# 사용자 확인·수정
# --------------------------------------------------------------------------- #


@router.patch(
    "/inbody/{inbody_id}",
    response_model=InbodyDetailResponse,
    summary="추출값 확인·수정 (raw_ocr 은 보존)",
)
async def patch_inbody(row: OwnedInbody, body: InbodyPatchRequest) -> InbodyDetailResponse:
    inbody_id = UUID(str(row["inbody_id"]))
    fields = body.fields or {}

    unknown = set(fields) - _PATCHABLE_FIELDS
    if unknown:
        raise invalid_request("수정할 수 없는 필드입니다.", {"fields": sorted(unknown)})

    # 사용자 입력은 OCR과 달리 조용히 버리지 않는다 — 범위 밖이면 400으로 돌려준다.
    _, warns = ocr.sanitize_columns(fields)
    if warns:
        raise invalid_request(
            "저장 가능 범위를 벗어난 값이 있습니다.",
            {"violations": [{"field": w["rule"], "message": w["message"]} for w in warns]},
        )

    if body.segments:
        inbody_repo.upsert_segments(
            inbody_id,
            [s.model_dump(exclude_none=True) for s in body.segments],
        )

    patch: dict[str, Any] = dict(fields)

    # 수정 후 재검증 — 고친 값이 또 항등식을 깨면 다시 warn 이 떠야 한다.
    # raw_ocr 의 항등식 전용 항목(체수분·단백질 등)은 유지하고 그 위에
    # 현재 컬럼값 + 수정값 + 최신 segments 를 덮어 재구성한다.
    merged: dict[str, Any] = {**(row.get("raw_ocr") or {}), **ocr.to_columns(row), **fields}
    merged["segments"] = {
        s["segment"]: {"lean_mass": s.get("lean_mass"), "fat_mass": s.get("fat_mass")}
        for s in inbody_repo.list_segments(inbody_id)
    }
    patch["validation"] = ocr.validate_inbody(merged)

    if body.verified:
        # FAILED 건도 수동 입력으로 DONE 으로 올릴 수 있다 (api-spec F07).
        patch["verified_at"] = "now()"
        patch["status"] = DomainStatus.DONE

    updated = inbody_repo.update_inbody(inbody_id, patch)
    return _detail(updated)


# --------------------------------------------------------------------------- #
# 삭제
# --------------------------------------------------------------------------- #


@router.delete(
    "/inbody/{inbody_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="결과지 삭제",
)
async def delete_inbody(user_id: UserId, row: OwnedInbody) -> None:
    """⚠️ Storage(임시 이미지)를 먼저, DB 행을 나중에 지운다 (users.py 규약).

    임시 경로는 job.payload 에만 있어 행에서 알 수 없으므로,
    inbody_temp_path 규칙(`{user_id}/{inbody_id}_{n}.jpg`)으로 prefix 를 훑는다.
    DONE 이면 핸들러가 이미 지웠으니 보통 0건이다.
    """
    inbody_id = UUID(str(row["inbody_id"]))
    marker = f"{user_id}/{inbody_id}_"
    leftovers = [
        p for p in storage.list_prefix(settings.bucket_inbody_temp, str(user_id))
        if p.startswith(marker)
    ]
    storage.remove(settings.bucket_inbody_temp, leftovers)
    inbody_repo.delete_inbody(inbody_id)
