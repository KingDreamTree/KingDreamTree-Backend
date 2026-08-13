"""F04 / F05 — 레퍼런스·사용자 사진 등록.

처리 분할이 이 파일의 핵심이다.

    포즈 판정   동기   — 촬영 화면이 기준값을 바로 받아야 다음 단계로 넘어간다
    세그멘테이션 비동기 — GPU에서 수백 ms~수십 초. 촬영 중에 백그라운드로 돈다

둘을 한 잡에 묶으면 사용자가 세그가 끝날 때까지 촬영 화면에 못 들어간다.

⚠️ 랜드마크 추출과 P/F 점수 계산은 **프론트**가 한다. 서버는 형식 검사와
   임계값 판정만 한다. 이유는 app/services/pose.py 모듈 주석 참조.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status

from app.config import settings
from app.deps import OwnedSession, UserId
from app.errors import (
    file_too_large,
    not_found,
    precondition_not_met,
    unsupported_media_type,
)
from app.schemas.enums import CaptureSource, JobKind, PhotoKind, PoseScaleBasis
from app.schemas.photo import ReferencePhotoResponse, UserPhotoResponse
from app.services import db, images, pose, storage
from app.worker import queue

router = APIRouter(tags=["photos"])


# --------------------------------------------------------------------------- #
# 공통
# --------------------------------------------------------------------------- #


async def _read_upload(file: UploadFile) -> bytes:
    """업로드 파일을 검사하고 바이트로 읽는다."""
    if file.content_type not in images.ALLOWED_CONTENT_TYPES:
        raise unsupported_media_type(file.content_type, list(images.ALLOWED_CONTENT_TYPES))

    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise file_too_large(settings.max_upload_bytes)
    if not raw:
        raise unsupported_media_type(file.content_type, list(images.ALLOWED_CONTENT_TYPES))
    return raw


def _discard_existing(user_id: UUID, session_id: UUID, kind: PhotoKind) -> None:
    """같은 종류의 사진이 이미 있으면 파생물까지 통째로 지운다.

    ⚠️ **삭제 순서가 중요하다.** 크롭 → 맵 → 원본 → 행.
       행을 먼저 지우면 어느 Storage 파일을 지워야 하는지 알 수 없게 된다
       (FK CASCADE는 Storage를 건드리지 않는다).
    """
    existing = db.get_photo(session_id, kind)
    if existing is None:
        return

    photo_id = UUID(str(existing["photo_id"]))

    storage.delete_prefix(settings.bucket_body_parts, f"{user_id}/{session_id}/{str(kind).lower()}")

    segmentation = db.get_segmentation(photo_id)
    if segmentation is not None:
        storage.remove(segmentation["storage_bucket"], [segmentation["map_path"]])

    storage.remove(existing["storage_bucket"], [existing["storage_path"]])
    db.delete_photo(photo_id)


def _store(
    user_id: UUID,
    session_id: UUID,
    kind: PhotoKind,
    raw: bytes,
    is_mirrored: bool,
    extra: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """사진을 저장하고 세그 잡을 건다. (photo 행, job 행) 반환."""
    jpeg, width, height = images.prepare_photo(raw, mirrored=is_mirrored)

    path = storage.photo_path(user_id, session_id, str(kind))
    storage.upload(settings.bucket_photos, path, jpeg, "image/jpeg")

    photo = db.create_photo(
        {
            "session_id": str(session_id),
            "kind": str(kind),
            "storage_bucket": settings.bucket_photos,
            "storage_path": path,
            "width": width,
            "height": height,
            "was_mirrored": is_mirrored,
            **extra,
        }
    )

    job_kind = JobKind.SEG_REFERENCE if kind == PhotoKind.REFERENCE else JobKind.SEG_USER
    job = queue.enqueue(session_id, job_kind, {"photo_id": photo["photo_id"]})
    return photo, job


def _landmarks_for_storage(raw_json: str | None, is_mirrored: bool) -> list[dict[str, Any]]:
    """랜드마크를 파싱하고, 거울 사진이면 비반전 기준으로 되돌린다.

    ⚠️ 이미지를 뒤집으면 랜드마크도 같이 뒤집어야 한다. 한쪽만 하면 사진과
       좌표가 어긋난 채 **에러 없이** 저장된다.
    """
    landmarks = pose.parse_landmarks(raw_json)
    return pose.unmirror_landmarks(landmarks) if is_mirrored else landmarks


# --------------------------------------------------------------------------- #
# F04 — 레퍼런스
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/photos/reference",
    response_model=ReferencePhotoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="레퍼런스 사진 등록 (재업로드는 교체)",
)
async def upload_reference(
    user_id: UserId,
    session: OwnedSession,
    file: Annotated[UploadFile, File(description="jpeg/png, 10MB 이하")],
    pose_landmarks: Annotated[str, Form(description="MediaPipe 33개 랜드마크 JSON 배열")],
    pose_scale_basis: Annotated[PoseScaleBasis, Form()],
    pose_person_area_ratio: Annotated[float | None, Form()] = None,
    multi_person: Annotated[bool, Form()] = False,
    is_mirrored: Annotated[
        bool, Form(description="거울로 촬영했는지. true면 서버가 좌우를 되돌려 저장한다")
    ] = False,
) -> ReferencePhotoResponse:
    session_id = UUID(str(session["session_id"]))

    raw = await _read_upload(file)
    pose.ensure_single_person(multi_person)
    landmarks = _landmarks_for_storage(pose_landmarks, is_mirrored)

    _discard_existing(user_id, session_id, PhotoKind.REFERENCE)
    photo, job = _store(
        user_id,
        session_id,
        PhotoKind.REFERENCE,
        raw,
        is_mirrored,
        {
            "pose_landmarks": landmarks,
            "pose_scale_basis": str(pose_scale_basis),
            "pose_person_area_ratio": pose_person_area_ratio,
            "multi_person": multi_person,
        },
    )

    url, expires_at = storage.signed_url(photo["storage_bucket"], photo["storage_path"])
    return ReferencePhotoResponse(
        photo_id=str(photo["photo_id"]),
        kind=PhotoKind.REFERENCE,
        width=photo["width"],
        height=photo["height"],
        pose_scale_basis=pose_scale_basis,
        was_mirrored=is_mirrored,
        created_at=str(photo["created_at"]),
        job_id=str(job["job_id"]),
        pose_landmarks=landmarks,
        signed_url=url,
        signed_url_expires_at=expires_at.isoformat(),
        segmented=False,
    )


@router.get(
    "/sessions/{session_id}/photos/reference",
    response_model=ReferencePhotoResponse,
    summary="촬영 화면용 기준값 조회",
)
async def get_reference(session: OwnedSession) -> ReferencePhotoResponse:
    session_id = UUID(str(session["session_id"]))
    photo = db.get_photo(session_id, PhotoKind.REFERENCE)
    if photo is None:
        raise not_found("레퍼런스 사진")

    photo_id = UUID(str(photo["photo_id"]))
    jobs = queue.list_jobs(session_id, kind=JobKind.SEG_REFERENCE)
    url, expires_at = storage.signed_url(photo["storage_bucket"], photo["storage_path"])

    return ReferencePhotoResponse(
        photo_id=str(photo_id),
        kind=PhotoKind.REFERENCE,
        width=photo["width"],
        height=photo["height"],
        pose_scale_basis=photo["pose_scale_basis"],
        was_mirrored=bool(photo.get("was_mirrored")),
        created_at=str(photo["created_at"]),
        job_id=str(jobs[-1]["job_id"]) if jobs else "",
        pose_landmarks=photo["pose_landmarks"] or [],
        signed_url=url,
        signed_url_expires_at=expires_at.isoformat(),
        segmented=db.get_segmentation(photo_id) is not None,
    )


# --------------------------------------------------------------------------- #
# F05 — 사용자 사진
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/photos/user",
    response_model=UserPhotoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="촬영본/업로드본 저장 (임계값 미달이면 저장하지 않고 422)",
)
async def upload_user_photo(
    user_id: UserId,
    session: OwnedSession,
    file: Annotated[UploadFile, File()],
    capture_source: Annotated[CaptureSource, Form()],
    pose_landmarks: Annotated[str, Form()],
    pose_similarity: Annotated[float, Form(description="0~100. 프론트 계산값")],
    framing_score: Annotated[float, Form(description="0~1. 프론트 계산값")],
    pose_scale_basis: Annotated[PoseScaleBasis, Form()],
    pose_person_area_ratio: Annotated[float | None, Form()] = None,
    multi_person: Annotated[bool, Form()] = False,
    is_mirrored: Annotated[bool, Form()] = False,
) -> UserPhotoResponse:
    session_id = UUID(str(session["session_id"]))

    reference = db.get_photo(session_id, PhotoKind.REFERENCE)
    if reference is None:
        raise precondition_not_met("레퍼런스 사진을 먼저 등록해주세요.")

    raw = await _read_upload(file)
    landmarks = _landmarks_for_storage(pose_landmarks, is_mirrored)

    # ⚠️ 판정을 통과하지 못하면 **저장하지 않는다.** 실패한 사진이 Storage에 쌓이면
    #    유저 삭제 시 고아 파일이 되고, 무료 티어 용량도 먹는다.
    pose.judge_user_photo(
        pose_similarity=pose_similarity,
        framing_score=framing_score,
        scale_basis=pose_scale_basis,
        reference_scale_basis=reference.get("pose_scale_basis"),
        multi_person=multi_person,
    )

    _discard_existing(user_id, session_id, PhotoKind.USER)
    photo, job = _store(
        user_id,
        session_id,
        PhotoKind.USER,
        raw,
        is_mirrored,
        {
            "capture_source": str(capture_source),
            "pose_landmarks": landmarks,
            "pose_scale_basis": str(pose_scale_basis),
            "pose_similarity": pose_similarity,
            "framing_score": framing_score,
            "pose_person_area_ratio": pose_person_area_ratio,
            "multi_person": multi_person,
        },
    )

    return UserPhotoResponse(
        photo_id=str(photo["photo_id"]),
        kind=PhotoKind.USER,
        width=photo["width"],
        height=photo["height"],
        pose_scale_basis=pose_scale_basis,
        was_mirrored=is_mirrored,
        created_at=str(photo["created_at"]),
        job_id=str(job["job_id"]),
        capture_source=capture_source,
        pose_similarity=pose_similarity,
        framing_score=framing_score,
        multi_person=multi_person,
    )
