"""사진 원본을 어디서 읽을지 한 곳에서 정한다 — 메모리(팟) 또는 Storage(종전).

    photo_source.load(photo_row) -> bytes

  * PHOTO_PIPELINE=pod  : 팟이 업로드 받은 사진을 `put()` 으로 메모리에 등록해 두고,
                          세그·진단 핸들러는 그걸 읽는다. Storage 에는 사진이 없다.
  * PHOTO_PIPELINE=storage : 등록된 게 없으면 종전대로 Storage 에서 내려받는다.

⚠️ 핸들러(worker/handlers/seg.py, vlm.py)는 이 함수만 부른다. `storage.download` 를
   직접 부르는 사진 읽기 경로를 남기면 팟 경로에서 storage_path 가 NULL 이라 터진다 —
   그 경로가 사진 저장을 강제하는 마지막 한 줄이 된다.

⚠️ 메모리에 있는 것은 **저장용과 같은 가공본**(거울 되돌림·3:4 크롭·리사이즈·JPEG)이다.
   종전 경로에서 Storage 에 있던 파일과 같은 규격이라, 읽는 쪽은 어디서 왔는지
   구분할 필요가 없다.

⚠️ 팟 경로는 사진마다 **두 벌**을 둔다 — 세그용 가공본과, 얼굴을 가린 OpenAI 행
   복사본(services/face_mask). GPT 를 부르는 쪽은 `load(photo, for_vlm=True)` 로 읽는다.
   가린 복사본이 없으면(종전 경로) 가공본을 그대로 준다 — 종전 경로는 기기에서
   가려 올리지 않으므로 얼굴이 그대로 나간다. 그게 pod 로 옮긴 이유 중 하나다.

⚠️ 등록·해제는 팟 파이프라인(app/pod/pipeline.py)만 한다. 처리가 끝나면 반드시
   `clear(session_id)` — 여기 남아 있으면 "메모리에서 몇 초"라는 말이 거짓이 된다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.services import storage

log = logging.getLogger("services.photo_source")

_lock = threading.Lock()
#: (session_id, kind) → JPEG bytes (세그·저장 규격 가공본)
_mem: dict[tuple[str, str], bytes] = {}
#: (session_id, kind) → JPEG bytes (얼굴을 가린 OpenAI 행 복사본)
_vlm: dict[tuple[str, str], bytes] = {}


def put(session_id: str, kind: str, data: bytes, vlm: bytes | None = None) -> None:
    """가공본을 등록한다. vlm 을 주면 GPT 호출은 그쪽을 읽는다."""
    key = (str(session_id), str(kind))
    with _lock:
        _mem[key] = data
        if vlm is not None:
            _vlm[key] = vlm
        else:
            _vlm.pop(key, None)


def get(session_id: str, kind: str, for_vlm: bool = False) -> bytes | None:
    key = (str(session_id), str(kind))
    with _lock:
        if for_vlm and key in _vlm:
            return _vlm[key]
        return _mem.get(key)


def clear(session_id: str) -> int:
    """세션의 사진(가공본·가린 복사본 모두)을 메모리에서 지운다. 지운 사진 수 반환."""
    with _lock:
        keys = [k for k in _mem if k[0] == str(session_id)]
        for k in keys:
            _mem.pop(k, None)
        for k in [k for k in _vlm if k[0] == str(session_id)]:
            _vlm.pop(k, None)
    return len(keys)


def held() -> int:
    """지금 메모리에 있는 사진 수 — 헬스·감사용. 0 이 정상 대기 상태다."""
    with _lock:
        return len(_mem)


def load(photo: dict[str, Any], for_vlm: bool = False) -> bytes:
    """photo 행 → 원본 바이트. 메모리 우선, 없으면 Storage.

    for_vlm=True 는 **OpenAI 로 보낼 사진**을 읽을 때 — 얼굴을 가린 복사본이 있으면
    그것을 준다. 세그는 False 로 읽는다 (가린 사진은 세그 결과를 틀어뜨린다).

    ⚠️ 둘 다 없으면(팟 경로인데 메모리가 비었다 = 팟이 재시작했다) 예외다.
       사진은 어디에도 없으므로 재시도해도 같다 — 호출자가 잡을 FAILED 로 종결한다.
    """
    data = get(str(photo["session_id"]), str(photo["kind"]), for_vlm=for_vlm)
    if data is not None:
        return data
    path = photo.get("storage_path")
    if not path:
        raise PhotoUnavailable(
            "사진이 서버에 없습니다. 팟이 재시작됐거나 이미 처리가 끝났습니다 — 다시 올려주세요."
        )
    log.info("사진 다운로드: %s/%s", photo["storage_bucket"], path)
    return storage.download(photo["storage_bucket"], path)


class PhotoUnavailable(RuntimeError):
    """사진이 메모리에도 Storage 에도 없다. 재시도 무의미 (worker/run.py `_is_retryable`)."""

    retryable = False
