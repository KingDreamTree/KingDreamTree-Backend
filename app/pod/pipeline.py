"""팟 파이프라인 — 업로드 받은 두 장을 메모리에 두고 세그 → 부위 진단 → 종합 진단.

종전(Storage 경로)과의 관계
    세그·진단 **핸들러는 그대로** 쓴다 (app/worker/handlers/seg.py, vlm.py). 핸들러가
    사진을 photo_source.load() 로 읽으므로, 여기서 메모리에 등록만 하면 Storage 없이
    같은 코드가 돈다. 잡 행도 같은 테이블에 만든다 — 프론트의 진행률 폴링이 그대로다.

잡 소유권
    잡은 **처음부터 PROCESSING** 으로 만든다 (queue.open_processing). PENDING 으로
    두면 사진이 없는 다른 워커가 집어 실패한다. 부위 진단 핸들러가 등록하는
    VLM_OVERALL 만 PENDING 으로 생기므로, 곧바로 claim_job 으로 가져온다.
    payload 에 `source=pod` 와 **이 팟의 id**(INSTANCE_ID)를 박는다 — 재시작 정리
    (fail_orphans)가 자기 잡만 건드리게. 스테이징 팟이 같은 DB 를 봐도 프로덕션 팟의
    처리 중 잡을 죽이지 않는다 (2026-09-09 검사에서 잡힘).

⚠️ 처리 스레드는 **1개**다. 세그(GPU)는 한 번에 하나만 돌 수 있고(VRAM), 그 뒤의
   GPT 호출도 같은 흐름에서 순서대로 한다. 동시 요청은 대기열(pod_queue_max)에 서고,
   넘치면 업로드 단계에서 503 으로 돌려보낸다 — 프록시 100초를 넘겨 끊기는 것보다 낫다.
   (스크리닝은 이 스레드가 아니라 업로드 요청 안에서 돌기 때문에 대기열과 무관하다)

⚠️ 같은 세션은 한 번에 하나만 (_active). 메모리 키가 (session, kind) 라 두 번째
   업로드가 첫 번째를 덮고, 첫 처리가 끝나며 세션 전체를 지워 두 번째가 통째로
   실패한다 — 연타·이중 전송은 업로드 단계에서 409 로 막는다.

⚠️ 사진은 처리가 끝나면 **반드시** photo_source.clear() 로 지운다. 실패해도 지운다.
   등록(submit) 도중 실패해도 지운다. 여기 남으면 "메모리에서 몇 초"라는 말이 거짓이 된다.
"""

from __future__ import annotations

import gc
import logging
import os
import queue as _q
import socket
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
#: 이 팟의 식별자 — 재시작해도 같아야 한다 (자기 잔여 잡을 찾는 열쇠).
#  RunPod 은 팟마다 RUNPOD_POD_ID 를 준다. 없으면 POD_INSTANCE_ID, 그것도 없으면 호스트명.
INSTANCE_ID = settings.pod_instance_id or os.environ.get("RUNPOD_POD_ID") or socket.gethostname()
POD_KINDS: tuple[JobKind, ...] = (
    JobKind.SEG_REFERENCE,
    JobKind.SEG_USER,
    JobKind.VLM_PART,
    JobKind.VLM_OVERALL,
)

RESTART_MESSAGE = "처리 중 팟이 재시작되어 이 분석은 사라졌습니다. 사진을 다시 올려주세요."


def _payload(**extra: Any) -> dict[str, Any]:
    return {"source": SOURCE, "pod": INSTANCE_ID, **extra}


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
    landmarks: list[dict[str, Any]] | None  # 가공본 기준으로 재정규화된 것 — DB 에는 안 쓴다
    vlm_jpeg: bytes  # 얼굴을 가린 복사본 — GPT 세 번(스크리닝·부위·종합)은 이것만 본다
    face_box: tuple[int, int, int, int] | None  # 가공본 픽셀 좌표. None = 얼굴 점 없음
    face_points: int = 0  # 박스를 잡는 데 쓰인 얼굴 점 수 (11 이 정상, 7 미만이면 보수 박스)


