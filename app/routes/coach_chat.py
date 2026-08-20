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
from app.services import coach_chat, contraindication, exercise_catalog, routine_repo
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


def _today_day(days: list[dict[str, Any]], session_id: UUID) -> dict[str, Any]:
    """직전에 완료한 Day — 대화의 주제. 완료 기록이 없으면 Day 1.

    ⚠️ **버전 무관 카운트**(count_session_logs)로 센다. 피드백 [적용]마다
       루틴은 새 버전으로 갈리고 이전 수행 기록은 이전 버전에 남으므로,
       count_logs(month_routine_id) 로 세면 적용 직후 카운트가 리셋돼
       코치가 항상 Day 1 을 "오늘 한 날"로 짚는다 — 사용자가 방금 끝낸
       Day 의 운동을 "오늘 하신 운동이 아니에요"라며 거부하던 원인
       (실측 2026-08-21). 진행도 화면도 2026-08-15 에 같은 리셋 사고로
       세션 카운트로 바꿨다 (routine_repo.count_session_logs 주석).
    """
    done = routine_repo.count_session_logs(session_id)
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
    # ⚠️ #113 — 지금까지 MAX_TURNS는 프롬프트로만 강제됐다. 모델이 [마지막 턴]
    #    지시를 무시하면 무한정 이어질 수 있어 여기서 코드로 먼저 끊는다.
    if coach_chat.turns_exceeded(body.messages):
        raise invalid_request(f"대화 턴 상한({coach_chat.MAX_TURNS})을 넘었습니다. [적용]으로 마무리해주세요.")

    session_id = UUID(str(session["session_id"]))
    routine = _active_routine_or_404(session_id)
    month_routine_id = UUID(str(routine["month_routine_id"]))

    days = routine_repo.list_days(month_routine_id)
    if not days:
        raise not_found("루틴 Day")

    result = await coach_chat.chat_turn(
        messages=body.messages,
        day=_today_day(days, session_id),
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

    # 히스토리에서 도구 호출을 **재수집·재검증** — 클라이언트를 믿지 않는 지점.
    calls, finalized = coach_chat.collect_tool_calls(body.messages, days, candidates)
    if finalized is None:
        raise invalid_request("대화가 아직 마무리되지 않았습니다 (finalize 없음).")

    routine_calls = [c for c in calls if c["name"] != "flag_contraindication"]
    flags = [c["args"] for c in calls if c["name"] == "flag_contraindication"]
    added = [{"body_part": f["body_part"], "severity": f["severity"]} for f in flags]
    _merge_contraindications(session_id, added)  # 멱등 — chat 때 이미 넣었어도 중복 안 됨

    by_ref = {c["exercise_ref"]: c for c in catalog}
    new_days, applied = coach_chat.apply_changes_to_days(days, routine_calls, by_ref)

    # ⚠️ 금기 강제는 **조기 반환보다 앞에** 있어야 한다. LLM 이 금기만 등록하고
    #    운동을 안 건드리면 routine_calls 가 비는데, 그게 바로 통증이 기록만 되고
    #    루틴에 반영되지 않는 누수 경로다. 여기서 걸러야 새 버전이 만들어진다.
    new_days, enforced = contraindication.enforce(
        new_days, _load_contraindications(session_id), applied
    )
    applied = applied + enforced

    if not applied:
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
                "raw_response": {
                    "source": "COACH_CHAT",
                    "base_version": routine["version"],
                    # ⚠️ 시작 중량 배율도 **승계 + 병합**한다. 아래 strategy 와 같은
                    #    이유다 — 새 버전이 raw_response 를 통째로 갈아치우므로,
                    #    안 옮기면 "무겁다고 해서 낮췄는데 다음 피드백에서 원복"이
                    #    된다. 무게 자체(kg)는 저장하지 않는다: 배율만 남기고 kg 은
                    #    조회 시점에 현재 체중으로 다시 계산한다 (load_guide 주석).
                    "load_adjust": coach_chat.merge_load_adjust(routine, applied),
                    # ⚠️ 승계 안 하면 화면의 «4주간 핵심 목표» 본문이 사라진다 —
                    #    worker/handlers/routine.py 의 _patch 와 같은 이유·같은 처리
                    #    (2026-08-18, #91). 코치 대화도 새 버전을 만드는 동안
                    #    똑같이 raw_response 를 통째로 갈아치우고 있었다.
                    "strategy": (routine.get("raw_response") or {}).get("strategy"),
                },
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
