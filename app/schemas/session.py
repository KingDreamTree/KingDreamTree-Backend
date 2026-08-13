"""분석 세션 스키마 (F03)."""

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.enums import ReferenceSource, SessionStatus


class SessionResponse(BaseModel):
    session_id: str
    status: SessionStatus
    reference_source: ReferenceSource
    contraindications: list[Any] = Field(
        default_factory=list, description="세션 단위로 누적되는 금기 사항"
    )
    created_at: str


class PhotoStep(BaseModel):
    uploaded: bool
    segmented: bool = False
    job_status: str | None = Field(default=None, description="PENDING / PROCESSING / DONE / FAILED")


class InbodyStep(BaseModel):
    count: int = 0
    done: int = 0
    failed: int = 0
    pending: int = 0


class AnalysisStep(BaseModel):
    part_done: int = 0
    part_total: int = 0
    overall_status: str | None = None


class RoutineStep(BaseModel):
    active_version: int | None = None
    status: str | None = None


class SessionSteps(BaseModel):
    """⚠️ 새로고침·재진입 시 **어느 화면으로 보낼지** 판단하는 용도다.

    프론트가 테이블마다 따로 물어보지 않게 한 번에 모아서 준다.
    """

    reference_photo: PhotoStep
    user_photo: PhotoStep
    inbody: InbodyStep
    analysis: AnalysisStep
    routine: RoutineStep


class ActiveSessionResponse(SessionResponse):
    steps: SessionSteps
