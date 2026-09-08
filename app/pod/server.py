"""팟 HTTP 서버 — POST /upload 하나와 GET /health.

    POST /upload   multipart: reference(파일) · user(파일) · pipeline(full|quick)
                   헤더:      X-Upload-Token (API 가 발급한 일회용 토큰)
                   응답:      202 {accepted, session_id, jobs{kind: job_id}, crop_box{...}}
                              422 UNSUITABLE_PHOTO   — 스크리닝 반려 (재촬영)
                              503 SCREENING_UNAVAILABLE / POD_BUSY — 잠시 후 다시
                              401 INVALID_UPLOAD_TOKEN / 413 / 429

순서가 곧 방어선이다
    1. IP 속도 제한 · Content-Length 상한 · 토큰 검증  ← **본문을 읽기 전에** (의존성)
    2. 세션·사진 행 확인 (토큰의 session 과 일치하는가)
    3. 두 장 디코딩 → 가공 (거울 되돌림·3:4 크롭·리사이즈)
    4. 사용자 사진 스크리닝 (OpenAI) — 이 응답 안에서 즉시 통과/반려
    5. 대기열 등록 → 202. 세그·진단은 뒤에서 (app/pod/pipeline.py)

⚠️ 프록시(RunPod) 연결 상한이 100초다. 이 핸들러가 기다리는 것은 스크리닝(10초
   상한)뿐이고, 나머지는 대기열에 넘기고 바로 답한다.
⚠️ 사진 바이트는 이 요청 범위와 pipeline 의 메모리 등록 외 어디에도 두지 않는다 —
   로그에 크기만 찍고, 디스크에 쓰지 않는다.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.errors import (
    file_too_large,
    invalid_upload_token,
    not_found,
    pod_busy,
    precondition_not_met,
    screening_unavailable,
    unsuitable_photo,
    unsupported_media_type,
)
from app.exception_handlers import register_exception_handlers
from app.pod import pipeline
from app.schemas.enums import PhotoKind, SessionStatus
from app.services import db, images, photo_screening, rate_limit, upload_token

log = logging.getLogger("pod.server")

app = FastAPI(
    title="KingDreamTree GPU Pod",
    description="사진을 받아 메모리에서 스크리닝·세그·진단을 처리하고 결과만 저장한다. 사진은 저장하지 않는다.",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)
register_exception_handlers(app)

#: 두 장 + 폼 오버헤드. 이보다 크면 본문을 읽지 않고 413.
_MAX_BODY = settings.max_upload_bytes * 2 + 64 * 1024


async def _gate(
    request: Request,
    x_upload_token: Annotated[str | None, Header(alias="X-Upload-Token")] = None,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
) -> dict[str, Any]:
    """본문 파싱 **전에** 도는 관문 — 속도 제한 → 크기 → 토큰.

    FastAPI 는 의존성을 먼저 풀고 그 다음 multipart 본문을 읽는다. 여기서 예외가
    나면 사진 바이트는 파싱되지 않는다.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_limit.check(
        f"pod-upload:{client_ip}",
        settings.pod_upload_rate_limit,
        settings.pod_upload_rate_window_sec,
    )
    if content_length is not None and content_length > _MAX_BODY:
        raise file_too_large(settings.max_upload_bytes)
    try:
        return upload_token.verify(x_upload_token, consume=True)
    except upload_token.InvalidUploadToken as e:
        raise invalid_upload_token(str(e)) from None


Gate = Annotated[dict[str, Any], Depends(_gate)]


async def _read(file: UploadFile, label: str) -> bytes:
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise file_too_large(settings.max_upload_bytes)
    if not raw:
        raise unsupported_media_type(file.content_type, ["jpeg", "png", "heic", "webp"])
    log.info("%s 수신 %d bytes", label, len(raw))
    return raw


