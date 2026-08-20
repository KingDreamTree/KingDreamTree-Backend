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
import re
from difflib import SequenceMatcher
from typing import Any

from app.config import settings
from app.prompts.overall_diagnosis import SYSTEM_PROMPT as OVERALL_SYSTEM
from app.prompts.overall_diagnosis import build_overall_prompt
from app.prompts.part_comparison import SYSTEM_PROMPT as PART_COMPARISON_SYSTEM
from app.prompts.part_comparison import build_part_comparison_prompt
from app.prompts.part_diagnosis import SYSTEM_PROMPT as PART_SYSTEM
from app.prompts.part_diagnosis import build_part_prompt
from app.schemas.enums import Confidence, GapLevel

log = logging.getLogger("services.vlm")

#: 진단용 모델. ⚠️ 바꾸면 프롬프트 튜닝 결과가 그대로 재현되지 않는다.
VLM_MODEL = "gpt-4o"

#: 부위 수만큼 항목이 나와야 하므로 넉넉히. 9부위 × 항목당 ~150토큰 + 여유.
PART_MAX_TOKENS = 4096
#: ⚠️ 2026-08-17 상향 — 프로필 A·B·방향·전략·다음사이클이 추가돼 1024 로는 잘린다.
#:    잘리면 JSON 이 깨져 종합 진단이 통째로 실패한다 (부분 저장이 없다).
OVERALL_MAX_TOKENS = 2048


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
    # ⚠️ 점수는 여기 없다 — scoring.compute_similarity() 가 실제 계산한다 (mock 여부 무관).
    ranked = sorted(parts, key=lambda p: p.get("priority") or 99)
    return {
        "summary": (
            "하체는 이미 목표 체형에 가깝게 균형이 잡혀 있어요. "
            "차이는 상체, 특히 양쪽 팔 근육에서 가장 큽니다. "
            "4주간 팔 위주의 상체 운동에 집중하면 실루엣이 눈에 띄게 달라질 거예요. (mock)"
        ),
        "strengths": ["하체 균형이 좋습니다"],
        "cautions": ["좌우 차이가 있어 균형 운동을 권합니다"],
        # ⚠️ 실제 호출과 같은 키를 낸다. mock 만 키가 적으면 USE_MOCK 로 개발한
        #    프론트가 실서버에서 처음 보는 필드를 만난다 (반대도 마찬가지다).
        "silhouette": (
            "어깨에서 허리로 내려오는 폭은 목표와 비슷하지만, "
            "상체 대비 하체의 볼륨이 목표 쪽이 더 큽니다. (mock)"
        ),
        "key_differences": [
            "상체 대비 하체 볼륨이 목표보다 적습니다 (mock)",
            "어깨 폭 대비 허리가 넓어 전체 윤곽이 다르게 보입니다 (mock)",
        ],
        "confidence": 0.8,
        "user_profile": {
            "summary": "상체보다 하체 쪽 실루엣이 상대적으로 강조되어 보입니다. (mock)",
            "characteristics": ["어깨 대비 허리 폭 차이가 크지 않음", "하체 외곽선이 뚜렷함"],
        },
        "reference_profile": {
            "summary": "어깨에서 허리로 내려오는 폭 변화가 뚜렷하게 관찰됩니다. (mock)",
            "characteristics": ["상체 폭이 상대적으로 강조됨", "허리 라인이 뚜렷함"],
        },
        "direction_summary": "약점 부위의 근력 강화를 우선하는 방향입니다. (mock)",
        "strategy_focus": ["상체 볼륨을 만드는 데 무게를 싣습니다 (mock)"],
        "next_cycle": "4주 뒤 인바디를 다시 재면 방향을 새로 판정합니다. (mock)",
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

    # timeout 을 주지 않으면 SDK 기본값(read 600s × 재시도)까지 매달린다 — 한 호출이
    # 최대 30분이다. 그동안 워커 슬롯이 잡혀 뒤의 잡이 전부 서고, 900초를 넘기면
    # is_stale 이 멀쩡한 잡을 좀비로 걸러 Vision 호출이 한 번 더 나간다(요금 2배).
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=1)
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


