"""잡 구간별 소요 시간 실시간 측정 — e2e 테스트를 돌리면서 옆 터미널에 띄워둔다.

    python scripts/watch_job_timing.py

스크립트 기동 **이후** 생성된 잡만 지켜본다. e2e-test.html 로 전 구간을 돌리면
잡이 끝날 때마다 한 줄씩 찍히고, Ctrl+C 로 끝내면 요약표가 나온다.

무엇을 재나 (job 테이블의 타임스탬프로 계산)
    대기   started_at − created_at    큐에 들어가서 워커가 집을 때까지
    처리   finished_at − started_at   워커가 실제로 일한 시간
    추론   result.inference_ms        세그 잡만 — 처리 시간 중 순수 GPU 추론

⚠️ e2e 페이지 로그의 "DONE (NNs)" 와 숫자가 조금 다른 게 정상이다.
   그쪽은 페이지가 폴링을 시작한 시점부터의 경과라 폴링 간격(1.5s)이 섞인다.
   여기 값이 서버 기준의 실측이다.

⚠️ 업로드 요청 자체(2차 VLM 스크리닝 2~5초 포함)는 잡이 아니라서 여기 안 나온다.
   그건 브라우저 F12 → Network 탭에서 POST /photos/... 의 시간으로 본다.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.db import get_client  # noqa: E402

POLL_SEC = 1.0


def _ts(value: str | None) -> datetime | None:
    """Supabase 의 ISO 문자열 → datetime. 'Z' 접미도 처리한다."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "     -"
    return f"{seconds:6.1f}s"


def main() -> int:
    client = get_client()
    started = datetime.now(timezone.utc)
    print(f"잡 감시 시작 — {started:%H:%M:%S} UTC 이후 생성분만 봅니다. Ctrl+C 로 요약.\n")
    print(f"{'구간':<14} {'대기':>7} {'처리':>7} {'추론':>8}  비고")
    print("-" * 58)

    seen_status: dict[str, str] = {}  # job_id -> 마지막으로 본 status
    finished_rows: list[dict] = []

    try:
        while True:
            rows = (
                client.table("job")
                .select("job_id,kind,status,result,error,created_at,started_at,finished_at")
                .gte("created_at", started.isoformat())
                .order("created_at")
                .execute()
                .data
            )
            for row in rows:
                jid = str(row["job_id"])
                status = row["status"]
                if seen_status.get(jid) == status:
                    continue
                seen_status[jid] = status

                if status == "PROCESSING":
                    print(f"{row['kind']:<14} {'':>7} {'':>7} {'':>8}  … 처리 중")
                elif status in ("DONE", "FAILED"):
                    c, s, f = _ts(row["created_at"]), _ts(row["started_at"]), _ts(row["finished_at"])
                    wait = (s - c).total_seconds() if (s and c) else None
                    work = (f - s).total_seconds() if (f and s) else None
                    infer_ms = (row.get("result") or {}).get("inference_ms")
                    infer = f"{infer_ms / 1000:6.2f}s" if infer_ms is not None else "      -"
                    note = "✗ " + (row.get("error") or "")[:40] if status == "FAILED" else "✓"
                    print(f"{row['kind']:<14} {_fmt(wait)} {_fmt(work)} {infer}  {note}")
                    finished_rows.append(
                        {"kind": row["kind"], "wait": wait, "work": work, "ok": status == "DONE"}
                    )
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        pass

    if finished_rows:
        total_wait = sum(r["wait"] or 0 for r in finished_rows)
        total_work = sum(r["work"] or 0 for r in finished_rows)
        print("\n요약")
        print(f"  잡 {len(finished_rows)}건 — 대기 합계 {total_wait:.1f}s · 처리 합계 {total_work:.1f}s")
        slowest = max(finished_rows, key=lambda r: r["work"] or 0)
        print(f"  가장 오래 걸린 구간: {slowest['kind']} ({slowest['work']:.1f}s)")
        failed = [r["kind"] for r in finished_rows if not r["ok"]]
        if failed:
            print(f"  실패: {', '.join(failed)}")
    else:
        print("\n감시 중 끝난 잡이 없습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
