"""잡 큐 — 등록 / 원자적 선점 / 완료 / 실패.

담당 A(SEG_*)와 담당 B(OCR/VLM/ROUTINE)가 함께 쓰는 공유 모듈이다.
핸들러는 app/worker/handlers/ 아래에 각자 추가하고, 여기는 건드리지 않는다.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import UUID

from app.config import settings
from app.schemas.enums import JobKind, JobStatus
from app.services.db import get_client

#: 아직 끝나지 않은 잡 (중복 등록 판정용)
#: ⚠️ PROCESSING 이라고 다 살아있는 건 아니다 — 워커가 죽으면 그대로 멈춘다.
#:    find_open() 은 is_stale() 로 좀비를 걸러낸다.
OPEN_STATUSES: tuple[str, ...] = (JobStatus.PENDING, JobStatus.PROCESSING)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 등록
# --------------------------------------------------------------------------- #


def enqueue(
    session_id: UUID,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """잡을 새로 등록한다."""
    row = {"session_id": str(session_id), "kind": str(kind)}
    if payload is not None:
        row["payload"] = payload
    return get_client().table("job").insert(row).execute().data[0]


def is_stale(job: dict[str, Any], older_than_sec: int | None = None) -> bool:
    """PROCESSING 인데 너무 오래 붙잡고 있는 잡인가 (= 워커가 죽었나).

    ⚠️ 시각 비교는 UTC 기준이다. started_at 은 TIMESTAMPTZ 로 저장된다.
    """
    if job.get("status") != JobStatus.PROCESSING:
        return False
    started = job.get("started_at")
    if not started:
        # PROCESSING 인데 started_at 이 없다 — 정상 경로에서는 나올 수 없다. 좀비로 본다.
        return True

    limit = older_than_sec if older_than_sec is not None else settings.job_stale_after_sec
    try:
        at = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
    except ValueError:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() > limit


def find_open(
    session_id: UUID,
    kind: JobKind,
    payload_match: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """이 세션에 아직 끝나지 않은 같은 종류의 잡이 있는지 찾는다.

    ⚠️ **좀비(워커가 죽어 PROCESSING 에 멈춘 잡)는 '진행 중'으로 치지 않는다.**
       치면 enqueue_once() 가 그 좀비를 계속 돌려주고, 사용자가 새로고침해도 같은
       job_id 만 받아 분석이 영구 정지한다. 재시도할 방법이 아예 없어진다.
       좀비를 빼면 새 잡이 생겨 살아 있는 워커가 이어받는다.

       (좀비 자체의 상태 정리는 reclaim_stale() 이 한다. 여기서는 판단만 바꾼다.)
    """
    rows = (
        get_client()
        .table("job")
        .select("*")
        .eq("session_id", str(session_id))
        .eq("kind", str(kind))
        .in_("status", list(OPEN_STATUSES))
        .order("created_at")
        .execute()
        .data
    )
    rows = [r for r in rows if not is_stale(r)]
    if payload_match:
        rows = [
            r
            for r in rows
            if all((r.get("payload") or {}).get(k) == v for k, v in payload_match.items())
        ]
    return rows[0] if rows else None


def enqueue_once(
    session_id: UUID,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
    payload_match: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """이미 진행 중인 잡이 있으면 그걸 그대로 돌려준다.

    ⚠️ VLM 계열에서 이게 없으면 사용자가 새로고침 한 번 할 때마다 요금이 두 배가 된다.
    반환값의 두 번째는 "새로 만들었는가".
    """
    existing = find_open(session_id, kind, payload_match)
    if existing is not None:
        return existing, False
    return enqueue(session_id, kind, payload), True


# --------------------------------------------------------------------------- #
# 선점 (워커)
# --------------------------------------------------------------------------- #


def claim(kinds: Iterable[JobKind]) -> dict[str, Any] | None:
    """PENDING 잡 하나를 PROCESSING으로 선점한다. 없으면 None.

    ⚠️ SELECT 후 무조건 UPDATE 하면 워커 2개가 같은 잡을 집는다.
       UPDATE의 WHERE에 `status='PENDING'`을 함께 걸어 compare-and-swap으로
       만든다. 다른 워커가 먼저 가져갔다면 0행이 갱신되고, 다음 후보로 넘어간다.
    """
    client = get_client()
    kind_values = [str(k) for k in kinds]

    for _ in range(settings.job_claim_retries):
        candidates = (
            client.table("job")
            .select("job_id,attempts")
            .eq("status", JobStatus.PENDING)
            .in_("kind", kind_values)
            .lt("attempts", settings.job_max_attempts)
            .order("created_at")
            .limit(1)
            .execute()
            .data
        )
        if not candidates:
            return None

        job_id = candidates[0]["job_id"]
        updated = (
            client.table("job")
            .update(
                {
                    "status": JobStatus.PROCESSING,
                    "attempts": candidates[0]["attempts"] + 1,
                    "started_at": _now(),
                }
            )
            .eq("job_id", job_id)
            .eq("status", JobStatus.PENDING)  # ← CAS 조건
            .execute()
            .data
        )
        if updated:
            return updated[0]
        # 다른 워커가 먼저 가져갔다 — 다음 후보로

    return None


# --------------------------------------------------------------------------- #
# 좀비 회수
# --------------------------------------------------------------------------- #


def reclaim_stale(
    kinds: Iterable[JobKind],
    older_than_sec: int | None = None,
) -> list[dict[str, Any]]:
    """워커가 죽어 PROCESSING 인 채로 멈춘 잡을 되살린다.

    ⚠️ 이게 없으면 팟을 끄거나 워커가 죽는 순간 그 잡은 **영영 안 끝난다.**
       attempts 는 이미 올라갔고 status 는 PROCESSING 이라 claim() 이 다시 집지
       않는다. 사용자는 로딩 화면에서 무한정 기다린다.

    ⚠️ **attempts 가 한도에 닿은 잡을 PENDING 으로 되돌리면 안 된다.**
       claim() 이 `attempts < job_max_attempts` 로 거르므로, 되돌려도 아무도
       집지 않는 채 PENDING 으로 남는다. 좀비의 상태만 바뀔 뿐이다.
       그런 잡은 FAILED 로 종결시켜 화면이 실패를 표시할 수 있게 한다.

    ⚠️ 자기가 처리하는 kind 만 회수할 것. 남의 kind 까지 건드리면 멀쩡히 돌고
       있는 다른 워커의 잡을 빼앗아 같은 일을 두 번 하게 된다.

    반환: 실제로 회수한 잡들 (status 가 무엇으로 바뀌었는지 포함)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=older_than_sec if older_than_sec is not None else settings.job_stale_after_sec
    )
    client = get_client()

    # ⚠️ started_at 이 NULL 인 PROCESSING 도 같이 집는다.
    #    `lt("started_at", ...)` 는 SQL 에서 NULL 을 걸러낸다. is_stale() 은 그런 잡을
    #    좀비로 보는데 여기서만 빠져나가면, **어느 쪽에서도 회수되지 않고 영원히
    #    PROCESSING 으로 남는다** (find_open 은 진행 중으로 안 치고, 여기는 못 잡는다).
    #    정상 경로에서는 claim() 이 항상 채우지만, 부분 갱신·수동 삽입으로 생길 수 있다.
    kind_values = [str(k) for k in kinds]
    base = client.table("job").select("job_id,kind,attempts,started_at")
    candidates = (
        base.eq("status", JobStatus.PROCESSING)
        .in_("kind", kind_values)
        .lt("started_at", cutoff.isoformat())
        .execute()
        .data
    )
    candidates += (
        client.table("job")
        .select("job_id,kind,attempts,started_at")
        .eq("status", JobStatus.PROCESSING)
        .in_("kind", kind_values)
        .is_("started_at", "null")
        .execute()
        .data
    )

    reclaimed: list[dict[str, Any]] = []
    for job in candidates:
        exhausted = job["attempts"] >= settings.job_max_attempts
        patch: dict[str, Any] = (
            {
                "status": JobStatus.FAILED,
                "error": "처리 중 서버가 중단되어 실패로 정리했습니다. 다시 시도해주세요.",
                "finished_at": _now(),
            }
            if exhausted
            else {"status": JobStatus.PENDING}
        )

        # ⚠️ CAS. 그 사이 워커가 살아나 complete/fail 했을 수 있다.
        updated = (
            client.table("job")
            .update(patch)
            .eq("job_id", job["job_id"])
            .eq("status", JobStatus.PROCESSING)
            .execute()
            .data
        )
        if updated:
            reclaimed.append(updated[0])

    return reclaimed


