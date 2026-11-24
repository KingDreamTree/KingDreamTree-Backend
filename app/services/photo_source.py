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

⚠️ GPT 입력은 `load(photo, for_vlm=True)` 로 읽는다. 팟은 준비된 메모리 복사본을,
   Storage 경로는 저장된 랜드마크로 얼굴을 가린 임시 복사본을 사용한다.
   저장 사진과 Sapiens 입력은 변경하지 않아 기존 부위 비율 계산을 유지한다.

⚠️ 등록·해제는 팟 파이프라인(app/pod/pipeline.py)만 한다. 처리가 끝나면 반드시
   `clear(session_id)` — 여기 남아 있으면 "메모리에서 몇 초"라는 말이 거짓이 된다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PIL import Image

from app.config import settings
from app.services import face_mask, images, storage

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


def mask_for_vlm(img: Image.Image, landmarks: list[dict[str, Any]] | None) -> Image.Image:
    """이미지와 같은 좌표계의 랜드마크로 OpenAI 입력 복사본을 만든다.

    기존 Pod의 얼굴 검출/가림 규칙을 사용한다. 얼굴이 검출되지 않는 경우의 한계도
    동일하다. 좌표가 아예 없는 과거 사진은 무가림으로 보내지 않고 재업로드를 요구한다.
    """
    if not landmarks:
        raise PhotoPrivacyUnavailable(
            "사진 보호 처리에 필요한 관절 좌표가 없습니다. 사진을 다시 업로드해주세요."
        )
    masked, box = face_mask.apply(img, landmarks, settings.storage_face_mask_style)
    if box is None:
        log.info("OpenAI 입력의 얼굴 위치가 검출되지 않았습니다 (뒷모습·프레임 밖 등)")
    return masked


def load(photo: dict[str, Any], for_vlm: bool = False) -> bytes:
    """photo 행 → 원본 바이트. 메모리 우선, 없으면 Storage.

    for_vlm=True 는 **OpenAI 로 보낼 사진**을 읽을 때. Storage 경로는 읽은 뒤
    임시 복사본에 얼굴 가림을 적용한다. 세그는 False로 읽어 기존 계산을 유지한다.

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
    raw = storage.download(photo["storage_bucket"], path)
    if not for_vlm:
        return raw
    masked = mask_for_vlm(images.load_rgb(raw), photo.get("pose_landmarks"))
    return images.encode_photo(masked)[0]


class PhotoUnavailable(RuntimeError):
    """사진이 메모리에도 Storage 에도 없다. 재시도 무의미 (worker/run.py `_is_retryable`)."""

    retryable = False


class PhotoPrivacyUnavailable(RuntimeError):
    """과거 저장 사진에 가림용 좌표가 없다. 원본을 외부로 보내지 않고 재업로드한다."""

    retryable = False
