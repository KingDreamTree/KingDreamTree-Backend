"""F10 · F11 — 4주 루틴 생성 / 조회 / 오늘의 루틴.

처리 분할
    조회        동기   — 이미 만들어진 루틴을 읽는 것뿐이다
    생성        비동기 — LLM 호출이 들어간다. 202 + job_id 폴링

⚠️ **중복 생성 = 요금 2배.** 같은 일수로 진행 중인 잡이 있으면 새로 만들지 않고
   기존 job_id 를 돌려준다. 운동 일수를 **바꾼** 경우는 새 버전이 맞으므로
   payload 의 일수까지 비교한다 (`payload_match`).

⚠️ 루틴 단위는 **주기당 N일 × 4주기**다. Day 1~28 도 요일도 아니다.
   "오늘의 루틴"은 완료 수행 횟수로 계산한다 — 하루 밀려도 안 깨진다.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, status

from app.deps import OwnedSession, UserId
from app.errors import not_found
from app.schemas.enums import DomainStatus, GenerationType, JobKind
from app.schemas.routine import (
    ExerciseDto,
    RoutineCreateRequest,
    RoutineCreateResponse,
    RoutineDayDto,
    RoutineDetailResponse,
    RoutineProgressDto,
    RoutineVersionItem,
    TodayRoutineResponse,
)
from app.services import routine_repo
from app.services.routine import DISCLAIMER
from app.worker import queue

router = APIRouter(tags=["routines"])


def _day_dto(day: dict) -> RoutineDayDto:
    return RoutineDayDto(
        day_order=day["day_order"],
        title=day.get("title"),
        estimated_duration_min=day.get("estimated_duration_min"),
        exercises=[ExerciseDto(**e) for e in day.get("exercises", [])],
    )


def _detail(routine: dict) -> RoutineDetailResponse:
    month_routine_id = UUID(str(routine["month_routine_id"]))
    # 진행도는 세션 전체에서 센다 — 피드백으로 버전이 갈려도 이어져야 한다.
    session_id = UUID(str(routine["session_id"]))
    days = routine_repo.list_days(month_routine_id)
    return RoutineDetailResponse(
        month_routine_id=month_routine_id,
        version=routine["version"],
        exercise_days_per_week=routine["exercise_days_per_week"],
        goal=routine.get("goal"),
        focus_areas=routine.get("focus_areas") or [],
        generation_type=routine.get("generation_type") or GenerationType.INITIAL,
        status=routine["status"],
        is_active=routine["is_active"],
        days=[_day_dto(d) for d in days],
        progress=RoutineProgressDto(
            **routine_repo.progress(month_routine_id, routine["exercise_days_per_week"], session_id)
        ),
        notice=(routine.get("raw_response") or {}).get("notice"),
        disclaimer=DISCLAIMER,
    )


# --------------------------------------------------------------------------- #
# 생성
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/routines",
    response_model=RoutineCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="4주 루틴 생성 (운동 일수 조정 = 새 버전)",
)
async def create_routine(
    session: OwnedSession,
    body: RoutineCreateRequest,
) -> RoutineCreateResponse:
    """운동 일수를 바꿔 다시 부르면 **새 버전**이 만들어진다.

    ⚠️ 기존 버전을 지우지 않는다. `month_routine` 을 삭제하면 `workout_log` 가
       CASCADE 로 딸려 사라져 수행 기록이 통째로 날아간다 (work-b.md §6).
       새 버전이 DONE 이 된 뒤에 워커가 is_active 를 넘긴다.
    """
    session_id = UUID(str(session["session_id"]))
    days = body.exercise_days_per_week

    # 같은 일수로 이미 만드는 중이면 그 잡을 그대로 — 다른 일수면 새 버전이 맞다
    existing = queue.find_open(
        session_id, JobKind.ROUTINE_GEN, payload_match={"days_per_week": days}
    )
    if existing is not None:
        routine = routine_repo.get_routine(UUID(str(existing["payload"]["month_routine_id"])))
        return RoutineCreateResponse(
            month_routine_id=routine["month_routine_id"],
            job_id=existing["job_id"],
            version=routine["version"],
            status=routine["status"],
            reused=True,
        )

    active = routine_repo.get_active(session_id)
    generation_type = (
        GenerationType.DAYS_CHANGED
        if active and active["exercise_days_per_week"] != days
        else GenerationType.INITIAL
    )
    routine = routine_repo.create_routine(session_id, days, generation_type)

    job = queue.enqueue(
        session_id,
        JobKind.ROUTINE_GEN,
        {"month_routine_id": str(routine["month_routine_id"]), "days_per_week": days},
    )
    return RoutineCreateResponse(
        month_routine_id=routine["month_routine_id"],
        job_id=job["job_id"],
        version=routine["version"],
        status=routine["status"],
        reused=False,
    )


# --------------------------------------------------------------------------- #
# 조회
# --------------------------------------------------------------------------- #


@router.get(
    "/sessions/{session_id}/routines/active",
    response_model=RoutineDetailResponse,
    summary="활성 루틴 전체 (Day 1..N + 진행 상태)",
)
async def get_active_routine(session: OwnedSession) -> RoutineDetailResponse:
    routine = routine_repo.get_active(UUID(str(session["session_id"])))
    if routine is None:
        raise not_found("활성 루틴")
    return _detail(routine)


@router.get(
    "/sessions/{session_id}/routines/today",
    response_model=TodayRoutineResponse,
    summary="오늘 해야 할 Day (완료 횟수 기준 — 요일 무관)",
)
async def get_today(session: OwnedSession) -> TodayRoutineResponse:
    """⚠️ 요일이 아니라 **다음 미완료 Day** 다. 하루 밀려도 순서가 안 깨진다."""
    session_id = UUID(str(session["session_id"]))
    routine = routine_repo.get_active(session_id)
    if routine is None:
        raise not_found("활성 루틴")

    month_routine_id = UUID(str(routine["month_routine_id"]))
    progress = routine_repo.progress(
        month_routine_id, routine["exercise_days_per_week"], session_id
    )
    day = routine_repo.get_day(month_routine_id, progress["next_day_order"])
    if day is None:
        raise not_found("오늘의 루틴")

    return TodayRoutineResponse(
        month_routine_id=month_routine_id,
        cycle_no=progress["cycle_no"],
        day=_day_dto(day),
        progress=RoutineProgressDto(**progress),
        disclaimer=DISCLAIMER,
    )


@router.get(
    "/sessions/{session_id}/routines",
    response_model=list[RoutineVersionItem],
    summary="버전 이력",
)
async def list_versions(session: OwnedSession) -> list[RoutineVersionItem]:
    rows = routine_repo.list_versions(UUID(str(session["session_id"])))
    return [RoutineVersionItem(**r) for r in rows]


@router.get(
    "/routines/{month_routine_id}/days/{day_order}",
    response_model=RoutineDayDto,
    summary="Day 상세",
)
async def get_day_detail(
    user_id: UserId,
    month_routine_id: Annotated[UUID, Path()],
    day_order: Annotated[int, Path(ge=1, le=7)],
) -> RoutineDayDto:
    """⚠️ `day_order` 는 주기 내 순서(1..N)다. 1~28 이 아니다."""
    routine = routine_repo.get_owned_routine(month_routine_id, user_id)
    if routine is None:
        raise not_found("루틴")

    day = routine_repo.get_day(month_routine_id, day_order)
    if day is None:
        raise not_found("Day")
    return _day_dto(day)