#: differences 가 assessment 의 격차 문장을 되풀이했다고 볼 유사도 하한.
#: 실측(2026-08-15 진단 출력) — 재진술 0.63~0.68 / 진짜 관찰 0.15~0.22 로 갈렸다.
#: 사이가 넓어 중간값이면 충분하다.
_RESTATE_RATIO = 0.45


def _drop_restatements(differences: list[str], assessment: str | None) -> list[str]:
    """assessment 를 되풀이하는 differences 항목을 버린다.

    ⚠️ **프롬프트가 이미 금지하고 있는데도 계속 나온다.** ("assessment 의 격차 문장을
       되풀이하지 마세요" — part_diagnosis.py §differences) 화면에는 부위 카드마다
       같은 말이 두 줄로 붙어 "왼팔 상완이 목표 체형보다 가늘어 보입니다"가
       진단문 아래 그대로 반복됐다.

       항목을 하나씩 생성하는 모델에게 "앞 문장과 겹치지 말라"는 자기참조 제약은
       잘 안 붙는다. _citation_targets 가 인용 부위를 코드로 정한 것과 같은 이유로,
       여기서도 코드가 지운다 — 지시는 어길 수 있지만 필터는 어길 수 없다.

    ⚠️ 문장 단위로 비교한다. assessment 전체와 재면 긴 쪽에 묻혀 비율이 낮아진다.
    """
    if not assessment or not differences:
        return differences

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", assessment) if s.strip()]
    kept = []
    for d in differences:
        ratio = max(
            (SequenceMatcher(None, _norm_ko(d), _norm_ko(s)).ratio() for s in sentences),
            default=0.0,
        )
        if ratio >= _RESTATE_RATIO:
            log.info("differences 재진술 제거 (유사도 %.2f): %.60s", ratio, d)
            continue
        kept.append(d)
    return kept


def _norm_ko(text: str) -> str:
    """공백·문장부호를 털어 비교용으로 만든다 (조사 차이까지는 굳이 안 건드린다)."""
    return re.sub(r"[\s.,!?·…\"'()]", "", text)


#: 부위 카드에서 잘라낼 **권유 어미**. 부위 카드는 본 것만 말하고, 방향은 종합
#: 진단(silhouette)이 전체를 보고 한 번만 말한다 — 부위마다 방향을 달면 그
#: 방향들이 서로 맞는지 아무도 보증하지 않는다.
#:
#: ⚠️ 낱말이 아니라 **어미**로 가른다. "발달"·"방향" 같은 낱말로 자르면
#:    "상완 부위의 발달 차이가 주요한 차이로 확인됩니다" 같은 정상 관찰문까지
#:    날아간다. 관찰은 «나타납니다 / 확인됩니다 / 드러납니다 / 보입니다 /
#:    어렵습니다» 로 끝나고, 권유는 아래로 끝난다.
_ADVICE_ENDINGS = (
    "좋습니다",
    "필요합니다",
    "권장합니다",
    "적합합니다",
    "추천합니다",
    "낫습니다",
    "하세요",
    "해보세요",
    "바랍니다",
    "important",  # 영문 응답 방어 (형식 붕괴 시)
)


