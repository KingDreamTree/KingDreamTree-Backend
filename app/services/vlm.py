"""F08 부위별 비교 진단 · F09 종합 진단 — VLM 호출과 응답 검증.

━━ 호출 구조 (2026-08-14 개정) ━━

    F08  Vision 1회   4장(레퍼런스 원본·오버레이, 사용자 원본·오버레이) + 수치 → 전 부위
    F09  Text  1회    F08 결과 → 점수·요약·우선순위

부위마다 호출하지 않는다. 이유는 app/prompts/part_diagnosis.py 모듈 주석 참고
(같은 원본을 부위 수만큼 재업로드 + 부위 간 순위를 매길 문맥이 없음).

━━ 응답을 믿지 않는다 ━━

LLM 응답은 **검증 후 부위 단위로 부분 채택**한다. 한 부위의 enum 이 소문자로
왔다고 나머지 8부위를 버리지 않는다. 못 쓰는 부위만 FAILED 행으로 남기고
나머지는 저장한다 — "부분 실패 허용"(work-b.md §6)이 호출 단위에서 파싱 단위로
옮겨왔을 뿐, 보장은 그대로다.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.config import settings
from app.prompts.overall_diagnosis import SYSTEM_PROMPT as OVERALL_SYSTEM
from app.prompts.overall_diagnosis import build_overall_prompt
from app.prompts.part_diagnosis import SYSTEM_PROMPT as PART_SYSTEM
from app.prompts.part_diagnosis import build_part_prompt
from app.schemas.enums import Confidence, GapLevel

log = logging.getLogger("services.vlm")

#: 진단용 모델. ⚠️ 바꾸면 프롬프트 튜닝 결과가 그대로 재현되지 않는다.
VLM_MODEL = "gpt-4o"

#: 부위 수만큼 항목이 나와야 하므로 넉넉히. 9부위 × 항목당 ~150토큰 + 여유.
PART_MAX_TOKENS = 4096
OVERALL_MAX_TOKENS = 1024


class VlmResponseError(RuntimeError):
    """응답을 JSON 으로 읽을 수 없거나 최상위 구조가 기대와 다르다.

    ⚠️ **재시도 대상이 아니다.** 같은 입력으로 다시 불러도 같은 형식 오류가 난다.
       기본값(재시도 3회)대로 두면 실패 하나가 Vision 호출 3회분으로 청구된다.
       워커가 `retryable` 속성을 보고 즉시 종결한다 (worker/run.py `_is_retryable`).
    """

    retryable = False


# --------------------------------------------------------------------------- #
# Mock — USE_MOCK=true 일 때 키 없이 전체 플로우를 돌린다
# --------------------------------------------------------------------------- #

_MOCK_GAPS = (
    (GapLevel.MODERATE, 2, Confidence.HIGH),
    (GapLevel.SLIGHT, 3, Confidence.MEDIUM),
    (GapLevel.SIGNIFICANT, 1, Confidence.HIGH),
    (GapLevel.NONE, 5, Confidence.MEDIUM),
)


def _mock_parts(class_names: list[str]) -> dict[str, Any]:
    """부위 목록에 맞춰 생성한다 — seed 데이터가 3부위든 9부위든 돌아야 한다."""
    results = []
    for i, name in enumerate(class_names):
        gap, priority, confidence = _MOCK_GAPS[i % len(_MOCK_GAPS)]
        results.append(
            {
                "class_name": name,
                "differences": ["레퍼런스 대비 볼륨이 부족", "근육 경계가 흐림"],
                "assessment": f"{name} 은(는) 레퍼런스 대비 개선 여지가 있습니다. (mock)",
                "gap_level": str(gap),
                "priority": priority,
                "confidence": str(confidence),
                "blocked_reason": None,
            }
        )
    return {"results": results, "missing": [], "raw_response": {"mock": True}}


def _mock_overall(parts: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(parts, key=lambda p: p.get("priority") or 99)
    return {
        "similarity_score": 68,
        "score_rationale": "상체 격차가 크고 하체는 근접 (mock)",
        "summary": "상체 중심 개선이 필요합니다. 어깨와 팔 근육 강화가 우선입니다. (mock)",
        "priority_parts": [p["class_name"] for p in ranked[:3]],
        "strengths": ["하체 균형이 좋습니다"],
        "cautions": ["좌우 차이가 있어 균형 운동을 권합니다"],
        "raw_response": {"mock": True},
    }


# --------------------------------------------------------------------------- #
# OpenAI 호출
# --------------------------------------------------------------------------- #


def _image_block(jpeg: bytes) -> dict[str, Any]:
    """base64 data URL 블록.

    signed URL 대신 본문에 실어 보낸다 — 워커는 서버 사이드라 URL 발급이
    불필요하고, 만료된 URL을 OpenAI가 못 읽어 조용히 실패하는 경로도 없앤다.
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}",
            "detail": "high",  # 근육 라인을 봐야 하므로 low 로 낮추지 않는다
        },
    }