def prepare(photo: dict[str, Any], raw: bytes) -> Prepared:
    """업로드 원본 → 세그·진단이 받을 가공본.

    ⚠️ routes/photos.py `_store` 와 **같은 순서·같은 함수**다. 여기가 달라지면 팟 경로와
       Storage 경로의 세그 입력이 달라져 "결과 동일" 전제가 깨진다.
    ⚠️ crop_box 는 **되돌린(비반전) 원본** 픽셀 좌표다. 프론트는 기기 원본을 flipped 면
       먼저 좌우 반전하고, 그 다음 이 박스로 자른다.
    ⚠️ photo["pose_landmarks"] 는 **원본(비반전) 기준**이어야 한다. 재정규화된 값을 DB 에
       되쓰지 않는 이유가 이것이다 — 되쓰면 재업로드 때 크롭 기준 좌표를 원본 기준으로
       착각해 크롭·얼굴 박스가 밀린다 (실측 123px·37px, 2026-09-09 검사).
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
    face_points = 0
    vlm_jpeg = jpeg
    if settings.pod_face_mask:
        decoded = _decode(jpeg)
        face_points = len(face_mask.visible_face_points(recentered, decoded.size))
        masked, face_box = face_mask.apply(decoded, recentered, settings.pod_face_mask_style)
        if face_box is None:
            log.info("[%s] 얼굴 점이 안 보여 가리지 않습니다 (뒷모습·프레임 밖)", photo.get("kind"))
        else:
            if face_points < face_mask.FULL_FACE_POINTS:
                log.info(
                    "[%s] 얼굴 점 %d/11 — 어깨 폭 기준 보수 박스로 가림 %s",
                    photo.get("kind"),
                    face_points,
                    face_box,
                )
            vlm_jpeg, _, _ = images.encode_photo(masked)
    return Prepared(
        jpeg=jpeg,
        width=width,
        height=height,
        crop_box=crop_box,
        landmarks=recentered,
        vlm_jpeg=vlm_jpeg,
        face_box=face_box,
        face_points=face_points,
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
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)  # kind → 아직 안 끝난 잡
    enqueued_at: float = field(default_factory=time.monotonic)


class PodBusy(RuntimeError):
    """대기열이 가득 찼거나 처리 스레드가 없다. 업로드 단계에서 503 으로 바꾼다."""


class AlreadyProcessing(RuntimeError):
    """같은 세션이 이미 대기·처리 중이다. 업로드 단계에서 409 로 바꾼다."""


_tasks: _q.Queue[Task] = _q.Queue(maxsize=max(1, settings.pod_queue_max))
_worker: threading.Thread | None = None
_stop = threading.Event()
_state = {"running": None, "done": 0, "failed": 0}
#: 대기 중 + 처리 중인 세션. submit 에서 넣고 _loop 의 finally 에서 뺀다.
_active: set[str] = set()
_active_lock = threading.Lock()


def queued() -> int:
    return _tasks.qsize()


def worker_alive() -> bool:
    return bool(_worker and _worker.is_alive())


def is_active(session_id: str) -> bool:
    with _active_lock:
        return str(session_id) in _active


def _activate(session_id: str) -> bool:
    """세션을 진행 중으로 표시한다. 이미 있으면 False."""
    with _active_lock:
        if session_id in _active:
            return False
        _active.add(session_id)
        return True


def _deactivate(session_id: str) -> None:
    with _active_lock:
        _active.discard(session_id)


def status() -> dict[str, Any]:
    return {
        "queued": queued(),
        "queue_max": settings.pod_queue_max,
        "running": _state["running"] is not None,  # 세션 id 는 밖에 내지 않는다
        "done": _state["done"],
        "failed": _state["failed"],
        "held_photos": photo_source.held(),
        "face_mask": bool(settings.pod_face_mask),
        "worker_alive": worker_alive(),
        "instance": INSTANCE_ID,
    }


def submit(
    session: dict[str, Any],
    photos: dict[str, dict[str, Any]],
    prepared: dict[str, Prepared],
    mode: str,
) -> dict[str, str]:
    """가공본을 메모리에 등록하고 잡을 만들어 대기열에 넣는다. 반환: kind → job_id.

    ⚠️ 순서: 스레드 살아있나 → 자리 확인 → 세션 중복 확인 → 사진 등록 → 행 갱신 →
       잡 생성 → 대기열. 어느 단계에서 실패해도 **사진은 메모리에서 지운다** — 행 갱신이
       try 밖에 있어 실패 시 사진이 영구히 남던 구멍(2026-09-09 검사)을 막는다.
    """
    if not worker_alive():
        raise PodBusy()
    if _tasks.full():
        raise PodBusy()

    session_id = str(session["session_id"])
    if not _activate(session_id):
        raise AlreadyProcessing()

    task = Task(session_id=session_id, mode=mode)
    sid = UUID(session_id)
    try:
        for kind, p in prepared.items():
            photo_source.put(session_id, kind, p.jpeg, vlm=p.vlm_jpeg)
            # ⚠️ pose_landmarks 는 건드리지 않는다 (prepare 주석). crop_box 컬럼이 없으면
            #    (마이그레이션 미적용) 여기서 그대로 예외 → 500 이다. 일부러 폴백을 두지
            #    않는다: crop_box 없이 저장되면 업로드는 성공처럼 보이는데 화면만 어긋난다.
            db.update_photo(
                UUID(str(photos[kind]["photo_id"])),
                {"width": p.width, "height": p.height, "crop_box": p.crop_box},
            )
        if mode != "quick":
            for kind, job_kind in (
                (PhotoKind.REFERENCE, JobKind.SEG_REFERENCE),
                (PhotoKind.USER, JobKind.SEG_USER),
            ):
                task.jobs[str(job_kind)] = queue.open_processing(
                    sid, job_kind, _payload(photo_id=photos[str(kind)]["photo_id"])
                )
        part_payload = _payload(mode="quick") if mode == "quick" else _payload()
        task.jobs[str(JobKind.VLM_PART)] = queue.open_processing(
            sid, JobKind.VLM_PART, part_payload
        )
        _tasks.put_nowait(task)
    except _q.Full:
        _abort(task, "지금은 분석 요청이 많아요. 잠시 후 다시 시도해주세요.")
        raise PodBusy() from None
    except Exception:
        _abort(task, "분석 준비 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        raise

    return {kind: str(job["job_id"]) for kind, job in task.jobs.items()}


def _abort(task: Task, message: str) -> None:
    """등록 실패 — 사진 해제, 만든 잡 FAILED, 세션 표시 해제."""
    photo_source.clear(task.session_id)
    _fail_all(task, message)
    _deactivate(task.session_id)


def _fail_all(task: Task, message: str) -> None:
    """아직 끝나지 않은 잡(task.jobs 에 남은 것)만 FAILED 로. 끝난 잡은 이미 빠져 있다."""
    for kind, job in list(task.jobs.items()):
        try:
            queue.fail(UUID(str(job["job_id"])), message, retryable=False)
        except Exception:  # noqa: BLE001
            log.exception("[%s] 실패 기록 실패", job.get("job_id"))
        task.jobs.pop(kind, None)


# --------------------------------------------------------------------------- #
# 처리 스레드
# --------------------------------------------------------------------------- #


def _run_job(task: Task, kind: str, handler, max_attempts: int = 2) -> dict[str, Any] | None:
    """핸들러를 돌리고 잡을 종결한다. 성공하면 result, 실패하면 None.

    어느 쪽이든 잡은 task.jobs 에서 빠진다 — 뒤의 예상 못 한 예외가 _fail_all 로
    **이미 DONE 인 잡을 FAILED 로 되돌리지** 않게 (2026-09-09 검사).

    ⚠️ 재시도는 **팟 안에서만** 한다. queue.fail(retryable=True) 로 PENDING 에 되돌리면
       사진 없는 다른 워커가 집어간다. 일시 오류는 여기서 한 번 더 돌리고, 그래도
       안 되면 FAILED 로 종결한다 (retryable=False).
    """
    job = task.jobs.pop(kind)
    job_id = UUID(str(job["job_id"]))
    # 대기열에서 기다린 시간은 처리 시간이 아니다 — started_at 을 지금으로 (stalled 오탐 방지).
    try:
        queue.mark_started(job_id)
    except Exception:  # noqa: BLE001
        log.exception("[%s] started_at 갱신 실패 (계속 진행)", job_id)
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
            if _run_job(task, kind, seg._handle) is None:
                ok = False
                break

    if not ok:
        _fail_all(
            task, "사진 분석(세그멘테이션)에 실패해 진단을 진행하지 못했습니다. 다시 올려주세요."
        )
        return

    part_result = _run_job(task, str(JobKind.VLM_PART), vlm._diagnose_parts)
    if part_result is None:
        return

    # 부위 진단 핸들러가 PENDING 으로 등록한 종합 잡을 **다른 워커보다 먼저** 가져온다.
    overall_id = part_result.get("overall_job_id")
    if not overall_id:
        log.warning("[%s] 종합 진단 잡 id 가 없습니다 — 건너뜁니다", sid)
        return
    overall = queue.claim_job(UUID(str(overall_id)), payload=_payload())
    if overall is None:
        log.warning("[%s] 종합 진단 잡 %s 을 다른 워커가 가져갔습니다", sid, overall_id)
        return
    task.jobs[str(JobKind.VLM_OVERALL)] = overall
    _run_job(task, str(JobKind.VLM_OVERALL), vlm._diagnose_overall)


def _loop() -> None:
    log.info("처리 스레드 기동 — 대기열 상한 %d, 팟 id %s", settings.pod_queue_max, INSTANCE_ID)
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
            _deactivate(task.session_id)
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
    """기동: 이 팟이 남긴 잡 정리 → 처리 스레드."""
    global _worker
    orphaned = queue.fail_orphans(POD_KINDS, _payload(), RESTART_MESSAGE)
    if orphaned:
        log.warning(
            "재시작 전 남은 잡 %d개를 FAILED 로 정리했습니다 (사진이 메모리에만 있었음)", orphaned
        )
    _stop.clear()
    _worker = threading.Thread(target=_loop, name="pod-pipeline", daemon=True)
    _worker.start()


def stop() -> None:
    _stop.set()