#: 진단문에 나오면 안 되는 운동 종목 이름. **루틴이 카탈로그에서 고르는 영역**이라
#: 여기서 종목을 약속하면 다음 화면과 어긋난다 ("아까는 덤벨 컬이라더니").
#: ⚠️ 프롬프트가 두 곳에서 금지하는데도 나온다 (실측 2026-08-17: 몸통 카드 "플랭크").
#:    짧은 토큰("컬")은 다른 낱말에 섞이므로 넣지 않는다 — 어차피 "덤벨 컬" 형태라
#:    "덤벨"에서 걸린다.
_EXERCISE_NAMES = (
    "덤벨",
    "바벨",
    "케틀벨",
    "플랭크",
    "스쿼트",
    "런지",
    "팔굽혀펴기",
    "푸시업",
    "풀업",
    "친업",
    "데드리프트",
    "벤치프레스",
    "숄더프레스",
    "레그프레스",
    "랫풀다운",
    "크런치",
    "레그레이즈",
    "레터럴 레이즈",
    "사이드 레이즈",
)


def _strip_exercise_names(assessment: str | None) -> str | None:
    """종목 이름이 든 문장을 통째로 걷어낸다.

    문장 단위로 지우는 이유 — "플랭크와 같은 코어 운동을 통해"에서 낱말만 빼면
    "와 같은 코어 운동을 통해"가 남는다. 이 진단의 본체는 **관찰**이고 처방은
    덧붙임이므로, 통째로 빼도 카드는 성립한다.
    """
    if not assessment:
        return assessment
    kept = [
        sentence
        for sentence in assessment.split(". ")
        if not any(name in sentence for name in _EXERCISE_NAMES)
    ]
    if not kept or len(kept) == len(assessment.split(". ")):
        return assessment if kept else None
    out = ". ".join(part.rstrip(". ") for part in kept)
    return out if out.endswith(".") else out + "."


def _strip_prescription(assessment: str | None) -> str | None:
    """부위 카드 서술에서 권유 문장을 걷어낸다. 관찰 문장만 남긴다.

    ⚠️ 이 함수가 필요한 이유는 프롬프트가 두 곳에서 금지하는데도 나오기 때문이다
       (실측 2026-08-17: "팔을 굽혀 드는 운동에 집중하는 것이 좋습니다").
       이 리포의 규칙 — 모델이 반복해서 어기는 규칙은 산문이 아니라 코드로 막는다.

    ⚠️ 판단 불가 부위에서는 특히 중요하다. "못 봤다" 뒤에 방향이 붙으면 사용자는
       뒤 문장을 믿는데 그 부위의 근거는 0 이다.
    """
    if not assessment:
        return assessment
    kept = [
        sentence
        for sentence in assessment.split(". ")
        if not any(sentence.rstrip(". ").endswith(end) for end in _ADVICE_ENDINGS)
    ]
    if not kept:
        # 권유만 있고 관찰이 없었다 — 진단이라 부를 수 없으므로 버린다.
        return None
    out = ". ".join(part.rstrip(". ") for part in kept)
    return out if out.endswith(".") else out + "."


def _differences_from_seen(seen: Any, assessment: str | None) -> list[str]:
    """seen(현재/목표 관찰)으로 differences 를 만든다 — 퀵 경로 카드 보강용.

    퀵 프롬프트(part_comparison.py)는 부위마다 `seen.user` / `seen.reference` 를
    25자 이내로 먼저 채우게 한다. 이 두 칸은 **부위마다 실제로 다르게** 들어오는데
    (실측: "복부가 두드러짐" / "복근이 뚜렷함"), 그동안 파싱조차 하지 않고 버렸다.

    ⚠️ assessment 가 같은 관찰을 되풀이해 differences 가 재진술 필터에 전부
       걸리는 것이 퀵 경로의 기본 동작이었다(실측 2026-08-20: 9/9). 그 결과
       화면 카드에는 판정 문장 한 줄만 남았다. 관찰은 있는데 안 보여준 것이다.

    ⚠️ 여기서도 **같은 재진술 기준을 다시 태운다.** seen 이 assessment 를
       그대로 옮긴 것이면 보여줄 가치가 없다 — 화면에 같은 말이 두 줄 된다.

    ⚠️ 문장을 새로 짓지 않는다. 모델이 관찰한 문구를 «현재/목표» 라벨만 붙여
       그대로 쓴다. 코드가 진단 문장을 창작하면 근거를 댈 수 없다.
    """
    if not isinstance(seen, dict):
        return []
    out: list[str] = []
    for key, label in (("user", "현재"), ("reference", "목표")):
        value = seen.get(key)
        if isinstance(value, str) and value.strip():
            out.append(f"{label} {value.strip().rstrip('.')}")
    return _drop_restatements(out, assessment)


