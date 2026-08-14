"""2차 검사 — 레퍼런스와 사용자 사진이 비교 가능한 상태인지.

흐름에서의 위치
    업로드 → 1차: 자세 판정(프론트 값) → **2차: 여기** → 저장 + 세그 큐잉

⚠️ **두 장을 같이 본다.** 사용자 사진만 보면 "괜찮은 사진"인데 레퍼런스와 촬영
   거리가 딴판이라 비율 비교가 무의미해지는 경우를 못 잡는다. 판단 대상은
   사진 한 장의 품질이 아니라 **두 장의 비교 가능성**이다.

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
import io
import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.prompts import photo_screening as prompt
from app.services import images

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
    #: 모델이 1단계에서 관찰한 값과 적용한 규칙 번호. 사용자에게는 안 나가고
    #  로그·튜닝용이다. ⚠️ 오판정이 났을 때 "무엇을 잘못 봤는지"를 여기서 본다.
    observed: dict | None = None
    rule: int | None = None


_PASS = ScreenResult(suitable=True, skipped=True)


def _data_url(image_bytes: bytes) -> str:
    """VLM 에 넣을 data URL. **판정용으로 줄여서** 보낸다.

    ⚠️ Storage 에 올리고 signed URL 을 만드는 대신 이걸 쓴다. 거부될 사진을
       먼저 저장했다가 지우는 왕복을 없애기 위해서다.

    ⚠️ **저장용 크기(긴 변 최대 4096px)를 그대로 보내면 안 된다.** 이미지는
       해상도에 비례해 토큰을 먹고, 우리는 **두 장**을 보낸다.
       이 판정이 보는 것 — 옷이 몸에 붙었나 / 촬영 거리가 비슷한가 / 팔다리가
       잘렸나 — 은 전부 작은 이미지로도 판별된다. 원본 해상도가 필요 없다.
    """
    img = images.load_rgb(image_bytes)
    side = settings.photo_screening_max_side
    if max(img.size) > side:
        scale = side / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


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

    observed = data.get("observed")
    rule = data.get("rule")
    return ScreenResult(
        suitable=bool(data.get("suitable")),
        reason=data.get("reason") or None,
        message=str(data.get("message") or ""),
        confidence=data.get("confidence") or None,
        observed=observed if isinstance(observed, dict) else None,
        rule=rule if isinstance(rule, int) else None,
    )


async def screen(user_image: bytes, reference_image: bytes | None) -> ScreenResult:
    """두 사진이 비교 가능한지 판정한다.

    **예외를 던지지 않는다** — 어떤 실패든 통과로 처리한다.

    ⚠️ reference_image 가 없으면 판정을 건너뛴다. 한 장만 보면 원근 불일치를
       못 잡는데, 그 상태로 "검사했다"고 하면 통과 의미가 달라진다.
       (레퍼런스는 라우터에서 이미 존재를 확인하므로 정상 경로에서는 항상 있다)
    """
    if not settings.photo_screening_enabled:
        return _PASS
    if settings.use_mock or not settings.openai_api_key:
        log.info("2차 검사 건너뜀 (mock 또는 API 키 없음)")
        return _PASS
    if reference_image is None:
        log.warning("레퍼런스 이미지를 못 읽어 2차 검사를 건너뜁니다")
        return _PASS

    try:
        from app.services.vlm import call_vlm

        raw = await asyncio.wait_for(
            call_vlm(
                prompt.USER,
                max_tokens=300,
                # ⚠️ 순서가 프롬프트의 전제다 — 첫 번째가 레퍼런스, 두 번째가 사용자.
                image_urls=[_data_url(reference_image), _data_url(user_image)],
                # ⚠️ 0 으로 고정한다. 기본값(1.0)이면 같은 사진이 실행마다
                #    통과/반려를 오간다 — 실측에서 3회 중 2회가 뒤집혔다.
                #    사용자가 같은 사진을 다시 올려 통과되는 상황은 판정이
                #    있으나 마나 하다는 뜻이다.
                temperature=0.0,
            ),
            timeout=settings.photo_screening_timeout_sec,
        )
    except asyncio.TimeoutError:
        log.warning("2차 검사 타임아웃 (%.0fs) — 통과 처리", settings.photo_screening_timeout_sec)
        return _PASS
    except Exception:  # noqa: BLE001 — 어떤 실패든 업로드를 막지 않는다
        log.exception("2차 검사 실패 — 통과 처리")
        return _PASS

    result = _parse(raw)
    if result is None:
        log.warning("2차 검사 응답을 해석하지 못함 — 통과 처리: %s", raw[:200])
        return _PASS

    if not result.suitable and not result.message:
        # 모델이 message 를 빠뜨린 경우. 사용자에게 빈 문구를 보여줄 수는 없다.
        result.message = "사진으로 체형을 판단하기 어렵습니다. 몸에 붙는 옷으로 다시 촬영해주세요."

    log.info(
        "2차 검사: suitable=%s reason=%s rule=%s confidence=%s observed=%s",
        result.suitable,
        result.reason,
        result.rule,
        result.confidence,
        result.observed,
    )
    return result