async def _call_json(
    system: str,
    content: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """JSON mode 로 호출하고 (파싱된 결과, raw 응답)을 반환한다."""
    if settings.vlm_provider and settings.vlm_provider.lower() != "openai":
        raise VlmResponseError(
            f"VLM_PROVIDER='{settings.vlm_provider}' 는 진단 파이프라인에서 지원하지 않습니다. "
            "'openai' 로 설정하거나 USE_MOCK=true 로 우회하세요."
        )

    from openai import AsyncOpenAI  # 지연 import — services/ocr.py 상단 주석 참고

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=VLM_MODEL,
        response_format={"type": "json_object"},
        temperature=0,  # 같은 사진에 같은 진단이 나와야 한다
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    )

    raw = response.model_dump()
    text = response.choices[0].message.content
    if not text:
        # max_tokens 로 잘렸거나 안전 필터에 걸린 경우. raw 는 그대로 보존한다.
        raise VlmResponseError(
            f"VLM 응답이 비어 있습니다 (finish={response.choices[0].finish_reason})."
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise VlmResponseError("VLM 응답을 JSON 으로 읽을 수 없습니다.") from e

    if not isinstance(parsed, dict):
        raise VlmResponseError("VLM 응답 최상위가 객체가 아닙니다.")
    return parsed, raw


#: 공개 별칭 — 담당 A 의 2차 검사(app/services/photo_screening.py)가 쓴다.
#: ⚠️ provider 분기·JSON 모드·temperature=0 을 저쪽에서 다시 구현하지 않게 하려는 것이다.
#:    같은 호출을 두 벌 두면 한쪽만 고쳐져 어긋난다 (실제로 그렇게 될 뻔했다 —
#:    2차 검사가 temperature 를 안 잡아 판정이 실행마다 뒤집혔다).
#: ⚠️ 이름이 바뀌면 photo_screening.py 도 같이 고쳐야 한다.
call_json = _call_json
image_block = _image_block


# --------------------------------------------------------------------------- #
# 응답 검증 — 부위 단위 부분 채택
# --------------------------------------------------------------------------- #


def _as_str_list(value: Any) -> list[str]:
    """differences 는 배열이어야 한다. 문자열 하나로 오면 감싼다.

    ⚠️ 이어붙인 TEXT 로 저장하면 화면에서 항목별 나열이 불가능하다 (work-b.md §6).
    """
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _enum_or_none(value: Any, allowed: type[GapLevel] | type[Confidence]) -> str | None:
    """대소문자만 교정한다. 값 자체가 목록에 없으면 None (조용히 바꾸지 않는다).

    ⚠️ DB CHECK 가 대문자로 걸려 있어 소문자는 INSERT 가 터진다. 소문자는
       LLM 이 가장 흔히 내는 실수라 교정할 가치가 있지만, 목록에 없는 값
       (예: gap_level="HIGH")을 임의로 매핑하면 진단을 조작하는 셈이 된다.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    return candidate if candidate in {str(m) for m in allowed} else None


def _coerce_part(item: Any, allowed: set[str]) -> dict[str, Any] | None:
    """부위 항목 하나를 검증한다. 못 쓰면 None (호출부가 FAILED 로 남긴다)."""
    if not isinstance(item, dict):
        return None

    class_name = item.get("class_name")
    if not isinstance(class_name, str) or class_name not in allowed:
        return None

    blocked = item.get("blocked_reason")
    blocked = blocked.strip() if isinstance(blocked, str) and blocked.strip() else None

    gap_level = _enum_or_none(item.get("gap_level"), GapLevel)
    if gap_level is None and item.get("gap_level") is not None and blocked is None:
        # 판단 불가(null)도 아니고 유효한 등급도 아니다 = 형식 오류.
        return None

    confidence = _enum_or_none(item.get("confidence"), Confidence)
    if confidence is None:
        # 확신도를 못 읽었다고 진단을 버리진 않는다. 안전한 쪽(LOW)으로 둔다 —
        # LOW 는 종합 진단에서 가중치가 낮아지므로 과신 방향의 오류가 없다.
        confidence = str(Confidence.LOW)

    priority = item.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 5:
        priority = None

    assessment = item.get("assessment")
    assessment = assessment.strip() if isinstance(assessment, str) and assessment.strip() else None

    return {
        "class_name": class_name,
        "differences": _as_str_list(item.get("differences")),
        "assessment": assessment,
        "gap_level": gap_level,
        "priority": priority,
        "confidence": confidence,
        "blocked_reason": blocked,
    }


def parse_part_response(parsed: dict[str, Any], class_names: list[str]) -> dict[str, Any]:
    """응답 → (검증 통과한 부위, 못 쓴 부위 이름).

    ⚠️ 요청한 부위가 응답에 없으면 그 부위는 missing 이다. 조용히 넘기면
       "왜 왼팔 진단이 없지?"에 답할 수 없다.
    """
    allowed = set(class_names)
    results: dict[str, dict[str, Any]] = {}

    # ⚠️ `parts` 자체가 없으면 "전 부위 실패"가 아니라 **응답 형식 오류**다.
    #    구분하지 않으면 프롬프트에서 구조 명세가 빠진 사고가 조용히 FAILED 9건으로
    #    기록되고, 화면에는 "진단 실패"만 뜬 채 원인이 안 보인다. (실제로 겪었다)
    items = parsed.get("parts")
    if not isinstance(items, list):
        raise VlmResponseError(
            "응답에 parts 배열이 없습니다. 프롬프트의 출력 구조 명세를 확인하세요."
        )

    for item in items:
        part = _coerce_part(item, allowed)
        if part is None:
            log.warning("부위 항목을 검증하지 못했습니다: %.200s", item)
            continue
        # 같은 부위를 두 번 보내오면 처음 것만 쓴다 (UNIQUE(session_id, class_name)).
        results.setdefault(part["class_name"], part)

    return {
        "results": [results[n] for n in class_names if n in results],
        "missing": [n for n in class_names if n not in results],
    }


def _clamp_score(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(100, int(round(value))))


def parse_overall_response(
    parsed: dict[str, Any],
    class_names: set[str],
) -> dict[str, Any]:
    """종합 진단 응답 검증. priority_parts 는 실재하는 부위 이름만 남긴다."""
    priority = [p for p in _as_str_list(parsed.get("priority_parts")) if p in class_names][:5]

    summary = parsed.get("summary")
    rationale = parsed.get("score_rationale")

    return {
        "similarity_score": _clamp_score(parsed.get("similarity_score")),
        "score_rationale": rationale.strip() if isinstance(rationale, str) else None,
        "summary": summary.strip() if isinstance(summary, str) else None,
        "priority_parts": priority,
        "strengths": _as_str_list(parsed.get("strengths")),
        "cautions": _as_str_list(parsed.get("cautions")),
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def diagnose_parts(
    reference_photo: bytes,
    reference_overlay: bytes,
    user_photo: bytes,
    user_overlay: bytes,
    parts: list[dict[str, Any]],
    metrics: dict[str, Any],
    reference_symmetry: dict[str, float],
    user_symmetry: dict[str, float],
    inbody: dict[str, Any] | None,
) -> dict[str, Any]:
    """F08 — 비교 대상 전 부위를 한 번의 Vision 호출로 진단한다.

    Args:
        parts: body_part 마스터 행 (class_name / name_ko / color_hex / inbody_segment).
               ⚠️ 부위 목록은 DB 가 유일한 출처다. 여기서 만들지 않는다.

    Returns:
        {"results": [검증 통과 부위...], "missing": [class_name...], "raw_response": {...}}
    """
    class_names = [p["class_name"] for p in parts]

    if settings.use_mock:
        return _mock_parts(class_names)

    text = build_part_prompt(
        parts=parts,
        metrics=metrics,
        ref_symmetry=reference_symmetry,
        user_symmetry=user_symmetry,
        inbody=inbody,
    )

    # ⚠️ 이미지 순서가 프롬프트의 설명 순서와 정확히 같아야 한다.
    #    순서가 어긋나면 레퍼런스와 사용자가 뒤바뀌어 진단이 정반대로 나온다.
    content = [
        _image_block(reference_photo),
        _image_block(reference_overlay),
        _image_block(user_photo),
        _image_block(user_overlay),
        {"type": "text", "text": text},
    ]

    parsed, raw = await _call_json(PART_SYSTEM, content, PART_MAX_TOKENS)
    out = parse_part_response(parsed, class_names)
    out["raw_response"] = raw
    return out


async def diagnose_overall(
    results: list[dict[str, Any]],
    failed: list[str],
    inbody: dict[str, Any] | None,
) -> dict[str, Any]:
    """F09 — 부위별 진단을 종합한다. **이미지를 보내지 않는다** (텍스트 전용).

    Args:
        results: F08 에서 저장에 성공한 부위 진단 전체 (판단 불가 포함)
        failed:  기술적으로 실패한 부위 이름
    """
    judged = [p for p in results if p.get("gap_level")]
    blocked = [p for p in results if not p.get("gap_level")]

    if settings.use_mock:
        return _mock_overall(judged or results)

    text = build_overall_prompt(parts=judged, blocked=blocked, failed=failed, inbody=inbody)
    parsed, raw = await _call_json(
        OVERALL_SYSTEM, [{"type": "text", "text": text}], OVERALL_MAX_TOKENS
    )

    out = parse_overall_response(parsed, {p["class_name"] for p in results})
    out["raw_response"] = raw
    return out