def _coerce_part(
    item: Any, allowed: set[str], inbody_available: bool = True
) -> dict[str, Any] | None:
    """부위 항목 하나를 검증한다. 못 쓰면 None (호출부가 FAILED 로 남긴다)."""
    if not isinstance(item, dict):
        return None

    class_name = item.get("class_name")
    if not isinstance(class_name, str) or class_name not in allowed:
        return None

    blocked = item.get("blocked_reason")
    blocked = blocked.strip() if isinstance(blocked, str) and blocked.strip() else None

    assessment = item.get("assessment")
    assessment = assessment.strip() if isinstance(assessment, str) and assessment.strip() else None

    # ⚠️ 종목 이름과 권유 문장은 **등급과 무관하게 항상** 막는다.
    #    부위 카드는 본 것만 말한다 — 방향은 종합 진단이 전체를 보고 한 번만 말한다.
    stripped = _strip_exercise_names(assessment)
    if stripped != assessment:
        log.warning("%s: 진단문에 운동 종목 이름이 있어 해당 문장 제거", class_name)
        assessment = stripped
    stripped = _strip_prescription(assessment)
    if stripped != assessment:
        log.warning("%s: 진단문에 권유 문장이 있어 제거", class_name)
        assessment = stripped

    differences = _drop_restatements(_as_str_list(item.get("differences")), assessment)
    # ⚠️ 재진술 필터에 differences 가 전부 걸리면 카드에 관찰이 하나도 안 남는다
    #    (실측 2026-08-20, 퀵 경로 9/9). 모델이 assessment 와 differences 에
    #    **같은 관찰 하나**를 두 번 쓰기 때문인데, 정작 seen 에는 부위마다 서로
    #    다른 관찰을 제대로 채워 놓고 그동안 그걸 버리고 있었다. 되살린다.
    #    ⚠️ **blocked 부위에는 쓰지 않는다.** 프롬프트가 seen 에 "안 보이면 왜
    #       안 보이는지"를 적게 하므로, 그걸 관찰로 옮기면 못 본 부위에 관찰이
    #       붙고 아래 모순 게이트가 blocked 를 해제해 버린다 (유닛 테스트로 확인).
    if not differences and blocked is None:
        differences = _differences_from_seen(item.get("seen"), assessment)

    # ⚠️ 자기모순 게이트 (2026-08-15 실측): differences 에 시각 관찰("두께가
    #    두드러집니다")을 적어놓고 blocked_reason("시각 확인 불가")을 함께 보낸
    #    케이스가 실물로 나왔다. few-shot 의 blocked 예시가 실데이터와 거의
    #    일치하자(몸통 + 평균 94% ≈ TRUNK 94.5%) 프레임째 복사된 것.
    #    눈으로 본 게 있다면 가려진 게 아니다 — 관찰 기록이 우선이고 blocked 는
    #    코드가 해제한다. 이 게이트는 아래 "blocked+인바디 없음 → gap 강등"보다
    #    먼저 와야 한다: 관찰이 있으면 gap 의 근거는 시각이므로 강등 대상이 아니다.
    if blocked is not None and differences:
        log.warning("%s: differences 관찰이 있는데 blocked 선언 — 모순, blocked 해제", class_name)
        blocked = None

    gap_level = _enum_or_none(item.get("gap_level"), GapLevel)
    if gap_level is None and item.get("gap_level") is not None and blocked is None:
        # 판단 불가(null)도 아니고 유효한 등급도 아니다 = 형식 오류.
        return None

    # ⚠️ 시각 판단 불가 + 인바디 없음 → gap_level 은 없어야 한다 (2026-08-14 실연동).
    #    프롬프트가 null 을 강제하지만 LLM 이 어기고 등급을 보내오는 케이스가 실물로
    #    나왔다 — SIGNIFICANT 2건이 이 경로로 점수에 들어가 56점(빼면 88점)을 만들었다.
    #    "시각으로도 못 봤고 실측도 없는" 등급은 근거가 0 이므로 코드가 무효화한다.
    #    (인바디가 있으면 유지 — 인바디를 근거로 등급을 매기는 건 프롬프트가 허용한 경로다)
    if blocked is not None and not inbody_available and gap_level is not None:
        log.warning(
            "%s: blocked 인데 인바디 없이 gap_level=%s — 근거 없음, null 로 강등",
            class_name,
            gap_level,
        )
        gap_level = None

    # ⚠️ 판단 불가면 표(프롬프트 §옷에 가려도…)가 정한 상태는 하나뿐이다:
    #    gap_level=null + confidence=LOW. 실측(2026-08-17)에서 모델이 표의 절반만
    #    적용해 blocked_reason 은 "인바디 기준 판단"인데 gap_level 은 null,
    #    confidence 는 MEDIUM 인 — 표에 없는 상태를 보내왔다. 그 상태로 저장되면
    #    "못 봤는데 꽤 믿을 만하다"가 되어 종합·루틴이 그 부위를 근거로 삼는다.
    confidence = _enum_or_none(item.get("confidence"), Confidence)
    if gap_level is None:
        confidence = str(Confidence.LOW)
    if confidence is None:
        # 확신도를 못 읽었다고 진단을 버리진 않는다. 안전한 쪽(LOW)으로 둔다 —
        # LOW 는 종합 진단에서 가중치가 낮아지므로 과신 방향의 오류가 없다.
        confidence = str(Confidence.LOW)

    # ⚠️ **등급은 매겼는데 시각적 근거(differences)가 없으면 확신도를 한 단계 내린다**
    #    (2026-08-20 실측). 프롬프트가 "등급을 매겼으면 근거를 함께 적으세요"를
    #    요구하는데 모델이 자주 어긴다 — 팔 4부위가 근거 없이 같은 문장 하나로
    #    등급만 받은 사례가 반복됐다. 근거를 못 댄 판정을 «확실히 봤다»와 같은
    #    무게로 점수에 넣으면 그 점수의 출처를 되짚을 수 없다.
    #    ⚠️ 버리지는 않는다 — 판정 자체가 틀렸다는 뜻은 아니고, 검증 가능성이
    #       낮을 뿐이다. scoring 의 확신도 가중이 알아서 비중을 줄인다.
    if gap_level is not None and not differences and confidence != str(Confidence.LOW):
        downgraded = str(Confidence.MEDIUM if confidence == str(Confidence.HIGH) else Confidence.LOW)
        log.info(
            "%s: 등급 %s 인데 differences 가 비어 근거 없음 — 확신도 %s → %s",
            class_name,
            gap_level,
            confidence,
            downgraded,
        )
        confidence = downgraded

    priority = item.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 5:
        priority = None

    return {
        "class_name": class_name,
        "differences": differences,
        "assessment": assessment,
        "gap_level": gap_level,
        "priority": priority,
        "confidence": confidence,
        "blocked_reason": blocked,
    }


