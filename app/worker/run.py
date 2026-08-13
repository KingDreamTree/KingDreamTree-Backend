"""워커 진입점.

처리할 잡 종류를 인자로 받는다. **어느 machine에서 돌든 상관없다** —
워커는 Supabase(job 테이블 + Storage)하고만 대화한다.

    # RunPod GPU 팟 — 세그멘테이션만
    python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER

    # EC2 — LLM 계열만 (GPU 불필요)
    python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH

    # 로컬 개발 — 전부
    python -m app.worker.run --all

⚠️ 세그 워커 동시성은 1이다. 모델을 프로세스마다 하나씩 들고 있으므로
   여러 개 띄우면 VRAM/RAM이 그만큼 곱해진다.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from app.config import settings
from app.schemas.enums import LLM_KINDS, SEG_KINDS, JobKind
from app.worker import queue

# ⚠️ 핸들러 등록소는 app/worker/registry.py 에 있다. 여기 두면 안 된다 —
#    `python -m app.worker.run` 은 이 파일을 __main__ 으로 로드하는데,
#    핸들러가 `from app.worker.run import register` 를 하면 같은 파일이
#    app.worker.run 이라는 **별개 모듈로 한 번 더** 로드된다. 그러면 등록이
#    사본 쪽 딕셔너리로 들어가고 __main__ 쪽은 빈 채로 남는다.
#    (재수출은 유지한다 — 기존 `from app.worker.run import register` 도 이제
#     같은 딕셔너리를 가리키므로 안전하다)
from app.worker.registry import HANDLERS, register  # noqa: F401

log = logging.getLogger("worker")


def _load_handlers(kinds: list[JobKind]) -> None:
    """필요한 핸들러만 import 한다.

    ⚠️ 세그 핸들러를 import하면 torch/transformers가 딸려 온다.
       LLM 워커에서까지 그걸 로드할 이유가 없으므로 필요할 때만 가져온다.
    """
    if any(k in SEG_KINDS for k in kinds):
        from app.worker.handlers import seg  # noqa: F401

    if any(k in LLM_KINDS for k in kinds):
        try:
            from app.worker.handlers import ocr, routine, vlm  # noqa: F401
        except ImportError as e:
            log.warning("LLM 핸들러 일부가 아직 없습니다 (담당 B 작업): %s", e)


_stop = False


def _handle_signal(signum, frame) -> None:  # noqa: ANN001
    global _stop
    log.info("종료 신호 수신 — 진행 중인 잡을 마치고 멈춥니다")
    _stop = True


def _reclaim(kinds: list[JobKind]) -> None:
    """죽은 워커가 남긴 PROCESSING 잡을 되살린다.

    ⚠️ 자기가 처리하는 kind 만 본다. 남의 kind 를 건드리면 멀쩡히 돌고 있는
       다른 워커의 잡을 빼앗는다.
    """
    try:
        reclaimed = queue.reclaim_stale(kinds)
    except Exception:  # noqa: BLE001 — 회수 실패로 워커가 죽으면 안 된다
        log.exception("좀비 잡 회수 실패 — 계속 진행합니다")
        return

    for job in reclaimed:
        log.warning(
            "[%s] %s 좀비 회수 → %s (시도 %d)",
            job["job_id"],
            job["kind"],
            job["status"],
            job["attempts"],
        )


def run(kinds: list[JobKind], poll_interval: float) -> int:
    _load_handlers(kinds)

    missing = [k for k in kinds if k not in HANDLERS]
    if missing:
        log.error("핸들러가 없는 kind: %s", ", ".join(missing))
        return 1

    log.info(
        "워커 기동 — kinds=%s poll=%.1fs 좀비판정=%ds",
        ",".join(kinds),
        poll_interval,
        settings.job_stale_after_sec,
    )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ⚠️ 기동 직후 한 번 — 팟을 껐다 켜는 게 좀비가 생기는 제일 흔한 경로다.
    #    여기서 바로 회수해야 사용자가 로딩 화면에서 무한정 기다리지 않는다.
    _reclaim(kinds)
    last_reclaim = time.monotonic()

    idle_logged = False
    while not _stop:
        if time.monotonic() - last_reclaim >= settings.job_reclaim_interval_sec:
            _reclaim(kinds)
            last_reclaim = time.monotonic()

        job = queue.claim(kinds)
        if job is None:
            if not idle_logged:
                log.debug("대기 중")
                idle_logged = True
            time.sleep(poll_interval)
            continue

        idle_logged = False
        job_id, kind = job["job_id"], job["kind"]
        log.info("[%s] %s 시작 (시도 %d)", job_id, kind, job["attempts"])

        started = time.perf_counter()
        try:
            result = HANDLERS[JobKind(kind)](job)
            queue.complete(job_id, result)
            log.info("[%s] 완료 (%.1fs)", job_id, time.perf_counter() - started)
        except Exception as e:  # noqa: BLE001
            # ⚠️ job.error 는 프론트에 그대로 노출된다.
            #    스택 트레이스·모델 경로·API 키가 섞이면 안 되므로 사람이 읽을
            #    한 줄만 넣고, 상세는 서버 로그로만 남긴다.
            log.exception("[%s] 실패", job_id)
            queue.fail(job_id, _user_message(e))

    log.info("워커 종료")
    return 0


def _user_message(e: Exception) -> str:
    """사용자에게 보여줘도 되는 문구로 축약."""
    from app.services.sapiens_labels import LabelsNotVerifiedError

    if isinstance(e, LabelsNotVerifiedError):
        return "서버 설정 오류입니다. 담당자에게 문의해주세요."
    if isinstance(e, FileNotFoundError):
        return "서버에 모델이 준비되지 않았습니다. 잠시 후 다시 시도해주세요."
    if isinstance(e, MemoryError):
        return "서버 자원이 부족합니다. 잠시 후 다시 시도해주세요."
    text = str(e).strip().splitlines()[0] if str(e).strip() else e.__class__.__name__
    return text[:200]


def main() -> int:
    ap = argparse.ArgumentParser(description="잡 워커")
    ap.add_argument("--kinds", help="쉼표로 구분한 JobKind 목록")
    ap.add_argument("--all", action="store_true", help="모든 kind 처리 (로컬 개발용)")
    ap.add_argument("--poll", type=float, default=None, help="폴링 주기(초)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if args.all:
        kinds = list(JobKind)
    elif args.kinds:
        try:
            kinds = [JobKind(k.strip()) for k in args.kinds.split(",") if k.strip()]
        except ValueError as e:
            print(f"[X] 잘못된 kind: {e}")
            print(f"    가능한 값: {', '.join(JobKind)}")
            return 1
    else:
        print("[X] --kinds 또는 --all 이 필요합니다.")
        return 1

    return run(kinds, args.poll or settings.worker_poll_interval_sec)


if __name__ == "__main__":
    sys.exit(main())
