"""팟 파이프라인 — 업로드 받은 두 장을 메모리에 두고 세그 → 부위 진단 → 종합 진단.

종전(Storage 경로)과의 관계
    세그·진단 **핸들러는 그대로** 쓴다 (app/worker/handlers/seg.py, vlm.py). 핸들러가
    사진을 photo_source.load() 로 읽으므로, 여기서 메모리에 등록만 하면 Storage 없이
    같은 코드가 돈다. 잡 행도 같은 테이블에 만든다 — 프론트의 진행률 폴링이 그대로다.

잡 소유권
    잡은 **처음부터 PROCESSING** 으로 만든다 (queue.open_processing). PENDING 으로
    두면 사진이 없는 다른 워커가 집어 실패한다. 부위 진단 핸들러가 등록하는
    VLM_OVERALL 만 PENDING 으로 생기므로, 곧바로 claim_job 으로 가져온다.

⚠️ 처리 스레드는 **1개**다. 세그(GPU)는 한 번에 하나만 돌 수 있고(VRAM), 그 뒤의
   GPT 호출도 같은 흐름에서 순서대로 한다. 동시 요청은 대기열(pod_queue_max)에 서고,
   넘치면 업로드 단계에서 503 으로 돌려보낸다 — 프록시 100초를 넘겨 끊기는 것보다 낫다.
   (스크리닝은 이 스레드가 아니라 업로드 요청 안에서 돌기 때문에 대기열과 무관하다)

⚠️ 사진은 처리가 끝나면 **반드시** photo_source.clear() 로 지운다. 실패해도 지운다.
   여기 남으면 "메모리에서 몇 초"라는 말이 거짓이 된다.
"""

from __future__ import annotations

import gc
import logging
import queue as _q
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from PIL import Image

from app.config import settings
from app.schemas.enums import JobKind, PhotoKind
from app.services import db, face_mask, images, photo_source
from app.worker import queue
from app.worker.run import _is_retryable, _user_message

log = logging.getLogger("pod.pipeline")

#: 팟이 만든 잡의 표식. 재시작 시 자기 것만 골라 정리한다 (fail_orphans).
SOURCE = "pod"
POD_KINDS: tuple[JobKind, ...] = (
    JobKind.SEG_REFERENCE,
    JobKind.SEG_USER,
    JobKind.VLM_PART,
    JobKind.VLM_OVERALL,
)

RESTART_MESSAGE = "처리 중 팟이 재시작되어 이 분석은 사라졌습니다. 사진을 다시 올려주세요."


# --------------------------------------------------------------------------- #
# 전처리 — 종전 API `_store` 와 같은 규칙 (거울 되돌리기 → 3:4 크롭 → 리사이즈 → JPEG)
# --------------------------------------------------------------------------- #


@dataclass
class Prepared:
    """저장용과 같은 규격의 가공본 + 프론트가 원본을 맞출 크롭 박스 + OpenAI 행 복사본."""

    jpeg: bytes  # 세그·저장 규격 가공본 (얼굴 그대로)
    width: int
    height: int
    crop_box: dict[str, Any]
    landmarks: list[dict[str, Any]] | None
    vlm_jpeg: bytes  # 얼굴을 가린 복사본 — GPT 세 번(스크리닝·부위·종합)은 이것만 본다
    face_box: tuple[int, int, int, int] | None  # 가공본 픽셀 좌표. None = 얼굴 점 없음


def prepare(photo: dict[str, Any], raw: bytes) -> Prepared:
    """업로드 원본 → 세그·진단이 받을 가공본.

    ⚠️ routes/photos.py `_store` 와 **같은 순서·같은 함수**다. 여기가 달라지면 팟 경로와
       Storage 경로의 세그 입력이 달라져 "결과 동일" 전제가 깨진다.
    ⚠️ crop_box 는 **되돌린(비반전) 원본** 픽셀 좌표다. 프론트는 기기 원본을 flipped 면
       먼저 좌우 반전하고, 그 다음 이 박스로 자른다.
    ⚠️ 얼굴 가림은 **여기서 한 번만** 한다 (services/face_mask). 세그는 안 가린 가공본,
       OpenAI 로 가는 세 호출은 가린 복사본. POD_FACE_MASK=false 는 실측(원본 vs 가림
       진단 비교) 전용 — 운영에서 끄면 OpenAI 에 얼굴이 나간다.
    """
    img = images.load_rgb(raw)
    flipped = bool(photo.get("was_mirrored"))
    if flipped:
        img = images.flip_horizontal(img)
    src_w, src_h = img.size

    landmarks = photo.get("pose_landmarks") or None
    box = images.model_aspect_box(img, landmarks)
    cropped, recentered = images.fit_to_model_aspect(img, landmarks)
    jpeg, width, height = images.encode_photo(cropped)

    if box is None:
        box = (0, 0, src_w, src_h)
    crop_box = {
        "x": box[0],
        "y": box[1],
        "w": box[2] - box[0],
        "h": box[3] - box[1],
        "source_width": src_w,
        "source_height": src_h,
        "flipped": flipped,
    }
    face_box = None
    vlm_jpeg = jpeg
    if settings.pod_face_mask:
        masked, face_box = face_mask.apply(_decode(jpeg), recentered)
        if face_box is None:
            log.info("[%s] 얼굴 점이 안 보여 가리지 않습니다 (뒷모습·프레임 밖)", photo.get("kind"))
        else:
            vlm_jpeg, _, _ = images.encode_photo(masked)
    return Prepared(
        jpeg=jpeg,
        width=width,
        height=height,
        crop_box=crop_box,
        landmarks=recentered,
        vlm_jpeg=vlm_jpeg,
        face_box=face_box,
    )


