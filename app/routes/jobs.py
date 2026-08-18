"""잡 폴링 API (F13).

프론트는 업로드 응답으로 받은 job_id를 여기로 폴링한다.
권장 주기: 세그/OCR 1.5초, VLM/루틴 2초.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.deps import OwnedSession, UserId
from app.errors import not_found
from app.schemas.enums import JobKind, JobStatus
from app.schemas.job import JobListResponse, JobResponse, JobSummary
from app.services import db
from app.worker import queue

router = APIRouter(tags=["jobs"])


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="잡 상태 조회",
    description="비동기 작업의 진행 상태를 조회합니다. 프론트는 이 엔드포인트를 폴링합니다.",
)
async def get_job(user_id: UserId, job_id: Annotated[UUID, Path()]) -> JobResponse:
    job = queue.get_job(job_id)
    if job is None:
        raise not_found("작업")

    # ⚠️ job → analysis_session → user_id 소유권 검증.
    #    빠뜨리면 job_id만 알면 남의 진행 상황이 보인다.
    session = db.get_session(UUID(str(job["session_id"])))
    if session is None or str(session["user_id"]) != str(user_id):
        raise not_found("작업")

    return JobResponse(**job, stalled=queue.is_stalled(job))


@router.get(
    "/sessions/{session_id}/jobs",
    response_model=JobListResponse,
    summary="세션의 잡 목록",
    description="진행률 표시용. kind / status 로 필터링할 수 있습니다.",
)
async def list_session_jobs(
    session: OwnedSession,
    kind: Annotated[JobKind | None, Query()] = None,
    status: Annotated[JobStatus | None, Query()] = None,
) -> JobListResponse:
    rows = queue.list_jobs(UUID(str(session["session_id"])), kind=kind, status=status)
    return JobListResponse(items=[JobSummary(**r) for r in rows])
