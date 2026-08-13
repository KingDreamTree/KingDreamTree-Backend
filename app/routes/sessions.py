"""F03 — 분석 세션 관리.

⚠️ analysis_session 은 **모든 소유권 검증의 기준점**이다.
   자식 리소스는 전부 여기까지 조인해 X-User-Id 와 일치하는지 확인한다.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.deps import UserId
from app.errors import active_session_exists, not_found
from app.schemas.enums import JobKind, PhotoKind
from app.schemas.session import (
    ActiveSessionResponse,
    AnalysisStep,
    InbodyStep,
    PhotoStep,
    RoutineStep,
    SessionResponse,
    SessionSteps,
)
from app.services import db
from app.worker import queue

router = APIRouter(tags=["sessions"])


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="새 분석 세션 시작",
)
async def create_session(user_id: UserId) -> SessionResponse:
    """⚠️ 진행 중 세션이 있으면 409.

    DB에 UNIQUE (user_id) WHERE status='ACTIVE' 가 걸려 있어 어차피 못 만든다.
    미리 확인해서 detail.session_id 를 돌려주면 프론트가 이어서 진행할 수 있다.
    """
    existing = db.get_active_session(user_id)
    if existing is not None:
        raise active_session_exists(str(existing["session_id"]))

    row = db.create_session(user_id)
    return SessionResponse(**row)


def _photo_step(session_id: UUID, kind: PhotoKind, job_kind: JobKind) -> PhotoStep:
    photo = db.get_photo(session_id, kind)
    if photo is None:
        return PhotoStep(uploaded=False)

    segmentation = db.get_segmentation(UUID(str(photo["photo_id"])))
    jobs = queue.list_jobs(session_id, kind=job_kind)
    return PhotoStep(
        uploaded=True,
        segmented=segmentation is not None,
        # 재업로드로 잡이 여러 개 생길 수 있으므로 가장 최근 것만 본다.
        job_status=jobs[-1]["status"] if jobs else None,
    )


def _inbody_step(session_id: UUID) -> InbodyStep:
    rows = db.rows_for_session("inbody", session_id, "status")
    statuses = [r["status"] for r in rows]
    return InbodyStep(
        count=len(rows),
        done=statuses.count("DONE"),
        failed=statuses.count("FAILED"),
        pending=statuses.count("PENDING"),
    )


def _analysis_step(session_id: UUID) -> AnalysisStep:
    parts = db.rows_for_session("part_diagnosis", session_id, "status")
    overall = db.rows_for_session("overall_diagnosis", session_id, "status")
    return AnalysisStep(
        part_done=sum(1 for p in parts if p["status"] == "DONE"),
        part_total=len(parts),
        overall_status=overall[0]["status"] if overall else None,
    )


def _routine_step(session_id: UUID) -> RoutineStep:
    rows = db.rows_for_session("month_routine", session_id, "version,status,is_active")
    active = next((r for r in rows if r.get("is_active")), None)
    if active is None:
        return RoutineStep()
    return RoutineStep(active_version=active["version"], status=active["status"])


@router.get(
    "/sessions/active",
    response_model=ActiveSessionResponse,
    summary="진행 중 세션 + 단계별 완료 여부",
)
async def get_active_session(user_id: UserId) -> ActiveSessionResponse:
    """새로고침·재진입 시 어느 화면으로 보낼지 판단하는 용도."""
    session: dict[str, Any] | None = db.get_active_session(user_id)
    if session is None:
        raise not_found("진행 중인 세션")

    session_id = UUID(str(session["session_id"]))
    steps = SessionSteps(
        reference_photo=_photo_step(session_id, PhotoKind.REFERENCE, JobKind.SEG_REFERENCE),
        user_photo=_photo_step(session_id, PhotoKind.USER, JobKind.SEG_USER),
        inbody=_inbody_step(session_id),
        analysis=_analysis_step(session_id),
        routine=_routine_step(session_id),
    )
    return ActiveSessionResponse(**session, steps=steps)