#: 한쪽만 가리키는 말. 이게 들어있으면 반대쪽 카드에 복사할 수 없다.
_SIDE_WORDS = ("왼", "오른", "좌측", "우측")


def _unify_pairs(results: dict[str, dict[str, Any]]) -> None:
    """좌우 쌍의 **우선순위만** 맞춘다 (제자리 수정). 문장은 손대지 않는다.

    ⚠️ **2026-08-20 개정 — 문장 통일을 걷어냈다.** 종전에는 좌우 쌍의
       assessment 를 한쪽 문장으로 덮어썼다. 그 규칙 때문에 프롬프트도 «양쪽»을
       주어로 쓰라고 요구했는데, 부작용이 실측으로 드러났다:

         · 오른쪽 상완 카드가 **왼팔 인바디 수치**("왼팔 근육량이 평균의 …%")를
           그대로 달고 나갔다. 인바디는 좌우가 다른 값인데 문장만 복사된 것이다.
         · 사용자는 «왼쪽 상완» 카드 하나를 눌러 보고 있는데 "양쪽 상완은…"
           이라고 하니 지금 무엇을 보고 있는지가 흐려졌다.

       좌우를 묶어 부르는 것은 **종합 진단의 몫**이다 (거기서 한 번만 «양쪽»
       으로 합친다 — 화면 헤드라인도 그 값을 쓴다). 부위 카드는 자기 부위를 말한다.

    ⚠️ priority 는 계속 맞춘다 — 우선순위는 «어느 부위부터 볼까»의 순서라
       좌우가 같은 부위면 같은 순위인 게 맞고, 루틴 볼륨 가중도 그렇게 읽는다.
       gap_level 이 서로 다르면(좌우가 실제로 다른 경우) 손대지 않는다.
    """
    for left, part in list(results.items()):
        if not left.startswith("Left_"):
            continue
        other = results.get("Right_" + left[len("Left_") :])
        if other is None:
            continue
        if part.get("gap_level") != other.get("gap_level"):
            continue  # 좌우가 실제로 다르게 판정됐다. 그대로 둔다.

        # 더 앞선(작은) 순위로 맞춘다 — 한쪽만 뒤로 밀리면 루틴 가중이 갈린다.
        picks = [x.get("priority") for x in (part, other) if isinstance(x.get("priority"), int)]
        if picks:
            best = min(picks)
            part["priority"] = other["priority"] = best


