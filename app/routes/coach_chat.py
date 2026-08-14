"""F12 — 코치 대화 (운동 후 피드백 → 다음 운동부터 반영).

설계 전문: docs/f12-coach-chat.md · services/coach_chat.py 모듈 주석

흐름
    POST /coach-chat        대화 1턴 (동기 — 잡 큐 아님, 사용자가 기다리는 중)
    POST /coach-chat/apply  [적용] 버튼 → FEEDBACK 새 버전 생성 + 활성화

⚠️ 대화는 **동기 API** 다. 잡 큐를 태우면 사용자가 답변마다 폴링해야 한다.
   호출당 LLM 1~3회(도구 루프)·수 초 — 동기로 감당 가능한 크기다.

⚠️ 적용은 **새 버전**으로만 한다. 활성 버전을 고치면 완료 기록과 어긋난다.
   변경이 0건이면 버전을 만들지 않는다 (no_change=true).

⚠️ 금기(flag_contraindication)만은 apply 를 기다리지 않고 **대화 중 즉시**
   세션에 누적한다 — 안전은 [적용] 버튼 뒤에 두지 않는다.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.deps import OwnedSession
from app.errors import invalid_request, not_found
from app.schemas.coach import (
    CoachApplyRequest,
    CoachApplyResponse,
    CoachChatRequest,
    CoachChatResponse,
    FinalizedCard,
)
from app.schemas.enums import DomainStatus, GenerationType
from app.services import coach_chat, exercise_catalog, routine_repo
from app.services.db import get_client

log = logging.getLogger("routes.coach_chat")

router = APIRouter(tags=["coach-chat"])


# --------------------------------------------------------------------------- #
# 공통 준비
# --------------------------------------------------------------------------- #


def _active_routine_or_404(session_id: UUID) -> dict[str, Any]:
    row = routine_repo.get_active(session_id)
    if row is None or row.get("status") != str(DomainStatus.DONE):
        raise not_found("활성 루틴")
    return row


def _load_contraindications(session_id: UUID) -> list[dict[str, Any]]:
    rows = (
        get_client()
        .table("analysis_session")
        .select("contraindications")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )
    return (rows[0].get("contraindications") if rows else None) or []


def _merge_contraindications(session_id: UUID, added: list[dict[str, Any]]) -> None:
    if not added:
        return
    existing = _load_contraindications(session_id)
    merged = existing + [a for a in added if a not in existing]
    get_client().table("analysis_session").update({"contraindications": merged}).eq(
        "session_id", str(session_id)
    ).execute()


def _today_day(days: list[dict[str, Any]], routine: dict[str, Any]) -> dict[str, Any]:
    """직전에 완료한 Day — 대화의 주제. 완료 기록이 없으면 Day 1."""
    done = routine_repo.count_logs(UUID(str(routine["month_routine_id"])))
    n = len(days) or 1
    # 방금 k개째를 끝냈다면 주제는 ((k-1) % n) + 1 번째 Day 다.
    order = ((max(done, 1) - 1) % n) + 1
    return next((d for d in days if d["day_order"] == order), days[0])


# --------------------------------------------------------------------------- #
# 대화
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/coach-chat",
    response_model=CoachChatResponse,
    summary="코치 대화 1턴 (운동 후 피드백)",
)
async def chat(session: OwnedSession, body: CoachChatRequest) -> CoachChatResponse:
    session_id = UUID(str(session["session_id"]))
    routine = _active_routine_or_404(session_id)
    month_routine_id = UUID(str(routine["month_routine_id"]))

    days = routine_repo.list_days(month_routine_id)
    if not days:
        raise not_found("루틴 Day")

    result = await coach_chat.chat_turn(
        messages=body.messages,
        day=_today_day(days, routine),
        days=days,
        contraindications=_load_contraindications(session_id),
        catalog=exercise_catalog.load_catalog(),
    )

    # 안전은 [적용]을 기다리지 않는다 — 금기는 대화 중 즉시 누적.
    flags = [e["args"] for e in result["tool_events"] if e["name"] == "flag_contraindication"]
    if flags:
        _merge_contraindications(
            session_id,
            [{"body_part": f["body_part"], "severity": f["severity"]} for f in flags],
        )
        log.info("대화 중 금기 등록 %d건 (session=%s)", len(flags), session_id)

    return CoachChatResponse(
        reply=result["reply"],
        messages=result["messages"],
        tool_events=result["tool_events"],
        finalized=FinalizedCard(**result["finalized"]) if result["finalized"] else None,
        turn=result["turn"],
        max_turns=result["max_turns"],
    )


# --------------------------------------------------------------------------- #
# 적용
# --------------------------------------------------------------------------- #


@router.post(
    "/sessions/{session_id}/coach-chat/apply",
    response_model=CoachApplyResponse,
    summary="대화에서 합의된 변경을 새 버전으로 적용",
)
async def apply(session: OwnedSession, body: CoachApplyRequest) -> CoachApplyResponse:
    session_id = UUID(str(session["session_id"]))
    routine = _active_routine_or_404(session_id)
    month_routine_id = UUID(str(routine["month_routine_id"]))
    days_per_week = routine["exercise_days_per_week"]

    days = routine_repo.list_days(month_routine_id)
    catalog = exercise_catalog.load_catalog()
    candidates = coach_chat._candidates_by_group(days, catalog)
    allowed_refs = {c["exercise_ref"] for group in candidates.values() for c in group}

    # 히스토리에서 도구 호출을 **재수집·재검증** — 클라이언트를 믿지 않는 지점.
    calls, finalized = coach_chat.collect_tool_calls(body.messages, days, allowed_refs)
    if finalized is None:
        raise invalid_request("대화가 아직 마무리되지 않았습니다 (finalize 없음).")

    routine_calls = [c for c in calls if c["name"] != "flag_contraindication"]
    flags = [c["args"] for c in calls if c["name"] == "flag_contraindication"]
    added = [{"body_part": f["body_part"], "severity": f["severity"]} for f in flags]
    _merge_contraindications(session_id, added)  # 멱등 — chat 때 이미 넣었어도 중복 안 됨

    if not routine_calls:
        # 루틴 변경이 없다 — 버전을 만들지 않고 이력만 남긴다.
        routine_repo.create_revision(
            month_routine_id,
            {
                "interpretation": finalized.get("summary"),
                "changes": [],
                "contraindications_added": added,
                "raw_response": {"conversation": body.messages, "source": "COACH_CHAT"},
            },
        )
        return CoachApplyResponse(
            month_routine_id=month_routine_id,
            version=routine["version"],
            applied_changes=[],
            contraindications_added=added,
            no_change=True,
        )

    by_ref = {c["exercise_ref"]: c for c in catalog}
    new_days, applied = coach_chat.apply_changes_to_days(days, routine_calls, by_ref)

    # FEEDBACK 새 버전 — 실패 시 이전 활성 버전이 그대로 남는다.
    new_row = routine_repo.create_routine(session_id, days_per_week, GenerationType.FEEDBACK)
    new_id = UUID(str(new_row["month_routine_id"]))
    try:
        routine_repo.replace_days(new_id, new_days, days_per_week)
        routine_repo.update_routine(
            new_id,
            {
                "goal": routine.get("goal"),
                "focus_areas": routine.get("focus_areas"),
                "raw_response": {"source": "COACH_CHAT", "base_version": routine["version"]},
                "status": str(DomainStatus.DONE),
            },
        )
    except Exception:
        routine_repo.update_routine(new_id, {"status": str(DomainStatus.FAILED)})
        raise

    routine_repo.activate(new_id, session_id)

    routine_repo.create_revision(
        new_id,
        {
            "previous_month_routine_id": str(month_routine_id),
            "interpretation": finalized.get("summary"),
            "changes": applied,
            "contraindications_added": added,
            "raw_response": {"conversation": body.messages, "source": "COACH_CHAT"},
        },
    )

    log.info(
        "코치 대화 적용 — v%s → v%s, 변경 %d건 (session=%s)",
        routine["version"],
        new_row["version"],
        len(applied),
        session_id,
    )
    return CoachApplyResponse(
        month_routine_id=new_id,
        version=new_row["version"],
        applied_changes=applied,
        contraindications_added=added,
    )
