"""잡 큐 — 등록 / 원자적 선점 / 완료 / 실패.

담당 A(SEG_*)와 담당 B(OCR/VLM/ROUTINE)가 함께 쓰는 공유 모듈이다.
핸들러는 app/worker/handlers/ 아래에 각자 추가하고, 여기는 건드리지 않는다.
"""

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from app.config import settings
from app.schemas.enums import JobKind, JobStatus
from app.services.db import get_client

#: 아직 끝나지 않은 잡 (중복 등록 판정용)
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


def find_open(
    session_id: UUID,
    kind: JobKind,
    payload_match: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """이 세션에 아직 끝나지 않은 같은 종류의 잡이 있는지 찾는다."""
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


def all_settled(session_id: UUID, kind: JobKind) -> bool:
    """해당 종류의 잡이 전부 종결(DONE/FAILED)됐는지.

    인바디처럼 "있으면 쓰고 없으면 그냥 진행"할 때 대기 여부 판정에 쓴다.
    """
    jobs = list_jobs(session_id, kind=kind)
    return not jobs or all(j["status"] not in OPEN_STATUSES for j in jobs)