def parse_part_response(
    parsed: dict[str, Any], class_names: list[str], inbody_available: bool = True
) -> dict[str, Any]:
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
        part = _coerce_part(item, allowed, inbody_available)
        if part is None:
            log.warning("부위 항목을 검증하지 못했습니다: %.200s", item)
            continue
        # 같은 부위를 두 번 보내오면 처음 것만 쓴다 (UNIQUE(session_id, class_name)).
        results.setdefault(part["class_name"], part)

    _unify_pairs(results)

    return {
        "results": [results[n] for n in class_names if n in results],
        "missing": [n for n in class_names if n not in results],
    }


def _confidence_ratio(value: Any) -> float | None:
    """종합 판단의 확신도 0.0~1.0. 못 읽으면 None (없는 것과 못 읽은 것을 구분하지 않는다).

    ⚠️ similarity_score 와 다른 값이다. 점수는 scoring 이 규칙으로 계산하고 LLM 이
       보낸 점수는 버리지만, 이 confidence 는 **LLM 만 알 수 있는 것**이라 받는다 —
       "내가 방금 쓴 판단을 얼마나 믿을 수 있나"는 규칙으로 계산할 수 없다.

    ⚠️ 0~100 으로 보내오면 100 으로 나눈다. 실측에서 모델이 곧잘 그렇게 답한다.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ratio = float(value)
    if ratio > 1.0:
        ratio = ratio / 100.0
    if not 0.0 <= ratio <= 1.0:
        return None
    return round(ratio, 2)


def _profile(value: Any) -> dict[str, Any] | None:
    """{summary, characteristics[]} 한 덩어리. 못 읽으면 None.

    ⚠️ characteristics 는 3개로 자른다 — 프롬프트도 2~3개로 지시하지만 지시는
       어겨질 수 있고, 길어지면 «비교문»이 섞여 들어온다(이 필드의 실패 형태).
    """
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
    items = _as_str_list(value.get("characteristics"))[:3]
    if summary is None and not items:
        return None
    return {"summary": summary, "characteristics": items}


def parse_overall_response(parsed: dict[str, Any]) -> dict[str, Any]:
    """종합 진단 응답 검증 — **설명 문장만** 받는다.

    ⚠️ 구조적 판단은 전부 코드가 한다. LLM 이 보내와도 버린다:
         similarity_score → scoring.compute_similarity()  (score_source=RULE)
         priority_parts   → scoring.rank_priority()
       LLM 의 몫은 "이미지에서 무엇이 관찰되는가"를 문장으로 만드는 것뿐이다.
    """

    summary = parsed.get("summary")
    silhouette = parsed.get("silhouette")

    return {
        "summary": summary.strip() if isinstance(summary, str) else None,
        "strengths": _as_str_list(parsed.get("strengths")),
        "cautions": _as_str_list(parsed.get("cautions")),
        # ── 사진을 직접 보고 낸 전체 형태 판단 (2026-08-17) ──
        "silhouette": silhouette.strip() if isinstance(silhouette, str) else None,
        # ⚠️ 2개까지만. 프롬프트도 1~2개로 지시하지만 지시는 어겨질 수 있고,
        #    많아지면 "부위 카드 복사"가 섞여 들어온다 (그게 이 필드의 실패 형태다).
        #    화면 요약 상자가 고정 폭이라 길이도 함께 눌러야 한다.
        "key_differences": _as_str_list(parsed.get("key_differences"))[:2],
        "confidence": _confidence_ratio(parsed.get("confidence")),
        # ── 전체 프로필 A·B (2026-08-17) ──
        "user_profile": _profile(parsed.get("user_profile")),
        "reference_profile": _profile(parsed.get("reference_profile")),
        # ── 방향·전략은 **설명 문장만** 받는다 ──
        # ⚠️ priority·mode 는 파싱하지 않는다. 규칙(scoring.decide_direction /
        #    routine_mode.decide_mode)이 정하고 호출자가 저장한다. LLM 이
        #    보내와도 버린다 — 점수·우선순위와 같은 원칙이다.
        "direction_summary": (
            parsed["direction_summary"].strip()
            if isinstance(parsed.get("direction_summary"), str)
            and parsed["direction_summary"].strip()
            else None
        ),
        "strategy_focus": _as_str_list(parsed.get("strategy_focus"))[:2],
        "next_cycle": (
            parsed["next_cycle"].strip()
            if isinstance(parsed.get("next_cycle"), str) and parsed["next_cycle"].strip()
            else None
        ),
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
    out = parse_part_response(parsed, class_names, inbody_available=inbody is not None)
    out["raw_response"] = raw
    return out


async def compare_parts_direct(
    reference_photo: bytes,
    user_photo: bytes,
    parts: list[dict[str, Any]],
    inbody: dict[str, Any] | None,
) -> dict[str, Any]:
    """F08-direct — **세그멘테이션 없이** 원본 2장으로 부위별 비교 진단.

    diagnose_parts 와의 유일한 차이는 **입력**이다:

        diagnose_parts        원본2 + 오버레이2 (4장) + 세그 수치
        compare_parts_direct  원본2 (2장)

    출력·검증은 완전히 같다 (parse_part_response 재사용). 그래서 저장 경로도,
    종합 진단(F09)도, 화면도 두 경로를 구분하지 않는다 — 부위 카드가 어떻게
    만들어졌는지는 화면의 관심사가 아니다.

    Args:
        parts: body_part 마스터 행. ⚠️ 부위 목록은 DB 가 유일한 출처다.

    Returns:
        {"results": [...], "missing": [class_name...], "raw_response": {...}}
    """
    class_names = [p["class_name"] for p in parts]

    if settings.use_mock:
        return _mock_parts(class_names)

    text = build_part_comparison_prompt(parts=parts, inbody=inbody)

    # ⚠️ 이미지 순서가 프롬프트의 설명 순서와 같아야 한다 (레퍼런스 → 사용자).
    content = [
        _image_block(reference_photo),
        _image_block(user_photo),
        {"type": "text", "text": text},
    ]

    parsed, raw = await _call_json(PART_COMPARISON_SYSTEM, content, PART_MAX_TOKENS)
    out = parse_part_response(parsed, class_names, inbody_available=inbody is not None)
    out["raw_response"] = raw
    return out


async def diagnose_overall(
    results: list[dict[str, Any]],
    failed: list[str],
    inbody: dict[str, Any] | None,
    score: int | None = None,
    excluded: list[str] | None = None,
    reference_photo: bytes | None = None,
    user_photo: bytes | None = None,
    priority_parts: list[str] | None = None,
    direction: dict[str, Any] | None = None,
    cut_notice: str | None = None,
    shares: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """F09 — 원본 두 장을 직접 보고 종합한다.

    2026-08-17 변경: 텍스트 전용이었으나 원본 사진을 함께 받는다. 부위 9개를 각각
    정확히 봐도 **부위 사이의 관계**(상하체 균형, 어깨-허리 폭 비율)는 어느 카드에도
    안 나오기 때문이다 — 등급의 합계에는 비율이 없다.

    Args:
        results: F08 에서 저장에 성공한 부위 진단 전체 (판단 불가 포함)
        failed:  기술적으로 실패한 부위 이름
        score:   scoring.compute_similarity() 결과. 프롬프트에 주입해 summary 가
                 점수와 모순되지 않게 한다. LLM 출력의 점수는 어차피 버린다.
        reference_photo / user_photo:
                 **이미 전처리된** JPEG (segmap.fit_for_vlm → encode_jpeg).
                 여기서 다시 리사이즈하지 않는다 — 호출자가 F08 과 같은 헬퍼로
                 만든 것을 그대로 넘긴다.
                 ⚠️ 둘 중 하나라도 없으면 **텍스트 전용으로 되돌아간다.** 사진을
                    못 구했다고 종합을 실패시키면, 부위 진단은 멀쩡한데 화면에
                    요약이 통째로 비는 상태가 된다.

    Returns:
        {summary, priority_parts, strengths, cautions,
         silhouette, key_differences, confidence, raw_response}
    """
    judged = [p for p in results if p.get("gap_level")]
    blocked = [p for p in results if not p.get("gap_level")]

    if settings.use_mock:
        return _mock_overall(judged or results)

    has_images = reference_photo is not None and user_photo is not None

    text = build_overall_prompt(
        parts=judged,
        blocked=blocked,
        failed=failed,
        inbody=inbody,
        score=score,
        excluded=excluded,
        has_images=has_images,
        priority_parts=priority_parts,
        direction=direction,
        cut_notice=cut_notice,
        shares=shares,
    )

    # ⚠️ 이미지 순서가 프롬프트 §사진 의 설명 순서와 같아야 한다 (레퍼런스 → 사용자).
    #    어긋나면 종합이 정반대로 나온다. F08 과 같은 규칙이다.
    content: list[dict[str, Any]] = []
    if has_images:
        content += [_image_block(reference_photo), _image_block(user_photo)]  # type: ignore[arg-type]
    content.append({"type": "text", "text": text})

    parsed, raw = await _call_json(OVERALL_SYSTEM, content, OVERALL_MAX_TOKENS)

    # ⚠️ 우선 부위 후보는 **판단된 부위만**이다. 판단 불가 부위가 여기 들어가면
    #    루틴의 볼륨 가중(L2)이 "못 본 부위"에 실린다 — 실측(2026-08-17)에서
    #    허벅지 2개가 gap_level=null 인 채 priority 3 을 달고 있었다.
    out = parse_overall_response(parsed)
    out["raw_response"] = raw
    return out
