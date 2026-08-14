"""잡 스키마 — "작업 등록 후 폴링" 방식의 계약."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.enums import JobKind, JobStatus


class JobResponse(BaseModel):
    """GET /jobs/{job_id}"""

    job_id: UUID
    session_id: UUID
    kind: JobKind
    status: JobStatus
    attempts: int
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = Field(
        default=None,
        description="사용자에게 보여줘도 되는 실패 사유. 스택 트레이스·키는 담기지 않는다.",
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class JobSummary(BaseModel):
    """목록용 — payload/result는 뺀다."""

    job_id: UUID
    kind: JobKind
    status: JobStatus
    attempts: int
    created_at: datetime


class JobListResponse(BaseModel):
    items: list[JobSummary]


# ⚠️ 공통 JobEnqueued(202) 모델은 두지 않는다. 잡을 만드는 라우트들이
#    각자의 응답 모델에 job_id 를 직접 넣는다 — 응답에 photo_id·width 처럼
#    잡과 무관한 값이 같이 나가기 때문이다. (2026-08-14 제거)
