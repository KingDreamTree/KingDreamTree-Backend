"""팟 업로드용 일회용 토큰 — API 가 발급하고 팟이 검증한다.

    API   POST /sessions/{id}/upload-token  →  issue(user_id, session_id)
    팟    POST /upload (X-Upload-Token)     →  verify(token)

토큰 = base64url(JSON 페이로드) "." base64url(HMAC-SHA256(페이로드))

    페이로드: {"uid": user_id, "sid": session_id, "exp": 만료 epoch 초, "jti": 일회용 번호}

⚠️ **비밀키(POD_UPLOAD_SECRET)는 API 와 팟이 같은 값을 가져야 한다.** 한쪽만 바꾸면
   모든 업로드가 401 로 막힌다 — 배포 순서가 아니라 값의 일치가 문제다.

⚠️ 팟 주소는 토큰에도, 발급 응답에도 넣지 않는다. 주소는 프론트 빌드에 고정한다 —
   API 가 주소를 알려주는 순간 API 운영자가 주소를 바꿔치기해 사진을 받을 수 있다
   (A안의 공개키 바꿔치기와 같은 구멍).

⚠️ 일회용 판정(jti)은 **팟 프로세스 메모리**에서만 한다. 팟이 재시작하면 목록이
   비지만 토큰 수명이 2분이라 재사용 창도 그만큼뿐이다 — DB 에 기록하지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from uuid import UUID

from app.config import settings


class InvalidUploadToken(Exception):
    """서명 불일치·만료·형식 오류·재사용. 사유는 사용자에게 보여줘도 된다."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _secret() -> bytes:
    if not settings.pod_upload_secret:
        raise RuntimeError(
            "POD_UPLOAD_SECRET 이 설정되지 않았습니다. API 와 팟에 같은 값을 넣으세요."
        )
    return settings.pod_upload_secret.encode()


def _sign(payload_b64: str) -> str:
    return _b64(hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest())


def issue(user_id: UUID, session_id: UUID, ttl_sec: int | None = None) -> dict[str, Any]:
    """토큰을 만든다. 반환: {"token", "expires_at"(epoch 초)}."""
    ttl = ttl_sec if ttl_sec is not None else settings.pod_upload_token_ttl_sec
    exp = int(time.time()) + max(1, ttl)
    payload = {
        "uid": str(user_id),
        "sid": str(session_id),
        "exp": exp,
        "jti": secrets.token_urlsafe(12),
    }
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return {"token": f"{payload_b64}.{_sign(payload_b64)}", "expires_at": exp}


#: 사용한 jti → 만료 시각. 만료 지난 것은 검증 때마다 걷어낸다.
_used: dict[str, int] = {}


def _purge_used(now: int) -> None:
    for jti, exp in list(_used.items()):
        if exp <= now:
            _used.pop(jti, None)


def verify(token: str | None, consume: bool = True) -> dict[str, Any]:
    """서명·만료·일회용을 확인하고 페이로드를 돌려준다. 실패는 InvalidUploadToken.

    consume=True 면 이 jti 를 사용한 것으로 기록한다 — 같은 토큰의 두 번째 업로드는
    막힌다. 검증만 하고 소비하지 않으려면(사전 검사) False.
    """
    if not token or "." not in token:
        raise InvalidUploadToken("업로드 토큰이 없거나 형식이 올바르지 않습니다.")
    payload_b64, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        raise InvalidUploadToken("업로드 토큰 서명이 올바르지 않습니다.")
    try:
        payload = json.loads(_unb64(payload_b64))
        uid, sid, exp, jti = payload["uid"], payload["sid"], int(payload["exp"]), payload["jti"]
        UUID(uid)
        UUID(sid)
    except Exception:  # noqa: BLE001 — 어떤 형태로 깨졌든 결론은 같다
        raise InvalidUploadToken("업로드 토큰 내용을 읽을 수 없습니다.") from None

    now = int(time.time())
    if exp <= now:
        raise InvalidUploadToken("업로드 토큰이 만료됐습니다. 다시 발급받아주세요.")

    _purge_used(now)
    if jti in _used:
        raise InvalidUploadToken("이미 사용한 업로드 토큰입니다. 다시 발급받아주세요.")
    if consume:
        _used[jti] = exp
    return {"user_id": uid, "session_id": sid, "exp": exp, "jti": jti}
