"""F08 · F09 — 부위별 비교 진단 + 종합 진단.

처리 분할
    선행 조건·부위 산출   동기   — 재촬영이 필요한지는 즉시 답해야 한다
    VLM 호출              비동기 — 202 + job_id 폴링

⚠️ **중복 호출 = 요금 2배.** 이 파일의 가드 세 겹이 그것을 막는다.
   ① 진행 중인 VLM_PART 잡이 있으면 기존 잡을 반환
   ② VLM_PART 는 끝났고 VLM_OVERALL 만 도는 중간 상태도 진행 중으로 취급
   ③ 이미 완료된 분석은 `force=true` 없이 다시 돌리지 않는다
   ②를 빼먹으면 부위 진단이 끝난 직후 새로고침 한 번에 Vision 호출이 통째로
   한 번 더 나간다 — 가장 비싼 호출이 두 번 되는 것이다.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.config import settings
from app.deps import OwnedSession
from app.errors import ApiError, precondition_not_met
from app.schemas.analysis import (
    DISCLAIMER,
    AnalysisProgressResponse,
    AnalysisResponse,
    AnalysisStartResponse,
    ExcludedPart,
    OverallDiagnosisDto,
    OverallProgress,
    PartJobRef,
    PartProgress,
    to_part_dto,
)
from app.schemas.enums import DomainStatus, JobKind, JobStatus
from app.services import db, diagnosis_repo, inbody_repo
from app.worker import queue

router = APIRouter(tags=["analysis"])


def insufficient_parts(count: int, excluded: list[dict[str, Any]]) -> ApiError:
    """비교 가능한 부위가 모자라다 → 재촬영 안내.

    ⚠️ 사용자가 취할 행동이 있는 에러다. 어느 부위가 왜 빠졌는지 함께 돌려줘야
       "팔이 나오게 다시 찍어주세요"를 화면이 만들 수 있다.
    """
    return ApiError(
        422,
        "INSUFFICIENT_PARTS",
        f"비교 가능한 부위가 {count}개뿐입니다. "
        "팔·다리가 드러나고 몸 전체가 보이도록 다시 촬영해주세요.",
        {
            "count": count,
            "min_required": settings.min_comparable_parts,
            "excluded": excluded,
        },
    )


def _latest_job(session_id: UUID, kind: JobKind) -> dict[str, Any] | None:
    jobs = queue.list_jobs(session_id, kind=kind)
    return jobs[-1] if jobs else None


# --------------------------------------------------------------------------- #
# 분석 시작
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/analysis",
    response_model=AnalysisStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="부위별 + 종합 VLM 진단 큐잉",
)
async def start_analysis(
    session: OwnedSession,
    force: Annotated[bool, Query(description="완료된 분석을 무시하고 다시 실행")] = False,
) -> AnalysisStartResponse:
    session_id = UUID(str(session["session_id"]))
    context = diagnosis_repo.build_comparison_context(session_id)

    if not context["ready"]:
        raise precondition_not_met(
            "사진 분석이 아직 끝나지 않았습니다. 잠시 후 다시 시도해주세요.",
            {"missing": context["missing"]},
        )

    parts = context["parts"]
    class_names = [p["class_name"] for p in parts]

    # ── 가드 ①② — 진행 중이면 새로 만들지 않는다 ──────────────────────────
    for kind in (JobKind.VLM_PART, JobKind.VLM_OVERALL):
        if queue.find_open(session_id, kind) is not None:
            existing = _latest_job(session_id, JobKind.VLM_PART)
            return _start_response(existing, class_names, reused=True)

    # ── 가드 ③ — 이미 끝난 분석을 새로고침으로 다시 돌리지 않는다 ─────────
    overall = diagnosis_repo.get_overall(session_id)
    if not force and overall is not None and overall["status"] == DomainStatus.DONE:
        return _start_response(_latest_job(session_id, JobKind.VLM_PART), class_names, reused=True)

    if len(parts) < settings.min_comparable_parts:
        raise insufficient_parts(len(parts), context["excluded"])

    if force:
        # 이전 결과를 남겨두면 새 진단과 섞여 화면에 옛 부위가 남는다.
        diagnosis_repo.clear_diagnoses(session_id)

    # ⚠️ VLM_OVERALL 은 여기서 만들지 않는다. 종합 진단의 입력은 부위 진단
    #    결과이므로 VLM_PART 핸들러가 끝난 직후 등록한다 (handlers/vlm.py 주석).
    job = queue.enqueue(session_id, JobKind.VLM_PART)
    return _start_response(job, class_names, reused=False)


def _start_response(
    job: dict[str, Any] | None,
    class_names: list[str],
    reused: bool,
) -> AnalysisStartResponse:
    job_id = UUID(str(job["job_id"])) if job else None
    return AnalysisStartResponse(
        part_job_id=job_id,
        overall_job_id=None,
        part_count=len(class_names),
        class_names=class_names,
        part_jobs=(
            [PartJobRef(job_id=job_id, class_name=n) for n in class_names] if job_id else []
        ),
        reused=reused,
    )


# --------------------------------------------------------------------------- #
# 진행률
# --------------------------------------------------------------------------- #


@router.get(
    "/sessions/{session_id}/analysis/progress",
    response_model=AnalysisProgressResponse,
    summary="진행률 (로딩 화면용)",
)
async def analysis_progress(session: OwnedSession) -> AnalysisProgressResponse:
    session_id = UUID(str(session["session_id"]))

    counts = diagnosis_repo.count_part_diagnoses(session_id)
    part_job = _latest_job(session_id, JobKind.VLM_PART)
    overall_job = _latest_job(session_id, JobKind.VLM_OVERALL)

    part_status = JobStatus(part_job["status"]) if part_job else JobStatus.PENDING
    overall_status = JobStatus(overall_job["status"]) if overall_job else JobStatus.PENDING

    # 종합까지 끝나야 결과 화면으로 넘어간다. 부위 진단이 끝나도 점수·요약이
    # 없으면 화면이 비어 보인다.
    completed = overall_status in (JobStatus.DONE, JobStatus.FAILED) or (
        part_status == JobStatus.FAILED
    )

    return AnalysisProgressResponse(
        part=PartProgress(
            done=counts["done"],
            failed=counts["failed"],
            # ⚠️ total 은 잡 개수가 아니라 진단 행 수다. 잡은 1개뿐이라
            #    잡으로 세면 화면의 "완료 3/9"를 만들 수 없다.
            total=counts["total"],
            status=part_status,
        ),
        overall=OverallProgress(status=overall_status),
        completed=completed,
    )


# --------------------------------------------------------------------------- #
# 결과 조회
# --------------------------------------------------------------------------- #


@router.get(
    "/sessions/{session_id}/analysis",
    response_model=AnalysisResponse,
    summary="종합 + 부위별 진단 전체",
)
async def get_analysis(session: OwnedSession) -> AnalysisResponse:
    """⚠️ 부위 일부가 FAILED 여도 **200** 이다. 실패한 부위는 status 로 표시만 한다."""
    session_id = UUID(str(session["session_id"]))

    rows = diagnosis_repo.list_part_diagnoses(session_id)
    master = {m["class_name"]: m for m in db.list_body_parts()}
    context = diagnosis_repo.build_comparison_context(session_id)
    overall_row = diagnosis_repo.get_overall(session_id)
    inbody = inbody_repo.latest_done(session_id)

    # 마스터의 display_order 를 따른다 — 화면에서 부위 순서가 매번 바뀌면 안 된다.
    order = {name: i for i, name in enumerate(master)}
    rows.sort(key=lambda r: order.get(r["class_name"], 999))

    return AnalysisResponse(
        overall=(
            OverallDiagnosisDto(
                similarity_score=overall_row.get("similarity_score"),
                score_source=overall_row.get("score_source") or "VLM",
                score_rationale=overall_row.get("score_rationale"),
                summary=overall_row.get("summary"),
                priority_parts=overall_row.get("priority_parts") or [],
                strengths=overall_row.get("strengths") or [],
                cautions=overall_row.get("cautions") or [],
                status=overall_row["status"],
            )
            if overall_row
            else None
        ),
        parts=[to_part_dto(r, master.get(r["class_name"], {})) for r in rows],
        excluded=[ExcludedPart(**e) for e in context["excluded"]],
        inbody_id=UUID(str(inbody["inbody_id"])) if inbody else None,
        disclaimer=DISCLAIMER,
    )
