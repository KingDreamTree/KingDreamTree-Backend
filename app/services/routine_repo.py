"""루틴 저장·조회 (F10~F12) — 담당 B 소유.

app/services/db.py 는 공유 파일이라 B 전용 쿼리를 여기로 분리한다
(inbody_repo·diagnosis_repo 와 같은 이유).

━━ 주기 모델 (2026-08-14 확정) ━━

    month_routine 1개 = routine_day N행 (N = exercise_days_per_week)
    4주기 반복은 **데이터 복제가 아니라 조회 규칙**이다.

        완료수 = workout_log 행 수
        오늘의 Day = (완료수 mod N) + 1
        현재 주기 = (완료수 // N) + 1        (4주기 끝나면 완료)

━━ DB 가 못 막는 것 두 개 — 여기서 막는다 (담당 A 지적) ━━

    1. workout_log.session_id ↔ month_routine.session_id
       비정규화라 서로 어긋나게 넣어도 DB 는 통과시킨다.
    2. routine_day 행 수 ↔ month_routine.exercise_days_per_week
       N=3 인데 4행이 들어가도 통과한다.

    복합 FK·트리거까지 갈 위험은 아니라는 데 합의했고, 대신 저장 시점에
    검증한다. 검증을 건너뛸 수 있는 경로를 만들지 않는 게 중요하다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.enums import DomainStatus, GenerationType
from app.services.db import get_client

#: 한 루틴이 반복되는 주기 수. 4주 프로그램이라는 뜻.
TOTAL_CYCLES = 4


class RoutineIntegrityError(RuntimeError):
    """DB 제약으로는 못 막는 불일치. 저장 전에 여기서 걸린다."""


# --------------------------------------------------------------------------- #
# month_routine
# --------------------------------------------------------------------------- #


def get_active(session_id: UUID) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table("month_routine")
        .select("*")
        .eq("session_id", str(session_id))
        .eq("is_active", True)
        .order("version", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def get_routine(month_routine_id: UUID) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table("month_routine")
        .select("*")
        .eq("month_routine_id", str(month_routine_id))
        .execute()
        .data
    )
    return rows[0] if rows else None


def get_owned_routine(month_routine_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    """month_routine → analysis_session → user_id 로 소유권 확인.

    ⚠️ 불일치도 None(→404). 403 은 id 존재를 알려준다 (deps.py 규약).
    """
    from app.services.db import get_session

    row = get_routine(month_routine_id)
    if row is None:
        return None
    session = get_session(UUID(str(row["session_id"])))
    if session is None or str(session["user_id"]) != str(user_id):
        return None
    return row


def list_versions(session_id: UUID) -> list[dict[str, Any]]:
    return (
        get_client()
        .table("month_routine")
        .select(
            "month_routine_id,version,exercise_days_per_week,goal,generation_type,"
            "is_active,status,created_at"
        )
        .eq("session_id", str(session_id))
        .order("version", desc=True)
        .execute()
        .data
    )


def next_version(session_id: UUID) -> int:
    rows = (
        get_client()
        .table("month_routine")
        .select("version")
        .eq("session_id", str(session_id))
        .order("version", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return (rows[0]["version"] + 1) if rows else 1


def create_routine(
    session_id: UUID,
    days_per_week: int,
    generation_type: GenerationType = GenerationType.INITIAL,
) -> dict[str, Any]:
    """PENDING 상태로 껍데기만 만든다.

    ⚠️ is_active 는 여기서 켜지 않는다. 생성 중에 활성으로 올리면 실패했을 때
       사용자가 볼 활성 루틴이 사라진다 (work-b.md §6).
    """
    return (
        get_client()
        .table("month_routine")
        .insert(
            {
                "session_id": str(session_id),
                "version": next_version(session_id),
                "exercise_days_per_week": days_per_week,
                "generation_type": str(generation_type),
                "is_active": False,
                "status": str(DomainStatus.PENDING),
            }
        )
        .execute()
        .data[0]
    )


def update_routine(month_routine_id: UUID, patch: dict[str, Any]) -> dict[str, Any]:
    return (
        get_client()
        .table("month_routine")
        .update(patch)
        .eq("month_routine_id", str(month_routine_id))
        .execute()
        .data[0]
    )


def activate(month_routine_id: UUID, session_id: UUID) -> None:
    """이 버전을 활성으로 올리고 나머지를 내린다.

    ⚠️ **DONE 인 것만 활성화한다.** 생성 중/실패한 버전을 올리면 화면이 빈다.
    ⚠️ month_routine 행을 삭제하지 않는다 — workout_log 가 CASCADE 로 딸려
       사라져 사용자의 수행 기록이 통째로 날아간다. is_active=false 로만 내린다.
    """
    routine = get_routine(month_routine_id)
    if routine is None or routine["status"] != DomainStatus.DONE:
        raise RoutineIntegrityError("완료되지 않은 루틴은 활성화할 수 없습니다.")

    client = get_client()
    client.table("month_routine").update({"is_active": False}).eq(
        "session_id", str(session_id)
    ).execute()
    client.table("month_routine").update({"is_active": True}).eq(
        "month_routine_id", str(month_routine_id)
    ).execute()


# --------------------------------------------------------------------------- #
# routine_day / routine_day_exercise
# --------------------------------------------------------------------------- #


def replace_days(
    month_routine_id: UUID,
    days: list[dict[str, Any]],
    days_per_week: int,
) -> None:
    """Day 와 운동을 통째로 교체한다.

    ⚠️ **행 수 검증** — DB 는 routine_day 행 수와 exercise_days_per_week 의
       일치를 막지 못한다 (담당 A 지적). N=3 인데 4행이 들어가면 "오늘의 Day"
       계산이 어긋나 사용자가 영영 Day 4 를 못 본다.

    ⚠️ delete → insert 로 통일한다. UNIQUE (month_routine_id, day_order) 라
       잡 재시도(attempts>1)에서 그냥 INSERT 하면 두 번째 시도가 터진다.
    """
    if len(days) != days_per_week:
        raise RoutineIntegrityError(
            f"Day 수가 맞지 않습니다: {len(days)}행 vs 설정 {days_per_week}일"
        )
    orders = sorted(d["day_order"] for d in days)
    if orders != list(range(1, days_per_week + 1)):
        raise RoutineIntegrityError(f"day_order 가 1..{days_per_week} 연속이 아닙니다: {orders}")

    client = get_client()
    client.table("routine_day").delete().eq("month_routine_id", str(month_routine_id)).execute()

    for day in days:
        row = (
            client.table("routine_day")
            .insert(
                {
                    "month_routine_id": str(month_routine_id),
                    "day_order": day["day_order"],
                    "title": day.get("title"),
                    "estimated_duration_min": day.get("estimated_duration_min"),
                }
            )
            .execute()
            .data[0]
        )
        exercises = [
            {
                "routine_day_id": row["routine_day_id"],
                "order_index": e["order_index"],
                "exercise_ref": e.get("exercise_ref"),
                "name": e["name"],
                "image_url": e.get("image_url"),
                # ⚠️ 두 이름을 모두 받는다. 생성 경로(build_routine)는 "kind" 로 주고,
                #    재적용 경로(피드백·코치 대화)는 list_days 가 읽어온 DB 행이라
                #    "exercise_kind" 다. 하나만 보면 유산소가 STRENGTH 로 저장돼
                #    routine_day_exercise_kind_chk 에 걸린다 (sets 가 없으므로).
                "exercise_kind": e.get("kind") or e.get("exercise_kind") or "STRENGTH",
                "muscle_group": e.get("muscle_group"),
                "sets": e.get("sets"),
                "reps": e.get("reps"),
                "duration_min": e.get("duration_min"),
                "rest_sec": e.get("rest_sec"),
                "rir": e.get("rir"),
                "boosted_by": e.get("boosted_by"),
                "note": e.get("note"),
            }
            for e in day["exercises"]
        ]
        if exercises:
            client.table("routine_day_exercise").insert(exercises).execute()


def list_days(month_routine_id: UUID) -> list[dict[str, Any]]:
    """Day + 운동 전체. day_order·order_index 순으로 정렬해 돌려준다."""
    client = get_client()
    days = (
        client.table("routine_day")
        .select("*")
        .eq("month_routine_id", str(month_routine_id))
        .order("day_order")
        .execute()
        .data
    )
    if not days:
        return []

    ids = [d["routine_day_id"] for d in days]
    exercises = (
        client.table("routine_day_exercise")
        .select("*")
        .in_("routine_day_id", ids)
        .order("order_index")
        .execute()
        .data
    )
    by_day: dict[str, list[dict[str, Any]]] = {}
    for e in exercises:
        by_day.setdefault(e["routine_day_id"], []).append(e)

    for d in days:
        d["exercises"] = by_day.get(d["routine_day_id"], [])
    return days


def get_day(month_routine_id: UUID, day_order: int) -> dict[str, Any] | None:
    for day in list_days(month_routine_id):
        if day["day_order"] == day_order:
            return day
    return None


# --------------------------------------------------------------------------- #
# workout_log — 진행 상태
# --------------------------------------------------------------------------- #


def count_logs(month_routine_id: UUID) -> int:
    return (
        get_client()
        .table("workout_log")
        .select("workout_log_id", count="exact")
        .eq("month_routine_id", str(month_routine_id))
        .limit(1)
        .execute()
        .count
        or 0
    )


def progress(month_routine_id: UUID, days_per_week: int) -> dict[str, Any]:
    """완료 수행 횟수로 현재 위치를 계산한다 (요일 무관 — 하루 밀려도 안 깨진다).

    ⚠️ 날짜가 아니라 **수행 횟수** 기준이다. 응답에 day_source 를 실어
       나중에 날짜 기준으로 바꿔도 프론트가 어느 방식인지 알 수 있게 한다.
    """
    done = count_logs(month_routine_id)
    total = days_per_week * TOTAL_CYCLES
    completed = done >= total
    return {
        "completed_count": done,
        "total_count": total,
        "cycle_no": min(done // days_per_week + 1, TOTAL_CYCLES),
        "next_day_order": (done % days_per_week) + 1,
        "is_completed": completed,
        "percent": round(done / total * 100) if total else 0,
        "day_source": "COUNT",
    }


def list_logs(session_id: UUID) -> list[dict[str, Any]]:
    return (
        get_client()
        .table("workout_log")
        .select("*")
        .eq("session_id", str(session_id))
        .order("completed_at", desc=True)
        .execute()
        .data
    )


def create_log(
    session_id: UUID,
    month_routine_id: UUID,
    routine_day_id: UUID,
    cycle_no: int,
    feedback_text: str | None = None,
) -> dict[str, Any]:
    """수행 기록. 저장 전에 소유 관계를 검증한다.

    ⚠️ **session_id 일치 검증** — workout_log 는 session_id 와 month_routine_id 를
       둘 다 들고 있는데 후자로 전자를 유도할 수 있다. 비정규화라 서로 어긋나게
       넣어도 DB 가 막지 못한다 (담당 A 지적). 여기서 막는다.
    """
    routine = get_routine(month_routine_id)
    if routine is None:
        raise RoutineIntegrityError("루틴을 찾을 수 없습니다.")
    if str(routine["session_id"]) != str(session_id):
        raise RoutineIntegrityError("루틴과 세션이 일치하지 않습니다.")

    day = (
        get_client()
        .table("routine_day")
        .select("month_routine_id")
        .eq("routine_day_id", str(routine_day_id))
        .execute()
        .data
    )
    if not day or str(day[0]["month_routine_id"]) != str(month_routine_id):
        raise RoutineIntegrityError("Day 가 이 루틴에 속하지 않습니다.")

    return (
        get_client()
        .table("workout_log")
        .insert(
            {
                "session_id": str(session_id),
                "month_routine_id": str(month_routine_id),
                "routine_day_id": str(routine_day_id),
                "cycle_no": cycle_no,
                "feedback_text": feedback_text,
            }
        )
        .execute()
        .data[0]
    )


# --------------------------------------------------------------------------- #
# routine_revision (F12)
# --------------------------------------------------------------------------- #


def create_revision(month_routine_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
    return (
        get_client()
        .table("routine_revision")
        .insert({**row, "month_routine_id": str(month_routine_id)})
        .execute()
        .data[0]
    )


def list_revisions(session_id: UUID) -> list[dict[str, Any]]:
    """이 세션의 모든 루틴 버전에 걸친 변경 이력.

    ⚠️ feedback_text 는 workout_log 에만 있다 — revision 에 중복 저장하지 않고
       조회 시 조인한다 (work-b.md §6).
    """
    client = get_client()
    routines = (
        client.table("month_routine")
        .select("month_routine_id")
        .eq("session_id", str(session_id))
        .execute()
        .data
    )
    if not routines:
        return []

    revisions = (
        client.table("routine_revision")
        .select("*")
        .in_("month_routine_id", [r["month_routine_id"] for r in routines])
        .order("created_at", desc=True)
        .execute()
        .data
    )

    log_ids = [r["source_log_id"] for r in revisions if r.get("source_log_id")]
    feedback: dict[str, str] = {}
    if log_ids:
        logs = (
            client.table("workout_log")
            .select("workout_log_id,feedback_text")
            .in_("workout_log_id", log_ids)
            .execute()
            .data
        )
        feedback = {log["workout_log_id"]: log.get("feedback_text") for log in logs}

    for r in revisions:
        r["feedback_text"] = feedback.get(r.get("source_log_id"))
    return revisions