@app.get("/health")
async def health() -> dict[str, Any]:
    """살아 있나 + 대기열 + 메모리 사진 수. 사진 수는 대기 상태에서 0 이어야 한다."""
    body: dict[str, Any] = {"status": "ok", "version": app.version, "pipeline": pipeline.status()}
    try:
        db.get_client().table("job").select("job_id").limit(1).execute()
        body["db"] = "ok"
    except Exception:  # noqa: BLE001
        log.exception("헬스체크 — Supabase 연결 실패")
        body["db"] = "unreachable"
        body["status"] = "degraded"
    if not body["pipeline"]["worker_alive"]:
        body["status"] = "degraded"
    return body


@app.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    gate: Gate,
    reference: Annotated[UploadFile, File(description="기준 사진 (얼굴 블러됨)")],
    user: Annotated[UploadFile, File(description="사용자 사진 (얼굴 블러됨)")],
    pipeline_mode: Annotated[str, Form(alias="pipeline", pattern="^(full|quick)$")] = "full",
) -> dict[str, Any]:
    session_id = UUID(gate["session_id"])
    session = db.get_session(session_id)
    if session is None or str(session["user_id"]) != gate["user_id"]:
        raise not_found("세션")
    if session.get("status") != SessionStatus.ACTIVE:
        raise precondition_not_met("종료된 분석 세션입니다. 새로 시작해주세요.")

    photos: dict[str, dict[str, Any]] = {}
    for kind in (PhotoKind.REFERENCE, PhotoKind.USER):
        row = db.get_photo(session_id, kind)
        if row is None:
            raise precondition_not_met(
                "레퍼런스와 사용자 사진(랜드마크)을 먼저 등록해주세요.", {"missing": str(kind)}
            )
        photos[str(kind)] = row

    ref_raw = await _read(reference, "REFERENCE")
    user_raw = await _read(user, "USER")

    # 디코딩·크롭은 CPU 작업 — 이벤트 루프를 막지 않게 스레드풀로.
    try:
        prepared = {
            str(PhotoKind.REFERENCE): await run_in_threadpool(
                pipeline.prepare, photos[str(PhotoKind.REFERENCE)], ref_raw
            ),
            str(PhotoKind.USER): await run_in_threadpool(
                pipeline.prepare, photos[str(PhotoKind.USER)], user_raw
            ),
        }
    except images.UnsupportedImageError as e:
        raise unsupported_media_type(None, ["jpeg", "png", "heic", "webp"]) from e
    del ref_raw, user_raw

    # ── 스크리닝 — 종전 API 와 같은 함수, 같은 기준. 이 응답 안에서 즉시 ───────
    #    ⚠️ OpenAI 로 가는 건 **얼굴을 가린 복사본**(vlm_jpeg)이다. 가공본(jpeg)을 넘기면
    #       얼굴이 그대로 나간다 — 이 두 줄이 이슈 3 의 마지막 방어선이다.
    try:
        screening = await photo_screening.screen(
            pipeline.screening_image(prepared[str(PhotoKind.USER)]),
            prepared[str(PhotoKind.REFERENCE)].vlm_jpeg,
        )
    except photo_screening.ScreeningUnavailable:
        raise screening_unavailable() from None
    if not screening.suitable:
        raise unsuitable_photo(
            screening.message, {"reason": screening.reason, "confidence": screening.confidence}
        )

    try:
        jobs = await run_in_threadpool(pipeline.submit, session, photos, prepared, pipeline_mode)
    except pipeline.PodBusy:
        raise pod_busy(pipeline.queued()) from None

    log.info("[%s] 접수 — mode=%s jobs=%s", session_id, pipeline_mode, list(jobs))
    return {
        "accepted": True,
        "session_id": str(session_id),
        "mode": pipeline_mode,
        "screening": {"suitable": True, "skipped": screening.skipped},
        "jobs": jobs,
        "crop_box": {kind: p.crop_box for kind, p in prepared.items()},
        "face_masked": {kind: p.face_box is not None for kind, p in prepared.items()},
        "photo_size": {
            kind: {"width": p.width, "height": p.height} for kind, p in prepared.items()
        },
    }
