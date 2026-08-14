"""F03 — 분석 세션 관리.

⚠️ analysis_session 은 **모든 소유권 검증의 기준점**이다.
   자식 리소스는 전부 여기까지 조인해 X-User-Id 와 일치하는지 확인한다.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.deps import CurrentUser, OwnedSession
from app.errors import active_session_exists, no_active_session
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
async def create_session(user: CurrentUser) -> SessionResponse:
    """⚠️ 진행 중 세션이 있으면 409.

    DB에 UNIQUE (user_id) WHERE status='ACTIVE' 가 걸려 있어 어차피 못 만든다.
    미리 확인해서 detail.session_id 를 돌려주면 프론트가 이어서 진행할 수 있다.

    ⚠️ UserId 가 아니라 CurrentUser 를 쓴다 — **사용자가 실재하는지까지** 본다.
       형식만 검사하면 없는 id 로 INSERT 가 들어가 FK 위반으로 **500** 이 났다.
       DB 를 초기화했는데 프론트에 옛 id 가 남아 있는 상황이 정확히 이 경로다.
       이때는 404 여야 프론트가 "재발급"으로 분기할 수 있다 (FRONTEND.md 안내와 일치).
       다른 라우트는 OwnedSession 을 거치므로 세션 조회에서 이미 404 가 난다.
    """
    user_id = UUID(str(user["user_id"]))
    existing = db.get_active_session(user_id)
    if existing is not None:
        raise active_session_exists(str(existing["session_id"]))

    row = db.create_session(user_id)
    return SessionResponse(**row)


def _photo_step(
    session_id: UUID,
    kind: PhotoKind,
    job_kind: JobKind,
    all_jobs: list[dict[str, Any]],
) -> PhotoStep:
    """⚠️ 잡 목록은 **호출부에서 한 번만 조회해** 넘긴다.

    부위별로 각각 조회하면 이 화면 하나에 왕복이 두 번 더 늘어난다.
    앱 진입마다 도는 경로라 왕복 수가 그대로 체감 지연이 된다.
    """
    photo = db.get_photo(session_id, kind)
    if photo is None:
        return PhotoStep(uploaded=False)

    segmentation = db.get_segmentation(UUID(str(photo["photo_id"])))
    jobs = [j for j in all_jobs if j["kind"] == str(job_kind)]
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
async def get_active_session(user: CurrentUser) -> ActiveSessionResponse:
    """새로고침·재진입 시 어느 화면으로 보낼지 판단하는 용도.

    ⚠️ CurrentUser 를 쓴다 — 사용자가 실재하는지까지 본다. 형식만 검사하면
       **계정이 사라진 경우에도 "진행 중인 분석 없음"** 으로 답해, 프론트가
       시작 화면을 띄우고 거기서 다시 404 를 맞는다.
       계정 없음 = NOT_FOUND / 세션 없음 = NO_ACTIVE_SESSION 으로 갈라서 준다.

    ⚠️ 이 엔드포인트는 왕복이 많다(세션·사진 2·세그 2·잡·인바디·진단 2·루틴).
       앱 진입마다 도는 경로라 느려지면 바로 체감된다. 화면에 필요한 단계가
       늘어나면 조회를 합치는 쪽을 먼저 고려할 것.
    """
    user_id = UUID(str(user["user_id"]))
    session: dict[str, Any] | None = db.get_active_session(user_id)
    if session is None:
        raise no_active_session()

    session_id = UUID(str(session["session_id"]))
    all_jobs = queue.list_jobs(session_id)
    steps = SessionSteps(
        reference_photo=_photo_step(
            session_id, PhotoKind.REFERENCE, JobKind.SEG_REFERENCE, all_jobs
        ),
        user_photo=_photo_step(session_id, PhotoKind.USER, JobKind.SEG_USER, all_jobs),
        inbody=_inbody_step(session_id),
        analysis=_analysis_step(session_id),
        routine=_routine_step(session_id),
    )
    return ActiveSessionResponse(**session, steps=steps)


@router.post(
    "/sessions/{session_id}/archive",
    response_model=SessionResponse,
    summary="진행 중 세션 종료 (새 분석을 시작할 수 있게)",
)
async def archive_session(session: OwnedSession) -> SessionResponse:
    """세션을 ARCHIVED 로 내린다.

    ⚠️ **이게 없으면 사용자는 첫 세션에 갇힌다.** DB 에 "사용자당 ACTIVE 는 하나"
       제약이 걸려 있어, 세션을 내릴 수단이 없으면 POST /sessions 가 영원히 409 다.
       레퍼런스를 잘못 올려 처음부터 다시 하고 싶을 때 빠져나갈 길이 없었다.
       유일한 탈출구가 계정 삭제(DELETE /users/me)였다.

    ⚠️ 사진과 분석 결과는 **지우지 않는다.** 지난 분석을 다시 볼 수 있어야 한다.
       (보관 기간 정책은 미정 — 정해지면 여기서 같이 정리하게 될 것이다)

    ⚠️ 이미 ARCHIVED 인 세션에 다시 호출해도 200 이다. 프론트가 재시도해도
       안전해야 한다 (같은 결과가 나오는 편이 낫다).
    """
    session_id = UUID(str(session["session_id"]))
    updated = db.archive_session(session_id)
    return SessionResponse(**(updated or session))
