"""VLM 서비스 — Claude Vision 호출 또는 mock 분기."""

from typing import Any

from app.config import settings

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


async def compare_body(user_seg: dict, ref_seg: dict) -> dict[str, Any]:
    """체형 비교 분석 — Claude Call1."""
    if settings.use_mock:
        return dict(_MOCK_COMPARISON)

    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = (
        f"다음 두 체형 세그멘테이션 데이터를 비교 분석해줘.\n"
        f"사용자: {user_seg}\n레퍼런스: {ref_seg}\n"
        "JSON으로 summary, differences(수치), body_type을 반환해."
    )
    message = await client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    # TODO: 응답 파싱 — 현재는 텍스트 그대로 반환
    return {"raw": message.content[0].text}


async def generate_routine(comparison: dict) -> dict[str, Any]:
    """개인화 운동 루틴 생성 — Claude Call2."""
    if settings.use_mock:
        return dict(_MOCK_ROUTINE)

    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = (
        f"다음 체형 비교 분석 결과를 바탕으로 개인화 운동 루틴을 만들어줘.\n{comparison}\n"
        "JSON으로 goal, frequency, exercises(name·sets·reps·reason 목록)을 반환해."
    )
    message = await client.messages.create(
        model="claude-opus-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return {"raw": message.content[0].text}
