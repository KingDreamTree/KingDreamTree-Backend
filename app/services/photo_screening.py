"""사용자 사진 적합성 판정 — 세그멘테이션을 돌릴 가치가 있는 사진인지.

흐름에서의 위치
    업로드 → 자세 판정(프론트 값) → **여기** → 저장 + 세그 큐잉

⚠️ **거부된 사진은 저장하지 않는다.** 이미지를 base64 로 VLM 에 직접 보내므로
   Storage 에 올릴 필요가 없다. 사람 몸 사진이라, 안 쓸 사진은 서버에 아예
   안 남기는 편이 낫다.

⚠️ **판정에 실패하면 통과시킨다 (fail-open).** OpenAI 가 느리거나 죽었을 때
   업로드가 통째로 막히면 시연이 멈춘다. 나쁜 사진 하나가 들어오는 것보다
   서비스 전체가 서는 게 더 큰 손해라는 판단이다.
   ⚠️ 실사용으로 가면 이 판단을 다시 해야 한다 — 그때는 막는 쪽이 맞을 수 있다.

⚠️ 왜 동기인가 — 목적이 **재촬영 유도**다. 비동기로 돌리면 사용자가 다음 화면으로
   넘어간 뒤에 "다시 찍으세요"가 뜬다. 그러면 이 판정을 넣은 이유가 없어진다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.prompts import photo_screening as prompt

log = logging.getLogger("services.photo_screening")


@dataclass
class ScreenResult:
    """판정 결과.

    skipped=True 는 "판정을 못 했다"는 뜻이다 (설정으로 껐거나 VLM 실패).
    이때 suitable 은 항상 True — fail-open 이다.
    """

    suitable: bool
    reason: str | None = None
    message: str = ""
    confidence: str | None = None
    skipped: bool = False


_PASS = ScreenResult(suitable=True, skipped=True)


def _data_url(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    """VLM 에 그대로 넣을 수 있는 data URL.

    ⚠️ Storage 에 올리고 signed URL 을 만드는 대신 이걸 쓴다. 거부될 사진을
       먼저 저장했다가 지우는 왕복을 없애기 위해서다.
    """
    return f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"


def _parse(raw: str) -> ScreenResult | None:
    """VLM 응답에서 JSON 을 꺼낸다. 모양이 아니면 None (→ 통과 처리)."""
    text = raw.strip()

    # ```json ... ``` 로 감싸 오는 경우가 흔하다
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "suitable" not in data:
        return None

    return ScreenResult(
        suitable=bool(data.get("suitable")),
        reason=data.get("reason") or None,
        message=str(data.get("message") or ""),
        confidence=data.get("confidence") or None,
    )


async def screen(image_bytes: bytes) -> ScreenResult:
    """사진 한 장을 판정한다. **예외를 던지지 않는다** — 실패는 통과로 처리한다."""
    if not settings.photo_screening_enabled:
        return _PASS
    if settings.use_mock or not settings.openai_api_key:
        log.info("사진 적합성 판정 건너뜀 (mock 또는 API 키 없음)")
        return _PASS

    try:
        from app.services.vlm import call_vlm

        raw = await asyncio.wait_for(
            call_vlm(
                prompt.USER,
                max_tokens=300,
                image_urls=[_data_url(image_bytes)],
            ),
            timeout=settings.photo_screening_timeout_sec,
        )
    except asyncio.TimeoutError:
        log.warning(
            "사진 적합성 판정 타임아웃 (%.0fs) — 통과 처리", settings.photo_screening_timeout_sec
        )
        return _PASS
    except Exception:  # noqa: BLE001 — 어떤 실패든 업로드를 막지 않는다
        log.exception("사진 적합성 판정 실패 — 통과 처리")
        return _PASS

    result = _parse(raw)
    if result is None:
        log.warning("사진 적합성 판정 응답을 해석하지 못함 — 통과 처리: %s", raw[:200])
        return _PASS

    if not result.suitable and not result.message:
        # 모델이 message 를 빠뜨린 경우. 사용자에게 빈 문구를 보여줄 수는 없다.
        result.message = "사진으로 체형을 판단하기 어렵습니다. 몸에 붙는 옷으로 다시 촬영해주세요."

    log.info(
        "사진 적합성 판정: suitable=%s reason=%s confidence=%s",
        result.suitable,
        result.reason,
        result.confidence,
    )
    return result
