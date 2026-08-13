"""F07 인바디 DTO.

⚠️ app/schemas/ 는 공유 계약 — 변경 시 상대 리뷰 필수 (github_rules).
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.enums import InbodySegment

# --------------------------------------------------------------------------- #
# 응답
# --------------------------------------------------------------------------- #


class InbodyCreateResponse(BaseModel):
    """POST → 202. 추출은 비동기 — job_id 폴링으로 완료 확인."""

    inbody_id: UUID
    job_id: UUID
    status: str  # PENDING


class InbodyListItem(BaseModel):
    inbody_id: UUID
    status: str
    device_type: str | None = None
    measured_at: date | None = None
    created_at: datetime


class InbodySegmentDto(BaseModel):
    segment: InbodySegment
    lean_mass: float | None = None
    fat_mass: float | None = None


class InbodyDetailResponse(BaseModel):
    """GET /inbody/{id}.

    * fields — inbody 테이블 컬럼 (사용자 수정 반영본)
    * smi — **백엔드가 계산** (골격근량 ÷ 신장(m)²). VLM 추출값이 아님.
    * validation — WARN 필드만 {"필드": {"level", "message"}} (확인 화면 강조용)
    * raw_ocr — OCR 원본. 추출 컬럼 확정 전까지 함께 반환 (api-spec 부록 §774)
    """

    inbody_id: UUID
    status: str
    device_type: str | None = None
    measured_at: date | None = None
    fields: dict[str, Any]
    smi: float | None = None
    segments: list[InbodySegmentDto]
    validation: dict[str, Any]
    raw_ocr: dict[str, Any] | None = None
    verified_at: datetime | None = None


# --------------------------------------------------------------------------- #
# 요청
# --------------------------------------------------------------------------- #


class InbodyPatchRequest(BaseModel):
    """사용자 확인·수정. ⚠️ raw_ocr 은 어떤 경우에도 덮어쓰지 않는다."""

    fields: dict[str, Any] | None = None
    segments: list[InbodySegmentDto] | None = None
    verified: bool = Field(default=False, description="true면 verified_at 기록 + status DONE")
