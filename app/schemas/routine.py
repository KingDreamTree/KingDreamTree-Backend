"""F10~F12 — 루틴 생성·조회·수행 기록 DTO.

⚠️ 주기 모델: 루틴 1개 = Day 1..N (N = 주당 운동 일수), 4주기 반복.
   Day 1~28 이 아니고 요일도 아니다. `day_order` 는 주기 내 순서다.

⚠️ `InbodySnapshot` 은 제거했다 (2026-08-14). 필드명을 DB 와 다르게 두었는데
   (`body_fat_pct` ↔ `body_fat_percentage`), 지금 루틴 생성은 인바디 **행을
   그대로** 받는다(`routine_mode.decide_mode`). 이 DTO 를 넘기면 체지방률을
   못 읽어 **에러 없이 BALANCE 로 떨어진다.** 아무도 쓰지 않는 채로 그 함정만
   남아 있어 지웠다.
"""

from uuid import UUID

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.enums import DomainStatus, GenerationType

# ── F10 — 생성 ───────────────────────────────────────────────────────────────


class RoutineCreateRequest(BaseModel):
    """POST /sessions/{id}/routines — 생성 및 운동 일수 조정.

    ⚠️ month_routine_id 를 클라이언트가 보내지 않는다. 서버가 활성 버전을 정한다.
    """

    exercise_days_per_week: int = Field(ge=1, le=7, description="주당 운동 일수 (N)")

    model_config = {"json_schema_extra": {"example": {"exercise_days_per_week": 3}}}


class RoutineCreateResponse(BaseModel):
    month_routine_id: UUID
    job_id: UUID
    version: int
    status: DomainStatus
    #: 이미 같은 일수로 진행 중인 생성이 있어 기존 잡을 돌려줬는가 (요금 중복 방지)
    reused: bool = False


# ── 조회 ─────────────────────────────────────────────────────────────────────


class ExerciseDto(BaseModel):
    order_index: int
    name: str
    #: ExerciseDB 원본 id. 이미지로 되짚을 수 있고 "지어낸 운동이 아님"의 근거다.
    exercise_ref: str | None = None
    image_url: str | None = None
    exercise_kind: str = "STRENGTH"
    muscle_group: str | None = None

    sets: int | None = None
    reps: int | None = None
    duration_min: int | None = None
    rest_sec: int | None = None
    #: "N회 남기고 멈추는 무게". ⚠️ 중량(kg)은 추정하지 않는다 (D9).
    rir: int | None = None
    #: 이 운동이 어느 진단 부위 때문에 볼륨을 더 받았는지 (개인화 문구 근거)
    boosted_by: str | None = None
    note: str | None = None


class RoutineDayDto(BaseModel):
    day_order: int = Field(description="주기 내 순서 (1..N). 요일 아님")
    title: str | None = None
    estimated_duration_min: int | None = None
    exercises: list[ExerciseDto] = Field(default_factory=list)


class RoutineProgressDto(BaseModel):
    completed_count: int
    total_count: int
    cycle_no: int = Field(description="현재 주기 (1~4)")
    next_day_order: int
    is_completed: bool
    percent: int
    #: 진행 계산 방식. 나중에 날짜 기준으로 바꿔도 프론트가 구분할 수 있게.
    day_source: str = "COUNT"


class RoutineStrategyDto(BaseModel):
    """«4주간 핵심 목표» 상자 본문 — **실제로 생성된 루틴에서 조립한다.**

    ⚠️ LLM 이 쓰지 않는다. 모드는 체지방률 판정 결과, volume 은 실제로 얹은 세트 수,
       cardio_days 는 슬롯에 실제로 들어간 날 수다. 설명과 루틴이 어긋날 수 없다.
    ⚠️ goal 은 한 줄 제목, 이건 그 아래 본문이다 — 둘은 다른 필드다.
    """

    headline: str | None = None
    #: "왜 이렇게 짰나" — **줄글 한 문단.** 항목 나열이 아니라 이어지는 산문이다.
    body: str | None = None
    mode: str | None = None
    mode_reason: str | None = None
    #: 부위별로 실제 얹은 세트 수 — 진단이 루틴을 바꾼 유일한 지점.
    volume: list[dict[str, Any]] = Field(default_factory=list)
    strength_days: int | None = None
    cardio_days: int | None = None


class RoutineDetailResponse(BaseModel):
    month_routine_id: UUID
    version: int
    exercise_days_per_week: int
    total_cycles: int = 4
    goal: str | None = None
    focus_areas: list[str] = Field(default_factory=list)
    generation_type: GenerationType = GenerationType.INITIAL
    status: DomainStatus
    is_active: bool

    days: list[RoutineDayDto] = Field(default_factory=list)
    progress: RoutineProgressDto
    notice: str | None = None
    #: 루틴 구성 근거 (2026-08-18). 없으면 null — 이 필드 이전 루틴은 안 갖고 있다.
    strategy: RoutineStrategyDto | None = None
    disclaimer: str


class RoutineVersionItem(BaseModel):
    month_routine_id: UUID
    version: int
    exercise_days_per_week: int
    goal: str | None = None
    generation_type: GenerationType
    is_active: bool
    status: DomainStatus


class TodayRoutineResponse(BaseModel):
    """오늘 해야 할 Day. 요일이 아니라 **다음 미완료 Day** 다."""

    month_routine_id: UUID
    cycle_no: int
    day: RoutineDayDto
    progress: RoutineProgressDto
    disclaimer: str


# ── F12 — 수행 기록 ──────────────────────────────────────────────────────────


class WorkoutLogCreateRequest(BaseModel):
    """POST /sessions/{id}/workout-logs — 완료 + 피드백.

    ⚠️ month_routine_id 는 서버가 활성 버전으로 채운다. 클라이언트가 보내지 않는다.
    """

    day_order: int = Field(ge=1, le=7)
    cycle_no: int = Field(ge=1, le=4)
    feedback_text: str | None = Field(default=None, max_length=1000)

    model_config = {
        "json_schema_extra": {
            "example": {"day_order": 1, "cycle_no": 1, "feedback_text": "무릎이 좀 아팠어요"}
        }
    }


class WorkoutLogCreateResponse(BaseModel):
    workout_log_id: UUID
    #: 피드백이 있어 루틴 패치 잡을 등록했으면 그 id. 없으면 null.
    job_id: UUID | None = None
    progress: RoutineProgressDto


class WorkoutLogItem(BaseModel):
    workout_log_id: UUID
    routine_day_id: UUID
    cycle_no: int
    completed_at: str
    feedback_text: str | None = None


class RevisionItem(BaseModel):
    routine_revision_id: UUID
    month_routine_id: UUID
    interpretation: str | None = None
    changes: list[dict] = Field(default_factory=list)
    contraindications_added: list[dict] = Field(default_factory=list)
    #: workout_log 에서 조인해 온 원본 피드백 (revision 에 중복 저장하지 않는다)
    feedback_text: str | None = None
    created_at: str
