"""VLM 서비스 — provider 추상화 레이어.

VLM_PROVIDER 환경변수로 구현체를 선택한다.
현재 지원: claude (stub) | openai (stub) | mock
확정되지 않은 provider는 TODO로 남겨두고, USE_MOCK=true로 우회한다.
"""

from typing import Any

from app.config import settings

# ---------------------------------------------------------------------------
# Mock 데이터
# ---------------------------------------------------------------------------

_MOCK_COMPARISON: dict[str, Any] = {
    "summary": "어깨 너비가 레퍼런스 대비 5.9% 좁고, 허리 너비는 비슷한 수준입니다.",
    "differences": {"shoulder_width": -2.5, "hip_width": 1.5, "waist_width": 1.4},
    "body_type": "역삼각형",
    "overlay_url": None,
}

_MOCK_ROUTINE: dict[str, Any] = {
    "goal": "어깨 라인 보완 및 상체 균형 개선",
    "frequency": "주 3회",
    "exercises": [
        {
            "name": "덤벨 숄더 프레스",
            "sets": 3,
            "reps": "10-12",
            "reason": "전면·측면 삼각근 활성화",
        },
        {
            "name": "사이드 레터럴 레이즈",
            "sets": 3,
            "reps": "12-15",
            "reason": "측면 삼각근 발달로 어깨 너비 강화",
        },
        {"name": "페이스 풀", "sets": 3, "reps": "15", "reason": "후면 삼각근 및 회전근개 안정화"},
    ],
}

# ---------------------------------------------------------------------------
# Provider 구현체
# ---------------------------------------------------------------------------

_COMPARE_PROMPT = (
    "다음 두 체형 세그멘테이션 데이터를 비교 분석해줘.\n"
    "사용자: {user_seg}\n레퍼런스: {ref_seg}\n"
    "JSON으로 summary, differences(수치), body_type을 반환해."
)

_ROUTINE_PROMPT = (
    "다음 체형 비교 분석 결과를 바탕으로 개인화 운동 루틴을 만들어줘.\n{comparison}\n"
    "JSON으로 goal, frequency, exercises(name·sets·reps·reason 목록)을 반환해."
)


async def _call_claude(prompt: str, max_tokens: int = 1024) -> str:
    """Claude Vision API 호출. VLM_PROVIDER=claude 일 때 사용."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    message = await client.messages.create(
        model="claude-opus-5",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    # TODO: structured output 파싱 (현재 raw text 반환)
    return message.content[0].text


async def _call_openai(
    prompt: str,
    max_tokens: int = 1024,
    image_urls: list[str] | None = None,
) -> str:
    """OpenAI GPT-4o API 호출. VLM_PROVIDER=openai 일 때 사용.

    image_urls: signed URL 목록. 텍스트+이미지 멀티모달 호출.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    content: list[dict] = []
    if image_urls:
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
    content.append({"type": "text", "text": prompt})

    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    return response.choices[0].message.content or ""


async def _call_vlm(
    prompt: str,
    max_tokens: int = 1024,
    image_urls: list[str] | None = None,
) -> str:
    """VLM_PROVIDER 설정에 따라 적절한 provider를 호출한다."""
    provider = settings.vlm_provider.lower()
    if provider == "claude":
        return await _call_claude(prompt, max_tokens)
    if provider == "openai":
        return await _call_openai(prompt, max_tokens, image_urls)
    raise ValueError(
        f"VLM_PROVIDER='{settings.vlm_provider}' 는 지원하지 않는 값입니다. "
        "'claude' | 'openai' 중 선택하거나 USE_MOCK=true로 우회하세요."
    )


#: 공개 별칭 — 다른 서비스가 provider 분기를 다시 구현하지 않게 한다.
#: (담당 A 의 사진 적합성 판정이 이걸 쓴다: app/services/photo_screening.py)
call_vlm = _call_vlm


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compare_body(user_seg: dict, ref_seg: dict) -> dict[str, Any]:
    """체형 비교 분석 (VLM Call1)."""
    if settings.use_mock:
        return dict(_MOCK_COMPARISON)

    raw = await _call_vlm(
        _COMPARE_PROMPT.format(user_seg=user_seg, ref_seg=ref_seg), max_tokens=1024
    )
    # TODO: JSON 파싱. 현재는 raw text를 그대로 반환
    return {"raw": raw}


async def generate_routine(comparison: dict) -> dict[str, Any]:
    """개인화 운동 루틴 생성 (VLM Call2)."""
    if settings.use_mock:
        return dict(_MOCK_ROUTINE)

    raw = await _call_vlm(_ROUTINE_PROMPT.format(comparison=comparison), max_tokens=2048)
    # TODO: JSON 파싱
    return {"raw": raw}