def _decode(jpeg: bytes) -> Image.Image:
    import io

    return Image.open(io.BytesIO(jpeg)).convert("RGB")


def screening_image(p: Prepared) -> Image.Image:
    """스크리닝(GPT)에 넘길 PIL 이미지 — **얼굴을 가린 복사본** 기준."""
    return _decode(p.vlm_jpeg)


# --------------------------------------------------------------------------- #
# 대기열
# --------------------------------------------------------------------------- #


@dataclass
class Task:
    session_id: str
    mode: str  # full | quick
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)  # kind → job row
    enqueued_at: float = field(default_factory=time.monotonic)


class PodBusy(RuntimeError):
    """대기열이 가득 찼다. 업로드 단계에서 503 으로 바꾼다."""


_tasks: _q.Queue[Task] = _q.Queue(maxsize=max(1, settings.pod_queue_max))
_worker: threading.Thread | None = None
_stop = threading.Event()
_state = {"running": None, "done": 0, "failed": 0}


def queued() -> int:
    return _tasks.qsize()


def status() -> dict[str, Any]:
    return {
        "queued": queued(),
        "queue_max": settings.pod_queue_max,
        "running": _state["running"],
        "done": _state["done"],
        "failed": _state["failed"],
        "held_photos": photo_source.held(),
        "face_mask": bool(settings.pod_face_mask),
        "worker_alive": bool(_worker and _worker.is_alive()),
    }


