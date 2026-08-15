"""2차 검사 — 레퍼런스와 사용자 사진이 비교 가능한 상태인지.

흐름에서의 위치
    업로드 → 1차: 자세 판정(프론트 값) → **2차: 여기** → 저장 + 세그 큐잉

⚠️ **두 장을 같이 본다.** 사용자 사진만 보면 "괜찮은 사진"인데 레퍼런스와 촬영
   거리가 딴판이라 비율 비교가 무의미해지는 경우를 못 잡는다. 판단 대상은
   사진 한 장의 품질이 아니라 **두 장의 비교 가능성**이다.

⚠️ **거부된 사진은 저장하지 않는다.** 이미지를 base64 로 VLM 에 직접 보내므로
   Storage 에 올릴 필요가 없다. 사람 몸 사진이라, 안 쓸 사진은 서버에 아예
   안 남기는 편이 낫다.

⚠️ **판정에 실패하면 막는다 (fail-closed, 2026-08-16).** 저장하지 않고
   "일시적 오류, 잠시 후 다시 시도" (503) — 반려(422)가 아니다. 사진이 나쁜 게
   아니라 검사기가 아픈 것이므로, 재촬영이 아니라 재시도를 안내해야 정직하다.
   ⚠️ 원래는 fail-open(통과 처리)이었다. 시연이 멈추는 것보다 낫다는 판단이었는데
      뒤집었다 — 2차가 거르는 것 중 **헐렁한 옷은 사후 검증(픽셀 수)이 못 잡는다**
      (옷 픽셀이 부위로 병합돼 픽셀 수는 멀쩡하다). 검사 없이 들여보내면 잘못된
      굵기 진단이 정상 결과처럼 나간다 — 그건 시연에서도 더 나쁘다. 몸 사진을
      검사 없이 저장하지 않는다는 원칙과도 일치한다.
   ⚠️ 설정으로 껐거나(mock·API 키 없음) 레퍼런스를 못 읽은 경우는 지금도
      건너뛰고 통과다 — 그건 장애가 아니라 환경의 의도된 상태다.

⚠️ 왜 동기인가 — 목적이 **재촬영 유도**다. 비동기로 돌리면 사용자가 다음 화면으로
   넘어간 뒤에 "다시 찍으세요"가 뜬다. 그러면 이 판정을 넣은 이유가 없어진다.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

from PIL import Image

from app.config import settings
from app.prompts import photo_screening as prompt
from app.services import images

log = logging.getLogger("services.photo_screening")


class ScreeningUnavailable(Exception):
    """VLM 장애·타임아웃·응답 해석 불가로 **판정 자체를 못 한** 경우.

    ⚠️ "부적합"(suitable=False)과 다르다 — 사진에 대해 아무것도 알아내지
       못했다는 뜻이다. 라우터는 이걸 받아 503으로 바꾼다 (사진 저장 안 함).
    """


@dataclass
class ScreenResult:
    """판정 결과.

    skipped=True 는 "판정을 건너뛰었다"는 뜻이다 (설정으로 껐거나 mock,
    레퍼런스 없음). 이때 suitable 은 항상 True. VLM 장애는 여기 안 온다 —
    ScreeningUnavailable 예외로 나간다.
    """

    suitable: bool
    reason: str | None = None
    message: str = ""
    confidence: str | None = None
    skipped: bool = False
    #: 모델이 1단계에서 관찰한 값과 처음 깨진 조건. 사용자에게는 안 나가고
    #  로그·튜닝용이다. ⚠️ 오판정이 났을 때 "무엇을 잘못 봤는지"를 여기서 본다.
    observed: dict | None = None
    rule: int | None = None


_PASS = ScreenResult(suitable=True, skipped=True)


def _jpeg_for_vlm(image: bytes | Image.Image) -> bytes:
    """판정용으로 **줄인** JPEG. base64 포장은 vlm.image_block() 이 한다.

    ⚠️ **저장용 크기(긴 변 최대 4096px)를 그대로 보내면 안 된다.** 이미지는
       해상도에 비례해 토큰을 먹고, 우리는 **두 장**을 보낸다.
       이 판정이 보는 것 — 옷이 몸에 붙었나 / 촬영 거리가 비슷한가 / 팔다리가
       잘렸나 — 은 전부 작은 이미지로도 판별된다. 원본 해상도가 필요 없다.
       3000x4000 기준 전송량 95% 감소를 확인했다.
    """
    # ⚠️ 이미 디코딩된 이미지를 받으면 다시 디코딩하지 않는다.
    #    호출부(routes/photos.py)는 업로드를 이미 열어둔 상태다. bytes 로 주고받으면
    #    인코딩 → 디코딩을 원본 크기로 한 번씩 더 하게 된다.
    img = image if isinstance(image, Image.Image) else images.load_rgb(image)
    side = settings.photo_screening_max_side
    if max(img.size) > side:
        scale = side / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _to_result(data: dict) -> ScreenResult | None:
    """검증된 JSON → ScreenResult. 모양이 아니면 None (→ 검사 불가 처리).

    ⚠️ 코드펜스·앞뒤 설명을 벗겨내던 코드가 있었는데 지웠다. vlm.call_json() 이
       JSON 모드(response_format=json_object)로 부르므로 항상 dict 로 온다.
    """
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


async def screen(user_image: bytes | Image.Image, reference_image: bytes | None) -> ScreenResult:
    """두 사진이 비교 가능한지 판정한다.

    VLM 장애·타임아웃·응답 해석 불가면 **ScreeningUnavailable 을 던진다** —
    판정 없이 통과시키지 않는다 (모듈 주석의 fail-closed 참조).

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
        from app.services import vlm

        parsed, _raw = await asyncio.wait_for(
            # ⚠️ 담당 B 의 진단 파이프라인과 **같은 호출부**를 쓴다.
            #    provider 분기·JSON 모드·temperature=0 이 저기 한 군데에만 있다.
            #    두 벌로 두면 한쪽만 고쳐져 어긋난다 (2차 검사가 temperature 를
            #    안 잡아 판정이 실행마다 뒤집힌 적이 있다).
            vlm.call_json(
                system=prompt.SYSTEM,
                # ⚠️ 순서가 프롬프트의 전제다 — 첫 번째가 레퍼런스, 두 번째가 사용자.
                content=[
                    vlm.image_block(_jpeg_for_vlm(reference_image)),
                    vlm.image_block(_jpeg_for_vlm(user_image)),
                    {"type": "text", "text": prompt.USER},
                ],
                max_tokens=400,
            ),
            timeout=settings.photo_screening_timeout_sec,
        )
    except asyncio.TimeoutError:
        log.warning("2차 검사 타임아웃 (%.0fs) — 검사 불가", settings.photo_screening_timeout_sec)
        raise ScreeningUnavailable("timeout") from None
    except Exception as e:  # noqa: BLE001
        log.exception("2차 검사 실패 — 검사 불가")
        raise ScreeningUnavailable("vlm_error") from e

    result = _to_result(parsed)
    if result is None:
        log.warning("2차 검사 응답을 해석하지 못함 — 검사 불가: %.200s", parsed)
        raise ScreeningUnavailable("unparseable_response")

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
