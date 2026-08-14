"""ROUTINE_GEN / ROUTINE_PATCH 핸들러 — GPU 불필요, EC2 LLM 워커에서 돈다.

━━ ROUTINE_GEN ━━

    job.payload.{month_routine_id, days_per_week}
      → 진단(F09)·인바디 조회 (둘 다 없어도 진행한다)
      → build_routine() — 모드·골격·가중·후보·선택·조립
      → routine_day N행 + 운동 저장
      → status=DONE 으로 올린 **뒤에** is_active 전환

    ⚠️ **DONE 이 되기 전에 is_active 를 넘기지 않는다.** 생성 중에 넘겼다가
       실패하면 사용자가 볼 활성 루틴이 사라진다 (work-b.md §6).

━━ 기동 점검 (담당 A 요청, body_part 에서 겪은 사고) ━━

    마스터 테이블이 비었는데 워커가 그냥 뜨면, 잡은 "성공"으로 끝나면서
    결과만 비어 있다. body_part 가 비었을 때 비교 가능 부위가 늘 0 이 나왔고,
    화면엔 "재촬영하세요"만 떠서 사용자가 사진을 아무리 다시 찍어도 안 고쳐졌다.
    카탈로그가 비면 LLM 이 고를 후보가 없어 같은 식으로 조용히 이상한 루틴이 나온다.
    → 그런 상태면 **워커를 아예 띄우지 않는다.**
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.schemas.enums import DomainStatus, JobKind
from app.services import diagnosis_repo, inbody_repo, routine, routine_repo
from app.services.db import get_client
from app.worker.registry import HANDLERS, register, register_preflight

log = logging.getLogger("worker.routine")

#: 이 점검이 적용되는 잡 종류. 이 중 하나라도 실제로 처리하는 워커일 때만 본다.
_ROUTINE_KINDS = (JobKind.ROUTINE_GEN, JobKind.ROUTINE_PATCH)

#: 이보다 적으면 슬롯별 후보가 모자라 루틴 품질을 보장할 수 없다.
#: 실측 200건 기준 슬롯당 12~42개가 나오므로, 절반 아래로 떨어지면 이상 상황이다.
MIN_CATALOG_ROWS = 100


def _check_exercise_catalog() -> None:
    """운동 카탈로그가 채워져 있는지. 비었으면 기동을 거부한다.

    ⚠️ **루틴 잡을 실제로 처리하는 워커에서만 본다.** run.py 의 `_load_handlers` 는
       LLM 계열 워커면 이 모듈을 무조건 import 하므로, 조건 없이 검사하면
       VLM 전용 워커(RunPod 분리 배치)까지 카탈로그 때문에 못 뜬다.
       핸들러가 등록되기 전에는 조용히 건너뛴다.
    """
    if not any(k in HANDLERS for k in _ROUTINE_KINDS):
        return

    try:
        res = (
            get_client()
            .table("exercise_catalog")
            .select("exercise_ref", count="exact")
            .eq("is_beginner_safe", True)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001 — 테이블이 아직 없을 수도 있다
        raise RuntimeError(
            "exercise_catalog 를 읽을 수 없습니다. 마이그레이션이 반영됐는지 확인하세요 "
            "(db/migrations/2026-08-14_exercise_catalog.sql). 원인: " + type(e).__name__
        ) from e

    count = res.count or 0
    if count < MIN_CATALOG_ROWS:
        raise RuntimeError(
            f"운동 카탈로그가 비어 있거나 부족합니다 (is_beginner_safe=true {count}행, "
            f"최소 {MIN_CATALOG_ROWS}행 필요). "
            "python scripts/seed_exercise_catalog.py 를 먼저 실행하세요. "
            "⚠️ 이 상태로 두면 LLM 이 고를 후보가 없어 빈 루틴이 조용히 생성됩니다."
        )

    log.info("운동 카탈로그 확인 — 초보 가용 %d행", count)


class RoutineInputError(RuntimeError):
    """입력이 잘못돼 다시 시도해도 같다 (worker/run.py `_is_retryable`)."""

    retryable = False


# --------------------------------------------------------------------------- #
# ROUTINE_GEN
# --------------------------------------------------------------------------- #


def _diagnosis_inputs(session_id: UUID) -> tuple[list[str], list[str]]:
    """진단에서 가져올 두 가지 — 우선 개선 부위, 좌우 비대칭 부위.

    ⚠️ 진단이 없어도 예외를 던지지 않는다. 진단은 **가중치**이지 구성 요소가
       아니므로, 없으면 가중 없이 기본 볼륨 루틴이 나오면 된다 (D10).
    """
    overall = diagnosis_repo.get_overall(session_id)
    priority = (overall or {}).get("priority_parts") or []

    # 좌우 쌍 중 한쪽만 우선 부위로 뽑혔으면 그 부위는 비대칭 신호로 본다
    asymmetric = [
        p
        for p in priority
        if (p.startswith("Left_") and p.replace("Left_", "Right_", 1) not in priority)
        or (p.startswith("Right_") and p.replace("Right_", "Left_", 1) not in priority)
    ]
    return list(priority), asymmetric


def _generate(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload") or {}
    routine_id_raw = payload.get("month_routine_id")
    if not routine_id_raw:
        raise RoutineInputError("job.payload.month_routine_id 가 없습니다.")

    month_routine_id = UUID(str(routine_id_raw))
    row = routine_repo.get_routine(month_routine_id)
    if row is None:
        # 사용자가 생성 완료 전에 세션을 지운 경우 — 실패가 아니라 건너뛴다.
        return {"month_routine_id": str(month_routine_id), "skipped": "이미 삭제된 루틴"}

    session_id = UUID(str(row["session_id"]))
    days_per_week = row["exercise_days_per_week"]

    priority_parts, asymmetric = _diagnosis_inputs(session_id)
    inbody_row = inbody_repo.latest_done(session_id)

    log.info(
        "루틴 생성 시작 — %d일 / 우선부위 %s / 인바디 %s",
        days_per_week,
        priority_parts or "없음",
        "있음" if inbody_row else "없음",
    )

    try:
        plan = asyncio.run(
            routine.build_routine(
                days_per_week=days_per_week,
                inbody=inbody_row,
                priority_parts=priority_parts,
                asymmetric_parts=asymmetric,
            )
        )
        routine_repo.replace_days(month_routine_id, plan["days"], days_per_week)
        routine_repo.update_routine(
            month_routine_id,
            {
                "goal": plan["goal"],
                "focus_areas": plan["focus_areas"],
                # ⚠️ 프롬프트 전문·API 키는 넣지 않는다. 재현에 필요한 것만.
                "raw_response": {
                    "mode": plan["mode"],
                    "mode_basis": plan["mode_basis"],
                    "mode_reason": plan["mode_reason"],
                    "notice": plan["notice"],
                    "boosts": plan["boosts"],
                    "weekly_sets": plan["weekly_sets"],
                    "selection": plan["selection"],
                },
                "status": str(DomainStatus.DONE),
            },
        )
    except Exception:
        routine_repo.update_routine(month_routine_id, {"status": str(DomainStatus.FAILED)})
        raise

    # ⚠️ DONE 이 된 뒤에만 활성으로 넘긴다 (모듈 주석).
    routine_repo.activate(month_routine_id, session_id)

    return {
        "month_routine_id": str(month_routine_id),
        "version": row["version"],
        "mode": plan["mode"],
        "mode_basis": plan["mode_basis"],
        "days": len(plan["days"]),
        "boosts": plan["boosts"],
        "selection": plan["selection"]["source"],
        "inbody": "USED" if inbody_row else "NONE",
    }


# --------------------------------------------------------------------------- #
# ROUTINE_PATCH
# --------------------------------------------------------------------------- #


def _patch(job: dict[str, Any]) -> dict[str, Any]:
    """피드백 → 루틴 변경 오퍼레이션을 해석해 이력으로 남긴다.

    ⚠️ 지금은 **해석과 이력 기록까지**다. 실제 운동 교체 적용은 다음 단계다 —
       변경을 적용하려면 새 버전을 만들어야 하는데(기존 버전을 고치면 이미 완료한
       Day 의 기록과 어긋난다), 그 정책이 아직 확정되지 않았다.
       지금도 "왜 바뀌었는지"(GET /revisions)와 금기 누적은 동작한다.
    """
    payload = job.get("payload") or {}
    routine_id_raw = payload.get("month_routine_id")
    log_id_raw = payload.get("workout_log_id")
    if not routine_id_raw or not log_id_raw:
        raise RoutineInputError("job.payload 에 month_routine_id / workout_log_id 가 없습니다.")

    month_routine_id = UUID(str(routine_id_raw))
    row = routine_repo.get_routine(month_routine_id)
    if row is None:
        return {"skipped": "이미 삭제된 루틴"}

    session_id = UUID(str(row["session_id"]))
    client = get_client()

    logs = (
        client.table("workout_log")
        .select("feedback_text")
        .eq("workout_log_id", str(log_id_raw))
        .execute()
        .data
    )
    feedback = (logs[0].get("feedback_text") if logs else None) or ""
    if not feedback.strip():
        return {"skipped": "피드백 없음"}

    session = (
        client.table("analysis_session")
        .select("contraindications")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )
    existing = (session[0].get("contraindications") if session else None) or []

    days = routine_repo.list_days(month_routine_id)
    result = asyncio.run(
        routine.patch_routine(
            current_routine={"days": days},
            feedback_text=feedback,
            contraindications=existing,
        )
    )

    routine_repo.create_revision(
        month_routine_id,
        {
            "source_log_id": str(log_id_raw),
            "interpretation": result["interpretation"],
            "changes": result["changes"],
            "contraindications_added": result["contraindications_added"],
            "raw_response": result.get("raw_response"),
        },
    )

    # 금기는 세션 단위로 누적한다 (revision 에는 이번 회차 증분만).
    added = result["contraindications_added"]
    if added:
        merged = existing + [a for a in added if a not in existing]
        client.table("analysis_session").update({"contraindications": merged}).eq(
            "session_id", str(session_id)
        ).execute()

    return {
        "month_routine_id": str(month_routine_id),
        "changes": len(result["changes"]),
        "contraindications_added": len(added),
    }


register(JobKind.ROUTINE_GEN, _generate)
register(JobKind.ROUTINE_PATCH, _patch)
register_preflight(_check_exercise_catalog)
