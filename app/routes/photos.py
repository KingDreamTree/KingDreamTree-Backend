"""F04 / F05 — 레퍼런스·사용자 사진 등록.

처리 분할이 이 파일의 핵심이다.

    포즈 판정   동기   — 촬영 화면이 기준값을 바로 받아야 다음 단계로 넘어간다
    세그멘테이션 비동기 — GPU에서 수백 ms~수십 초. 촬영 중에 백그라운드로 돈다

둘을 한 잡에 묶으면 사용자가 세그가 끝날 때까지 촬영 화면에 못 들어간다.

⚠️ 랜드마크 추출과 P/F 점수 계산은 **프론트**가 한다. 서버는 형식 검사와
   임계값 판정만 한다. 이유는 app/services/pose.py 모듈 주석 참조.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status
from PIL import Image

from app.config import settings
from app.deps import OwnedSession, UserId
from app.errors import (
    file_too_large,
    not_found,
    precondition_not_met,
    screening_unavailable,
    unsuitable_photo,
    unsupported_media_type,
)
from app.schemas.enums import CaptureSource, JobKind, PhotoKind, PoseScaleBasis
from app.schemas.photo import ReferencePhotoResponse, UserPhotoResponse
from app.services import db, diagnosis_repo, images, photo_screening, pose, storage
from app.worker import queue

log = logging.getLogger("routes.photos")

router = APIRouter(tags=["photos"])


# --------------------------------------------------------------------------- #
# 공통
# --------------------------------------------------------------------------- #


async def _read_upload(file: UploadFile) -> Image.Image:
    """업로드 파일을 검사하고 디코딩한다.

    ⚠️ **Content-Type 헤더로 형식을 거르지 않는다.** 브라우저가 아이폰 HEIC에
       빈 값이나 application/octet-stream 을 붙여 보내는 경우가 있어, 헤더로
       거르면 멀쩡한 사진이 415로 막힌다. **실제로 열리는지**가 유일한 기준이다.
       저장은 어차피 JPEG로 다시 인코딩하므로 입력 형식은 남지 않는다.
    """
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise file_too_large(settings.max_upload_bytes)

    try:
        return images.load_rgb(raw)
    except images.UnsupportedImageError as e:
        raise unsupported_media_type(file.content_type, ["jpeg", "png", "heic", "webp"]) from e


def _discard_existing(user_id: UUID, session_id: UUID, kind: PhotoKind) -> None:
    """같은 종류의 사진이 이미 있으면 파생물까지 통째로 지운다.

    ⚠️ **삭제 순서가 중요하다.** 크롭 → 맵 → 원본 → 행.
       행을 먼저 지우면 어느 Storage 파일을 지워야 하는지 알 수 없게 된다
       (FK CASCADE는 Storage를 건드리지 않는다).

    ⚠️ 진단(part_diagnosis/overall_diagnosis)도 함께 지운다 (2026-08-18).
       `POST /analysis`의 가드③은 overall이 DONE이면 재실행을 건너뛴다 —
       사진을 갈아 끼워도 진단 행이 남아있으면 옛 사진 기준 결과를 계속
       돌려준다("다시 올려도 똑같이 나온다"). 사진이 바뀌면 그 사진에서
       나온 진단은 더 이상 진실이 아니므로 같이 지운다.
    """
    existing = db.get_photo(session_id, kind)
    if existing is None:
        return

    photo_id = UUID(str(existing["photo_id"]))

    # ⚠️ 잡을 먼저 내린다. 사진 행을 지운 뒤에 워커가 그 잡을 집으면
    #    "대상 사진을 찾을 수 없습니다"로 실패하고 재시도까지 돈다.
    canceled = queue.cancel_open_for_photo(session_id, photo_id)
    if canceled:
        log.info("사진 교체로 잡 %d건 취소: photo_id=%s", canceled, photo_id)

    diagnosis_repo.clear_diagnoses(session_id)

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
    img: Image.Image,
    is_mirrored: bool,
    extra: dict[str, Any],
    enqueue_seg: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """사진을 저장하고 (기본) 세그 잡을 건다. (photo 행, job 행 | None) 반환.

    enqueue_seg=False 는 **퀵 파이프라인(웹캠)** 용이다 — 그 경로는 Sapiens2 를
    쓰지 않으므로 세그 잡을 걸면 GPU 워커가 없는 배포에서 PENDING 이 쌓이고,
    stalled 힌트("아무도 안 집어감")가 멀쩡한 세션에 뜬다.
    """
    # ⚠️ 거울 되돌리기를 **여기서** 한다 (예전엔 encode_photo 안에서 했다).
    #    아래 크롭이 랜드마크와 같은 좌표계에서 일어나야 하는데, extra 의
    #    랜드마크는 이미 언미러된 상태라 이미지도 먼저 맞춰야 한다.
    if is_mirrored:
        img = images.flip_horizontal(img)

    # ⚠️ Sapiens2 입력 비율(3:4)로 맞춘다. 전처리기가 비율을 보존하지 않고
    #    강제로 늘리기 때문에, 16:9 노트북 웹캠 사진은 사람이 가로로 2.37배
    #    눌린 채 모델에 들어간다 (images.fit_to_model_aspect 주석 참고).
    #    ⚠️ 사진과 랜드마크를 **함께** 옮긴다. 하나만 바꾸면 조용히 어긋난다.
    landmarks = extra.get("pose_landmarks")
    img, cropped_landmarks = images.fit_to_model_aspect(img, landmarks)
    if cropped_landmarks is not landmarks:
        extra = {**extra, "pose_landmarks": cropped_landmarks}

    jpeg, width, height = images.encode_photo(img)

    path = storage.photo_path(user_id, session_id, str(kind))
    storage.upload(settings.bucket_photos, path, jpeg, "image/jpeg")

    # ⚠️ 업로드 뒤의 행 생성·잡 등록이 실패하면 **올린 것을 도로 지운다.**
    #    여기 오기 전에 _discard_existing 이 이전 사진을 이미 지웠으므로 실패는
    #    어차피 "사진 없음" 상태다 — 거기에 고아 파일(영원히 안 쓰일 JPEG)이나
    #    잡 없는 사진 행(화면에서 영원히 '처리 중')까지 남기면 재시도가 더 꼬인다.
    photo = None
    try:
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
        job = None
        if enqueue_seg:
            job_kind = JobKind.SEG_REFERENCE if kind == PhotoKind.REFERENCE else JobKind.SEG_USER
            job = queue.enqueue(session_id, job_kind, {"photo_id": photo["photo_id"]})
    except Exception:
        try:
            if photo is not None:
                db.delete_photo(UUID(str(photo["photo_id"])))
            storage.remove(settings.bucket_photos, [path])
        except Exception:  # noqa: BLE001 — 뒷정리 실패가 원인 예외를 가리면 안 된다
            log.exception("업로드 실패 뒷정리 실패 — 고아 파일이 남았을 수 있습니다")
        raise
    return photo, job


def _reference_bytes(reference: dict[str, Any]) -> bytes | None:
    """2차 검사에 넣을 레퍼런스 원본. 못 읽으면 None (검사는 건너뛴다).

    ⚠️ 여기서 실패해도 업로드를 막지 않는다. 레퍼런스를 못 읽는 건 사용자 잘못이
       아니고, 그것 때문에 사진을 반려하면 사용자가 할 수 있는 게 없다.
    """
    try:
        return storage.download(reference["storage_bucket"], reference["storage_path"])
    except Exception:  # noqa: BLE001
        log.exception("레퍼런스 원본을 읽지 못했습니다 — 2차 검사를 건너뜁니다")
        return None


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
    file: Annotated[UploadFile, File(description="jpeg/png/heic/webp 등, 10MB 이하")],
    pose_landmarks: Annotated[str, Form(description="MediaPipe 33개 랜드마크 JSON 배열")],
    pose_scale_basis: Annotated[PoseScaleBasis, Form()],
    pose_person_area_ratio: Annotated[float | None, Form()] = None,
    multi_person: Annotated[bool, Form()] = False,
    is_mirrored: Annotated[
        bool, Form(description="거울로 촬영했는지. true면 서버가 좌우를 되돌려 저장한다")
    ] = False,
    pipeline: Annotated[
        str,
        Form(
            pattern="^(full|quick)$",
            description=(
                "full(기본) = 세그멘테이션 잡 등록. "
                "quick = 웹캠 퀵 진단 경로 — Sapiens2 를 쓰지 않으므로 세그 잡을 "
                "걸지 않는다 (응답 job_id 가 null). 이후 POST /analysis?mode=quick"
            ),
        ),
    ] = "full",
) -> ReferencePhotoResponse:
    session_id = UUID(str(session["session_id"]))

    img = await _read_upload(file)
    pose.ensure_single_person(multi_person)
    pose.ensure_observation_ranges(person_area_ratio=pose_person_area_ratio)
    landmarks = _landmarks_for_storage(pose_landmarks, is_mirrored)

    _discard_existing(user_id, session_id, PhotoKind.REFERENCE)
    photo, job = _store(
        user_id,
        session_id,
        PhotoKind.REFERENCE,
        img,
        is_mirrored,
        {
            "pose_landmarks": landmarks,
            "pose_scale_basis": str(pose_scale_basis),
            "pose_person_area_ratio": pose_person_area_ratio,
            "multi_person": multi_person,
        },
        enqueue_seg=(pipeline != "quick"),
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
        job_id=str(job["job_id"]) if job else None,
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
        job_id=str(jobs[-1]["job_id"]) if jobs else None,
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
    pose_similarity: Annotated[float, Form(description="0~100. 산식은 docs/pose-scoring.md")],
    framing_score: Annotated[
        float,
        Form(
            description=(
                "0~1. 촬영 거리 일치도 = min(비율, 1/비율), "
                "비율 = 사용자 몸통길이 / 레퍼런스 몸통길이. "
                "⚠️ bbox IoU 아님 — 그건 팔다리 움직임을 프레이밍 문제로 오인한다"
            )
        ),
    ],
    pose_scale_basis: Annotated[PoseScaleBasis, Form()],
    facing_delta: Annotated[
        float,
        Form(
            description=(
                "레퍼런스와 몸 방향의 차이. |어깨폭비 차| / 레퍼런스 어깨폭비. "
                "⚠️ '정면인가'가 아니라 상대값이며, 레퍼런스가 많이 돌아가 있으면 1을 넘는다"
            )
        ),
    ] = 0.0,
    pose_oks: Annotated[
        float | None,
        Form(
            description=(
                "OKS-inspired 자세 유사도 0~1. ⚠️ **판정에 쓰지 않는다.** "
                "지금 쓰는 pose_similarity(각도 기반)와 비교하려고 같이 저장만 한다"
            )
        ),
    ] = None,
    pose_person_area_ratio: Annotated[float | None, Form()] = None,
    multi_person: Annotated[bool, Form()] = False,
    is_mirrored: Annotated[bool, Form()] = False,
    pipeline: Annotated[
        str,
        Form(
            pattern="^(full|quick)$",
            description=(
                "full(기본) = 세그멘테이션 잡 등록. "
                "quick = 웹캠 퀵 진단 경로 — Sapiens2 를 쓰지 않으므로 세그 잡을 "
                "걸지 않는다 (응답 job_id 가 null). 이후 POST /analysis?mode=quick"
            ),
        ),
    ] = "full",
) -> UserPhotoResponse:
    session_id = UUID(str(session["session_id"]))

    reference = db.get_photo(session_id, PhotoKind.REFERENCE)
    if reference is None:
        raise precondition_not_met("레퍼런스 사진을 먼저 등록해주세요.")

    img = await _read_upload(file)
    landmarks = _landmarks_for_storage(pose_landmarks, is_mirrored)

    # ⚠️ 관찰용 필드도 판정 **전에** 범위를 본다 — 통과 후 DB CHECK 500 이 나면
    #    그 시점엔 이전 사진이 이미 교체돼 있다 (pose.ensure_observation_ranges 주석).
    pose.ensure_observation_ranges(pose_oks=pose_oks, person_area_ratio=pose_person_area_ratio)

    # ⚠️ facing_delta 는 관찰용(2026-08-14 부터 판정 미사용)이라 **막지 않고 눌러 담는다.**
    #    산식 (u+r)/r 은 측면 레퍼런스(어깨폭비→0)에서 정당하게 10 을 넘을 수 있는데,
    #    그때 범위 검사 400 을 주면 판정에도 안 쓰는 지표가 업로드를 막는 모순이 된다.
    #    단위 착오까지 같이 눌리는 건 감수하고 로그로만 남긴다 — 어차피 관문이 아니다.
    if facing_delta > 10.0:
        log.warning("facing_delta %.2f > 10 — 10.0 으로 눌러 저장합니다", facing_delta)
        facing_delta = 10.0

    # ── 1차 관문: 자세 ────────────────────────────────────────────────────
    # ⚠️ 판정을 통과하지 못하면 **저장하지 않는다.** 실패한 사진이 Storage에 쌓이면
    #    유저 삭제 시 고아 파일이 되고, 무료 티어 용량도 먹는다.
    pose.judge_user_photo(
        pose_similarity=pose_similarity,
        framing_score=framing_score,
        scale_basis=pose_scale_basis,
        reference_scale_basis=reference.get("pose_scale_basis"),
        multi_person=multi_person,
        facing_delta=facing_delta,
    )

    # ── 2차 관문: 이 둘로 비교 진단이 성립하는가 ──────────────────────────
    # ⚠️ **레퍼런스와 함께** 판단한다. 사용자 사진만 보면 "괜찮은 사진"인데
    #    촬영 거리가 딴판이라 비율 비교가 무의미해지는 경우를 못 잡는다.
    # ⚠️ VLM 에 이미지를 직접(base64) 보내므로 **거부될 사진은 Storage 에 안 올라간다.**
    # ⚠️ 판정 실패·타임아웃은 503 — 저장하지 않고 "잠시 후 다시 시도" (fail-closed).
    #    반려(422)와 다르다: 사진이 아니라 검사기 문제라, 재촬영이 아니라 재시도.
    #    (photo_screening 모듈 주석 참조 — 헐렁한 옷은 사후 검증이 못 잡아서
    #    판정 없이 들여보내면 잘못된 진단이 정상처럼 나간다)
    # ⚠️ 여기서 JPEG 로 인코딩하지 않는다. 판정 쪽에서 어차피 768px 로 줄이므로
    #    원본 크기로 인코딩 → 디코딩을 한 번씩 더 하는 셈이 된다.
    #    거울 여부는 판정 기준(옷·거리·잘림) 어디에도 영향이 없어 그대로 넘긴다.
    try:
        screening = await photo_screening.screen(img, _reference_bytes(reference))
    except photo_screening.ScreeningUnavailable:
        raise screening_unavailable() from None
    if not screening.suitable:
        raise unsuitable_photo(
            screening.message,
            {"reason": screening.reason, "confidence": screening.confidence},
        )

    _discard_existing(user_id, session_id, PhotoKind.USER)
    photo, job = _store(
        user_id,
        session_id,
        PhotoKind.USER,
        img,
        is_mirrored,
        {
            "capture_source": str(capture_source),
            "pose_landmarks": landmarks,
            "pose_scale_basis": str(pose_scale_basis),
            "pose_similarity": pose_similarity,
            "framing_score": framing_score,
            # ⚠️ 판정에 쓰면서 저장은 안 하고 있었다. 그러면 "FACING 으로 반려된
            #    사람들이 실제로 몸이 돌아가 있었나"를 나중에 확인할 방법이 없다.
            #    ⚠️ 다만 **통과한 사진만 저장된다** — 반려 건은 여전히 안 남는다.
            "facing_delta": facing_delta,
            "pose_oks": pose_oks,
            "pose_person_area_ratio": pose_person_area_ratio,
            "multi_person": multi_person,
        },
        enqueue_seg=(pipeline != "quick"),
    )

    return UserPhotoResponse(
        photo_id=str(photo["photo_id"]),
        kind=PhotoKind.USER,
        width=photo["width"],
        height=photo["height"],
        pose_scale_basis=pose_scale_basis,
        was_mirrored=is_mirrored,
        created_at=str(photo["created_at"]),
        job_id=str(job["job_id"]) if job else None,
        capture_source=capture_source,
        pose_similarity=pose_similarity,
        framing_score=framing_score,
        multi_person=multi_person,
    )
