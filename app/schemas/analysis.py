"""F08 · F09 — 분석 요청/응답 DTO."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.enums import Confidence, DomainStatus, GapLevel, JobStatus, VlmInputType

# ── F08 — 분석 시작 ──────────────────────────────────────────────────────────


class PartJobRef(BaseModel):
    """부위 ↔ 잡 매핑.

    ⚠️ 모든 항목의 `job_id` 가 같다. 부위 진단은 **잡 1개가 전 부위를 처리**하기
       때문이다(호출 1회 = 요금 1회). 부위별로 폴링해도 동작하도록 형태만 유지한다.
    """

    job_id: UUID
    class_name: str


class AnalysisStartResponse(BaseModel):
    #: null 이면 이미 완료된 분석이라 새 잡을 만들지 않았다는 뜻이다
    #: (reused=true). 바로 `GET .../analysis` 로 결과를 읽으면 된다.
    part_job_id: UUID | None = None
    #: ⚠️ POST 시점에는 항상 null 이다. 종합 진단은 부위 진단 결과가 입력이라
    #:    부위 진단이 끝난 뒤에 등록된다. 진행 상황은 `GET .../analysis/progress`로 본다.
    overall_job_id: UUID | None = None
    part_count: int
    class_names: list[str]
    part_jobs: list[PartJobRef]
    #: 이미 진행 중인 분석이 있어 기존 잡을 그대로 돌려줬는가 (중복 호출 가드)
    reused: bool = False


# ── F08 — 진행률 ─────────────────────────────────────────────────────────────


class PartProgress(BaseModel):
    done: int
    failed: int
    total: int
    status: JobStatus


class OverallProgress(BaseModel):
    status: JobStatus


class AnalysisProgressResponse(BaseModel):
    part: PartProgress
    overall: OverallProgress
    completed: bool


# ── F09 — 결과 조회 ──────────────────────────────────────────────────────────


class PartDiagnosisDto(BaseModel):
    class_name: str
    name_ko: str | None = None
    part_group: str | None = None
    color_hex: str | None = None

    differences: list[str] = Field(default_factory=list)
    assessment: str | None = None
    gap_level: GapLevel | None = None
    priority: int | None = None
    confidence: Confidence | None = None
    #: 판단 불가 사유. gap_level 이 null 인 부위에만 채워진다.
    blocked_reason: str | None = None

    vlm_input_type: VlmInputType = VlmInputType.HIGHLIGHT
    status: DomainStatus


class OverallDiagnosisDto(BaseModel):
    similarity_score: int | None = None
    score_source: str = "VLM"
    score_rationale: str | None = None
    summary: str | None = None
    priority_parts: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    status: DomainStatus


class ExcludedPart(BaseModel):
    class_name: str
    name_ko: str | None = None
    reason: str
    side: str


class AnalysisResponse(BaseModel):
    """⚠️ 부위 일부가 FAILED 여도 200 이다 (work-b.md §6: 부분 실패 허용)."""

    overall: OverallDiagnosisDto | None = None
    parts: list[PartDiagnosisDto] = Field(default_factory=list)
    #: 비교 대상에서 빠진 부위와 사유. "왼팔은 왜 없지?"에 답하기 위한 것.
    excluded: list[ExcludedPart] = Field(default_factory=list)
    #: 이 분석에 반영된 인바디. 없으면 null (인바디는 선택 입력).
    inbody_id: UUID | None = None
    disclaimer: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "overall": {
                    "similarity_score": 68,
                    "score_source": "VLM",
                    "score_rationale": "상체 근육량 격차가 크고 하체는 근접",
                    "summary": "상체 중심 개선이 필요합니다.",
                    "priority_parts": ["Left_Upper_Arm", "Torso"],
                    "strengths": ["하체 균형이 좋습니다"],
                    "cautions": [],
                    "status": "DONE",
                },
                "parts": [
                    {
                        "class_name": "Left_Upper_Arm",
                        "name_ko": "왼팔 상완",
                        "part_group": "UPPER",
                        "color_hex": "#F76707",
                        "differences": ["상완 둘레가 얇음"],
                        "assessment": "레퍼런스 대비 상완 볼륨이 부족합니다.",
                        "gap_level": "MODERATE",
                        "priority": 2,
                        "confidence": "HIGH",
                        "vlm_input_type": "HIGHLIGHT",
                        "status": "DONE",
                    }
                ],
                "excluded": [],
                "disclaimer": "본 분석은 의학적 조언이 아닙니다.",
            }
        }
    }


#: 화면에 반드시 노출해야 하는 고지. 프론트 구현에 맡기면 빠진다.
DISCLAIMER = (
    "본 분석은 사진 기반 추정이며 의학적 조언이 아닙니다. "
    "통증이 있거나 지속되면 운동을 중단하고 전문가와 상담하세요."
)


def to_part_dto(row: dict[str, Any], master: dict[str, Any]) -> PartDiagnosisDto:
    """part_diagnosis 행 + body_part 마스터 → 응답 DTO.

    blocked_reason 은 컬럼이 아니라 raw_response.item 에 있다
    (handlers/vlm.py `_to_diagnosis_rows` 주석 참고).
    """
    item = (row.get("raw_response") or {}).get("item") or {}
    return PartDiagnosisDto(
        class_name=row["class_name"],
        name_ko=master.get("name_ko"),
        part_group=master.get("part_group"),
        color_hex=master.get("color_hex"),
        differences=row.get("differences") or [],
        assessment=row.get("assessment"),
        gap_level=row.get("gap_level"),
        priority=row.get("priority"),
        confidence=row.get("confidence"),
        blocked_reason=item.get("blocked_reason"),
        vlm_input_type=row.get("vlm_input_type") or VlmInputType.HIGHLIGHT,
        status=row["status"],
    )