def submit(
    session: dict[str, Any],
    photos: dict[str, dict[str, Any]],
    prepared: dict[str, Prepared],
    mode: str,
) -> dict[str, str]:
    """가공본을 메모리에 등록하고 잡을 만들어 대기열에 넣는다. 반환: kind → job_id.

    ⚠️ 순서: 자리 확인 → 사진 등록 → 행 갱신 → 잡 생성 → 대기열. 대기열이 찼으면
       아무것도 만들지 않고 PodBusy — 잡만 남고 처리는 안 되는 상태를 만들지 않는다.
    """
    if _tasks.full():
        raise PodBusy()

    session_id = str(session["session_id"])
    for kind, p in prepared.items():
        photo_source.put(session_id, kind, p.jpeg, vlm=p.vlm_jpeg)
        patch: dict[str, Any] = {"width": p.width, "height": p.height, "crop_box": p.crop_box}
        if p.landmarks is not None:
            patch["pose_landmarks"] = p.landmarks
        # ⚠️ crop_box 컬럼이 없으면(마이그레이션 미적용) 여기서 그대로 500 이다 — 일부러
        #    폴백을 두지 않는다. crop_box 없이 저장되면 업로드는 성공처럼 보이는데 화면만
        #    어긋나서 더 찾기 어렵다. pod 모드는 2026-09-09_photo_pod_pipeline.sql 이 전제다.
        db.update_photo(UUID(str(photos[kind]["photo_id"])), patch)

    task = Task(session_id=session_id, mode=mode)
    sid = UUID(session_id)
    try:
        if mode != "quick":
            for kind, job_kind in (
                (PhotoKind.REFERENCE, JobKind.SEG_REFERENCE),
                (PhotoKind.USER, JobKind.SEG_USER),
            ):
                task.jobs[str(job_kind)] = queue.open_processing(
                    sid, job_kind, {"photo_id": photos[str(kind)]["photo_id"], "source": SOURCE}
                )
        part_payload: dict[str, Any] = {"source": SOURCE}
        if mode == "quick":
            part_payload["mode"] = "quick"
        task.jobs[str(JobKind.VLM_PART)] = queue.open_processing(
            sid, JobKind.VLM_PART, part_payload
        )
    except Exception:
        photo_source.clear(session_id)
        _fail_all(task, "분석 준비 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        raise

    try:
        _tasks.put_nowait(task)
    except _q.Full:
        photo_source.clear(session_id)
        _fail_all(task, "지금은 분석 요청이 많아요. 잠시 후 다시 시도해주세요.")
        raise PodBusy() from None

    return {kind: str(job["job_id"]) for kind, job in task.jobs.items()}


def _fail_all(task: Task, message: str) -> None:
    for job in task.jobs.values():
        try:
            queue.fail(UUID(str(job["job_id"])), message, retryable=False)
        except Exception:  # noqa: BLE001
            log.exception("[%s] 실패 기록 실패", job.get("job_id"))


# --------------------------------------------------------------------------- #
# 처리 스레드
# --------------------------------------------------------------------------- #


def _run_job(job: dict[str, Any], handler, max_attempts: int = 2) -> dict[str, Any] | None:
    """핸들러를 돌리고 잡을 종결한다. 성공하면 result, 실패하면 None.

    ⚠️ 재시도는 **팟 안에서만** 한다. queue.fail(retryable=True) 로 PENDING 에 되돌리면
       사진 없는 다른 워커가 집어간다. 일시 오류는 여기서 한 번 더 돌리고, 그래도
       안 되면 FAILED 로 종결한다 (retryable=False).
    """
    job_id = UUID(str(job["job_id"]))
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            result = handler(job)
            queue.complete(job_id, result)
            log.info("[%s] %s 완료 (%.1fs)", job_id, job["kind"], time.perf_counter() - started)
            return result or {}
        except Exception as e:  # noqa: BLE001
            last = e
            log.exception("[%s] %s 실패 (시도 %d)", job_id, job["kind"], attempt)
            if not _is_retryable(e):
                break
    try:
        queue.fail(job_id, _user_message(last) if last else "처리 실패", retryable=False)
    except Exception:  # noqa: BLE001
        log.exception("[%s] 실패 기록마저 실패", job_id)
    return None


def _process(task: Task) -> None:
    from app.worker.handlers import seg, vlm  # 지연 import — torch 는 여기서만

    sid = task.session_id
    _state["running"] = sid
    log.info(
        "[%s] 처리 시작 — mode=%s 대기 %.1fs", sid, task.mode, time.monotonic() - task.enqueued_at
    )

    ok = True
    if task.mode != "quick":
        for kind in (str(JobKind.SEG_REFERENCE), str(JobKind.SEG_USER)):
            if _run_job(task.jobs[kind], seg._handle) is None:
                ok = False
                break

    part_job = task.jobs[str(JobKind.VLM_PART)]
    if not ok:
        try:
            queue.fail(
                UUID(str(part_job["job_id"])),
                "사진 분석(세그멘테이션)에 실패해 진단을 진행하지 못했습니다. 다시 올려주세요.",
                retryable=False,
            )
        except Exception:  # noqa: BLE001
            log.exception("[%s] VLM_PART 실패 기록 실패", sid)
        return

    part_result = _run_job(part_job, vlm._diagnose_parts)
    if part_result is None:
        return

    # 부위 진단 핸들러가 PENDING 으로 등록한 종합 잡을 **다른 워커보다 먼저** 가져온다.
    overall_id = part_result.get("overall_job_id")
    if not overall_id:
        log.warning("[%s] 종합 진단 잡 id 가 없습니다 — 건너뜁니다", sid)
        return
    overall = queue.claim_job(UUID(str(overall_id)), payload={"source": SOURCE})
    if overall is None:
        log.warning("[%s] 종합 진단 잡 %s 을 다른 워커가 가져갔습니다", sid, overall_id)
        return
    _run_job(overall, vlm._diagnose_overall)


def _loop() -> None:
    log.info("처리 스레드 기동 — 대기열 상한 %d", settings.pod_queue_max)
    while not _stop.is_set():
        try:
            task = _tasks.get(timeout=0.5)
        except _q.Empty:
            continue
        try:
            _process(task)
            _state["done"] += 1
        except Exception:  # noqa: BLE001 — 한 세션 때문에 스레드가 죽으면 안 된다
            _state["failed"] += 1
            log.exception("[%s] 파이프라인 예외", task.session_id)
            _fail_all(task, "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        finally:
            # ⚠️ 사진은 성공·실패와 무관하게 여기서 사라진다.
            cleared = photo_source.clear(task.session_id)
            _state["running"] = None
            gc.collect()
            log.info(
                "[%s] 메모리 사진 %d장 해제 (남은 사진 %d)",
                task.session_id,
                cleared,
                photo_source.held(),
            )
            _tasks.task_done()
    log.info("처리 스레드 종료")


def start() -> None:
    """기동: 팟이 남긴 잡 정리 → 처리 스레드."""
    global _worker
    orphaned = queue.fail_orphans(POD_KINDS, {"source": SOURCE}, RESTART_MESSAGE)
    if orphaned:
        log.warning(
            "재시작 전 남은 잡 %d개를 FAILED 로 정리했습니다 (사진이 메모리에만 있었음)", orphaned
        )
    _stop.clear()
    _worker = threading.Thread(target=_loop, name="pod-pipeline", daemon=True)
    _worker.start()


def stop() -> None:
    _stop.set()
