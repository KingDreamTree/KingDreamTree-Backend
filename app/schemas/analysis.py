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
    #: 진단 잡을 **아무도 집어가지 않고 있다** (워커가 꺼져 있을 때).
    #  ⚠️ completed 와 독립이다. completed=false 인 채로 영원히 머무는 상태를
    #     프론트가 "느린 것"과 구분할 수 있게 하는 힌트다 — 회수는 워커가
    #     실행하는 코드라 워커가 없으면 이 잡은 저절로 FAILED 가 되지 않는다.
    stalled: bool = False


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


class BodyProfileDto(BaseModel):
    """한 사진의 전체 프레임 특징. **비교가 아니라 단독 관찰**이다.

    ⚠️ 골격이 아니라 «보이는 프레임 특징»이다 — 사진으로 뼈를 재지 않는다.
    """

    summary: str | None = None
    characteristics: list[str] = Field(default_factory=list)


class RealisticDirectionDto(BaseModel):
    """개선 방향. **priority·reason 은 규칙이 정한다** (scoring.decide_direction).

    summary 만 LLM 이 쓴 설명 문장이다.
    """

    priority: str | None = None
    reason: str | None = None
    summary: str | None = None


class ExerciseStrategyDto(BaseModel):
    """운동 전략. **mode·mode_basis·mode_reason 은 규칙이 정한다**
    (routine_mode.decide_mode — 체지방률 기준, 루틴 생성이 쓰는 값과 동일).

    focus·next_cycle 만 LLM 이 쓴 설명 문장이다.
    ⚠️ 단계(1→2→3) 개념은 없다. 루틴은 4주기 반복이고 주기마다 내용이 같다.
    """

    mode: str | None = None
    mode_basis: str | None = None
    mode_reason: str | None = None
    focus: list[str] = Field(default_factory=list)
    next_cycle: str | None = None


class OverallDiagnosisDto(BaseModel):
    similarity_score: int | None = None
    # ⚠️ 기본값은 RULE 이다. 점수는 scoring.compute_similarity() 가 규칙으로
    #    계산하고(handlers/vlm.py:349), vlm.py 는 LLM 이 점수를 보내와도 버린다.
    #    기본값이 "VLM" 이던 시절 화면이 "AI가 매긴 점수"라고 말하고 있었다.
    score_source: str = "RULE"
    score_rationale: str | None = None
    summary: str | None = None
    priority_parts: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    #: F09 가 원본 두 장을 직접 보고 낸 전체 형태 판단 (2026-08-17 추가).
    #  ⚠️ 전부 선택 필드다. 이 변경 이전 세션은 null / 빈 배열로 내려간다 —
    #     기존 프론트는 summary·priority_parts 만으로도 그대로 동작한다.
    silhouette: str | None = None
    key_differences: list[str] = Field(default_factory=list)
    #: 종합 판단의 확신도 0.0~1.0. ⚠️ similarity_score 와 무관하다 —
    #  점수는 규칙이 계산하고(score_source=RULE), 이건 "이 종합을 얼마나 믿을 수 있나"다.
    confidence: float | None = None
    #: 이번 비교에서 무엇을 못 봤는지. **규칙이 DB 에서 만든다** — LLM 문장이 아니다.
    #  "왜 이 부위는 비교를 못 했나"에 데이터로 답하기 위한 필드.
    comparison_limitations: list[str] = Field(default_factory=list)
    #: 두 사진 각각의 전체 프레임 특징 (2026-08-17). 비교는 silhouette 이 한다.
    user_profile: BodyProfileDto | None = None
    reference_profile: BodyProfileDto | None = None
    #: 개선 방향·운동 전략. ⚠️ 핵심 판정은 규칙이 하고 LLM 은 설명만 한다.
    realistic_direction: RealisticDirectionDto | None = None
    exercise_strategy: ExerciseStrategyDto | None = None
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
                    "score_source": "RULE",
                    "score_rationale": "상체 근육량 격차가 크고 하체는 근접",
                    "summary": "상체 중심 개선이 필요합니다.",
                    "priority_parts": ["Left_Upper_Arm", "Torso"],
                    "strengths": ["하체 균형이 좋습니다"],
                    "cautions": [],
                    "silhouette": "어깨 폭은 목표와 비슷하지만 상체 대비 하체 볼륨이 적습니다.",
                    "key_differences": ["상체 대비 하체 볼륨이 목표보다 적습니다"],
                    "confidence": 0.8,
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
#: ⚠️ 상수 하나가 진단 화면과 루틴 화면 양쪽을 덮는다. 고지를 여기 넣으면
#:    프론트 변경 없이 두 화면에 동시에 뜬다 — 별도 동의 체크박스를 두지 않는
#:    이유이기도 하다. 기록이 안 남는 체크박스는 UI 장식이고, 기록하려면
#:    users 컬럼이 필요해 해커톤 범위를 넘는다.
DISCLAIMER = (
    "본 분석은 사진 기반 추정이며 의학적 조언이 아닙니다. "
    "통증이 있거나 지속되면 운동을 중단하고 전문가와 상담하세요. "
    "업로드한 사진과 인바디 결과지는 분석을 위해 외부 AI(OpenAI)로 전송되며, "
    "계정을 삭제하면 서버에서 모두 삭제됩니다. "
    "본인 또는 사용 권한이 있는 사진만 올려주세요."
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