# --------------------------------------------------------------------------- #
# 종료
# --------------------------------------------------------------------------- #


def complete(job_id: UUID, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return (
        get_client()
        .table("job")
        .update(
            {
                "status": JobStatus.DONE,
                "result": result,
                "error": None,
                "finished_at": _now(),
            }
        )
        .eq("job_id", str(job_id))
        .execute()
        .data[0]
    )


def fail(job_id: UUID, error: str, retryable: bool = True) -> dict[str, Any]:
    """실패 처리. 재시도 여력이 남아 있으면 PENDING으로 되돌린다.

    ⚠️ `error`는 프론트에 그대로 노출된다.
       스택 트레이스·모델 경로·프롬프트 전문·API 키를 넣지 말 것.
    """
    client = get_client()
    rows = client.table("job").select("attempts").eq("job_id", str(job_id)).execute().data
    attempts = rows[0]["attempts"] if rows else settings.job_max_attempts

    requeue = retryable and attempts < settings.job_max_attempts
    patch: dict[str, Any] = {
        "status": JobStatus.PENDING if requeue else JobStatus.FAILED,
        "error": error[:2000],
    }
    if not requeue:
        patch["finished_at"] = _now()

    return client.table("job").update(patch).eq("job_id", str(job_id)).execute().data[0]


def cancel_open_for_photo(session_id: UUID, photo_id: UUID) -> int:
    """폐기된 사진을 가리키는 아직 안 끝난 잡을 종결한다. 종결한 개수 반환.

    ⚠️ 사진을 교체하면 **옛 잡은 없어진 photo_id 를 들고 그대로 남는다.**
       그대로 두면 워커가 집어 "대상 사진을 찾을 수 없습니다"로 실패하고,
       재시도 여력만큼(기본 3회) 반복한다. 세그는 GPU 작업이라 그냥 낭비다.
       화면에도 방금 올린 사진과 무관한 FAILED 가 하나 남는다.

    ⚠️ PROCESSING 인 잡도 같이 내린다. 이미 GPU 에서 돌고 있더라도, 그 결과는
       존재하지 않는 사진에 대한 것이라 어차피 저장되지 않는다.
    ⚠️ retryable=False 로 종결한다 — 사진이 없어진 것은 다시 시도해도 그대로다.
    """
    client = get_client()
    rows = (
        client.table("job")
        .select("job_id,payload")
        .eq("session_id", str(session_id))
        .in_("status", list(OPEN_STATUSES))
        .execute()
        .data
    )
    targets = [
        r["job_id"] for r in rows if str((r.get("payload") or {}).get("photo_id")) == str(photo_id)
    ]
    for job_id in targets:
        client.table("job").update(
            {
                "status": JobStatus.FAILED,
                "error": "사진이 교체되어 취소되었습니다.",
                "finished_at": _now(),
            }
        ).eq("job_id", job_id).execute()
    return len(targets)


# --------------------------------------------------------------------------- #
# 조회
# --------------------------------------------------------------------------- #


def get_job(job_id: UUID) -> dict[str, Any] | None:
    rows = get_client().table("job").select("*").eq("job_id", str(job_id)).execute().data
    return rows[0] if rows else None


def list_jobs(
    session_id: UUID,
    kind: JobKind | None = None,
    status: JobStatus | None = None,
) -> list[dict[str, Any]]:
    q = (
        get_client()
        .table("job")
        .select("job_id,kind,status,attempts,created_at")
        .eq("session_id", str(session_id))
    )
    if kind is not None:
        q = q.eq("kind", str(kind))
    if status is not None:
        q = q.eq("status", str(status))
    return q.order("created_at").execute().data


# ⚠️ all_settled(session_id, kind) 는 2026-08-14 에 지웠다.
#    "인바디처럼 있으면 쓰고 없으면 진행"할 때 쓰려고 미리 만들었는데 그 코드가
#    아직 없다. 필요해지면 list_jobs() + OPEN_STATUSES 로 다시 만들면 된다.
