"""F12 — 운동 완료 기록 + 피드백 반영.

흐름
    POST /workout-logs
      → 소유·정합성 검증 (routine_repo 가 DB 가 못 막는 것까지 본다)
      → workout_log 저장
      → 피드백이 있으면 ROUTINE_PATCH 잡 등록 (없으면 잡 없음)

⚠️ 피드백이 없으면 잡을 만들지 않는다. "완료" 체크만 한 경우까지 LLM 을 부르면
   요금이 수행 횟수만큼 나간다.

⚠️ `feedback_text` 는 `workout_log` 에만 저장한다. `routine_revision` 에
   중복 저장하지 않고 조회 시 조인한다 (work-b.md §6).
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.deps import OwnedSession
from app.errors import invalid_request, not_found
from app.schemas.enums import JobKind
from app.schemas.routine import (
    RevisionItem,
    RoutineProgressDto,
    WorkoutLogCreateRequest,
    WorkoutLogCreateResponse,
    WorkoutLogItem,
)
from app.services import routine_repo
from app.worker import queue

router = APIRouter(tags=["workout-logs"])


@router.post(
    "/sessions/{session_id}/workout-logs",
    response_model=WorkoutLogCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="운동 완료 + 피드백 (피드백 있으면 루틴 패치 큐잉)",
)
async def create_log(
    session: OwnedSession,
    body: WorkoutLogCreateRequest,
) -> WorkoutLogCreateResponse:
    session_id = UUID(str(session["session_id"]))

    routine = routine_repo.get_active(session_id)
    if routine is None:
        raise not_found("활성 루틴")

    month_routine_id = UUID(str(routine["month_routine_id"]))
    if body.day_order > routine["exercise_days_per_week"]:
        raise invalid_request(
            f"Day {body.day_order} 는 이 루틴에 없습니다 "
            f"(주 {routine['exercise_days_per_week']}일).",
            {"day_order": body.day_order, "max": routine["exercise_days_per_week"]},
        )

    day = routine_repo.get_day(month_routine_id, body.day_order)
    if day is None:
        raise not_found("Day")

    try:
        log = routine_repo.create_log(
            session_id=session_id,
            month_routine_id=month_routine_id,
            routine_day_id=UUID(str(day["routine_day_id"])),
            cycle_no=body.cycle_no,
            feedback_text=body.feedback_text,
        )
    except routine_repo.RoutineIntegrityError as e:
        raise invalid_request(str(e)) from e

    # 피드백이 있을 때만 패치 잡. 완료 체크만으로 LLM 을 부르지 않는다.
    job_id = None
    if body.feedback_text and body.feedback_text.strip():
        job = queue.enqueue(
            session_id,
            JobKind.ROUTINE_PATCH,
            {
                "month_routine_id": str(month_routine_id),
                "workout_log_id": str(log["workout_log_id"]),
            },
        )
        job_id = job["job_id"]

    return WorkoutLogCreateResponse(
        workout_log_id=log["workout_log_id"],
        job_id=job_id,
        progress=RoutineProgressDto(
            **routine_repo.progress(month_routine_id, routine["exercise_days_per_week"])
        ),
    )


@router.get(
    "/sessions/{session_id}/workout-logs",
    response_model=list[WorkoutLogItem],
    summary="수행 기록",
)
async def list_logs(session: OwnedSession) -> list[WorkoutLogItem]:
    rows = routine_repo.list_logs(UUID(str(session["session_id"])))
    return [
        WorkoutLogItem(
            workout_log_id=r["workout_log_id"],
            routine_day_id=r["routine_day_id"],
            cycle_no=r["cycle_no"],
            completed_at=str(r["completed_at"]),
            feedback_text=r.get("feedback_text"),
        )
        for r in rows
    ]


@router.get(
    "/sessions/{session_id}/revisions",
    response_model=list[RevisionItem],
    summary='"왜 루틴이 바뀌었는지" 변경 이력',
)
async def list_revisions(session: OwnedSession) -> list[RevisionItem]:
    rows = routine_repo.list_revisions(UUID(str(session["session_id"])))
    return [
        RevisionItem(
            routine_revision_id=r["routine_revision_id"],
            month_routine_id=r["month_routine_id"],
            interpretation=r.get("interpretation"),
            changes=r.get("changes") or [],
            contraindications_added=r.get("contraindications_added") or [],
            feedback_text=r.get("feedback_text"),
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]
