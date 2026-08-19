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

from app.schemas.enums import DomainStatus, GenerationType, JobKind
from app.services import (
    coach_chat,
    db,
    contraindication,
    diagnosis_repo,
    exercise_catalog,
    inbody_repo,
    routine,
    routine_repo,
)
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


#: 진단 없이 만들어진 루틴에 붙이는 안내. build_routine 이 만드는 SEVEN_DAY_NOTICE·
#: CUT_NOTICE 와 같은 방식으로 notice 문자열에 이어붙인다.
_NO_DIAGNOSIS_NOTICE = "체형 비교 진단 없이 생성된 기본 루틴입니다. 진단을 완료하면 약점 부위에 맞춰 다시 만들어드려요."


def _diagnosis_inputs(session_id: UUID) -> tuple[list[str], list[str], bool]:
    """진단에서 가져올 세 가지 — 우선 개선 부위, 좌우 비대칭 부위, 진단 존재 여부.

    ⚠️ 진단이 없어도 예외를 던지지 않는다. 진단은 **가중치**이지 구성 요소가
       아니므로, 없으면 가중 없이 기본 볼륨 루틴이 나오면 된다 (D10).

    ⚠️ **"진단이 없음"과 "진단은 했는데 우선 부위가 없음"을 구분한다.** 후자는
       전 부위가 NONE(격차 없음)이라 정상적으로 생긴 빈 목록이라 안내가 필요
       없다. 전자만 이 함수의 세 번째 반환값(has_diagnosis)으로 표시한다
       (실측 — 진단 0건인 세션도 루틴이 조용히 만들어지고, 그게 폴백이라는
       표시가 화면 어디에도 없었다).
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
    return list(priority), asymmetric, overall is not None


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

    priority_parts, asymmetric, has_diagnosis = _diagnosis_inputs(session_id)
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
                # 전략 설명 문구에 한글 부위명을 쓴다 — 서비스 계층은 DB 를 보지 않는다.
                part_names={r["class_name"]: r.get("name_ko") or r["class_name"]
                            for r in db.list_body_parts()},
            )
        )
        routine_repo.replace_days(month_routine_id, plan["days"], days_per_week)

        notice = plan["notice"]
        if not has_diagnosis:
            # 진단이 아예 없어서 개인화 없이 나온 기본 볼륨 루틴이다 — 그렇다는
            # 표시 없이 나가면 "맞춤 루틴"으로 보인다 (실측, 모듈 상단 참고).
            notice = f"{notice} {_NO_DIAGNOSIS_NOTICE}" if notice else _NO_DIAGNOSIS_NOTICE

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
                    "notice": notice,
                    "boosts": plan["boosts"],
                    "weekly_sets": plan["weekly_sets"],
                    # «4주간 핵심 목표» 상자 본문. 생성 시점의 루틴에서 조립했으므로
                    # 나중에 읽어도 그때의 근거 그대로다.
                    "strategy": plan["strategy"],
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


def _interpretation(
    result: dict[str, Any], applied: list[dict[str, Any]], added: list[dict[str, Any]]
) -> str:
    """사용자에게 보이는 해석문 — **실제로 적용된 것만** 말하게 한다.

    ⚠️ patch_routine 의 interpretation 은 LLM 이 산문을 안 쓴 경우 **제안 목록**을
       요약한 값이다. 제안 중 일부는 검증에서 거부되므로(후보 밖 운동 등) 그대로
       쓰면 하지도 않은 일을 했다고 말하게 된다. 실측(2026-08-15): 교차 근육군
       교체가 거부됐는데 해석문은 "금기 등록, 운동 교체 (2건)" 이었다.
       LLM 이 직접 쓴 산문이 있으면 그건 존중하고, 없을 때만 적용 결과로 요약한다.
    """
    prose = result.get("llm_message")
    if isinstance(prose, str) and prose.strip():
        return prose.strip()
    return routine.summarize_applied(applied, added)


def _patch(job: dict[str, Any]) -> dict[str, Any]:
    """피드백 → 변경 해석 → **실제 적용** → FEEDBACK 새 버전 (2026-08-14).

    이전에는 해석·이력까지만 하고 적용을 안 했다. 버전 정책이 미확정이라서였는데,
    F12-b 코치 대화에서 그 정책이 확정됐으므로(새 버전 생성 → DONE 후 활성화)
    같은 경로를 그대로 쓴다. 도구 스키마도 coach_chat 과 통일했다
    (prompts/routine_patch.py 모듈 주석).

    ⚠️ 기존 버전을 고치지 않는다 — 이미 완료한 Day 의 기록과 어긋난다.
       변경이 0건이면 새 버전을 만들지 않고 이력만 남긴다.
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
    catalog = exercise_catalog.load_catalog()
    candidates = coach_chat._candidates_by_group(days, catalog)

    result = asyncio.run(
        routine.patch_routine(
            current_routine={"days": days},
            feedback_text=feedback,
            contraindications=existing,
            candidates_by_group=candidates,
        )
    )

    # 도구 인자를 재검증한다 — coach_chat 과 같은 함수. 후보 밖 운동·없는 Day·
    # 범위 밖 delta 는 여기서 걸린다 (LLM 을 믿지 않는 지점).
    validated: list[dict[str, Any]] = []
    for change in result["changes"]:
        name, args = change.get("function"), change.get("args") or {}
        ok_args, err = coach_chat.validate_tool_call(name, args, days, candidates)
        if err is not None:
            log.warning("피드백 도구 거부: %s — %s", name, err)
            continue
        validated.append({"name": name, "args": ok_args})

    routine_calls = [c for c in validated if c["name"] != "flag_contraindication"]

    # 금기는 세션 단위로 누적한다 (revision 에는 이번 회차 증분만).
    added = result["contraindications_added"]
    if added:
        merged = existing + [a for a in added if a not in existing]
        client.table("analysis_session").update({"contraindications": merged}).eq(
            "session_id", str(session_id)
        ).execute()

    by_ref = {c["exercise_ref"]: c for c in catalog}
    new_days, applied = coach_chat.apply_changes_to_days(days, routine_calls, by_ref)

    # ⚠️ 금기 강제는 **조기 반환보다 앞에** 있어야 한다. LLM 이 금기만 등록하고
    #    운동을 안 건드리면 routine_calls 가 비는데, 그게 바로 통증이 기록만 되고
    #    루틴에 반영되지 않는 누수 경로다. 여기서 걸러야 새 버전이 만들어진다.
    merged_cx = existing + [a for a in added if a not in existing]
    new_days, enforced = contraindication.enforce(new_days, merged_cx, applied)
    applied = applied + enforced

    if not applied:
        # 적용할 루틴 변경이 없다 — 버전을 만들지 않고 이력만 남긴다.
        routine_repo.create_revision(
            month_routine_id,
            {
                "source_log_id": str(log_id_raw),
                "interpretation": _interpretation(result, [], added),
                "changes": [],
                "contraindications_added": added,
                "raw_response": result.get("raw_response"),
            },
        )
        return {
            "month_routine_id": str(month_routine_id),
            "changes": 0,
            "contraindications_added": len(added),
            "no_change": True,
        }

    days_per_week = row["exercise_days_per_week"]
    new_row = routine_repo.create_routine(session_id, days_per_week, GenerationType.FEEDBACK)
    new_id = UUID(str(new_row["month_routine_id"]))
    try:
        routine_repo.replace_days(new_id, new_days, days_per_week)
        routine_repo.update_routine(
            new_id,
            {
                "goal": row.get("goal"),
                "focus_areas": row.get("focus_areas"),
                "raw_response": {
                    "source": "WORKOUT_FEEDBACK",
                    "base_version": row["version"],
                    # ⚠️ 승계 안 하면 화면의 «4주간 핵심 목표» 본문이 사라진다
                    #    (2026-08-18 실측). 피드백 패치는 모드·가중 근거를 다시
                    #    계산하지 않으므로 이전 버전 설명이 여전히 맞는 말이다.
                    "strategy": (row.get("raw_response") or {}).get("strategy"),
                },
                "status": str(DomainStatus.DONE),
            },
        )
    except Exception:
        # 실패해도 이전 활성 버전은 그대로다 (work-b.md §6).
        routine_repo.update_routine(new_id, {"status": str(DomainStatus.FAILED)})
        raise

    routine_repo.activate(new_id, session_id)

    routine_repo.create_revision(
        new_id,
        {
            "previous_month_routine_id": str(month_routine_id),
            "source_log_id": str(log_id_raw),
            "interpretation": _interpretation(result, applied, added),
            "changes": applied,
            "contraindications_added": added,
            "raw_response": result.get("raw_response"),
        },
    )
    log.info("피드백 적용 — v%s → v%s, 변경 %d건", row["version"], new_row["version"], len(applied))

    return {
        "month_routine_id": str(new_id),
        "previous_month_routine_id": str(month_routine_id),
        "version": new_row["version"],
        "changes": len(applied),
        "contraindications_added": len(added),
    }


register(JobKind.ROUTINE_GEN, _generate)
register(JobKind.ROUTINE_PATCH, _patch)
register_preflight(_check_exercise_catalog)
