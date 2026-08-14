"""유사도 점수 — 결정론적 규칙 합산 (score_source = RULE).

━━ 왜 VLM 직접 출력을 폐기했는가 (2026-08-14) ━━

"68점이 왜 68점인가요?"에 "AI가 그렇게 냈습니다"는 답이 아니다.
VLM 점수는 ① 같은 입력에도 호출마다 흔들리고 ② 근거를 사후 서술로만 남기며
③ 부위 진단과 점수가 모순돼도(격차 SIGNIFICANT 4개인데 90점) 막을 수 없다.

그래서 점수를 둘로 쪼갠다:

    부위별 격차 판정  → VLM (시각 정보라 모델만 할 수 있음)
    격차 → 점수 합산  → 코드 (규칙이라 모델에게 시킬 이유가 없음)

VLM 이 잘하는 판단은 남기고, 산수는 회수한다. F10 이 분할(코드)과
운동 선택(LLM)을 나눈 것과 같은 원칙이다.

━━ 공식 ━━

    part_score  = 100 − 25 × ordinal(gap_level)     # NONE 100 · SLIGHT 75
                                                    # MODERATE 50 · SIGNIFICANT 25
    similarity  = round(mean(part_scores))          # 판단된 부위만, 등가중

방어 근거:
  * **등급당 25점** — gap_level 은 4단계 등간 서열 척도다. 100점 척도에
    선형 사상하면 등급 간격이 같아야 하고, 그게 25다. 임의 튜닝값이 아니다.
  * **SIGNIFICANT 가 0이 아닌 25인 이유** — 이 점수는 "격차 크기"의 사상이지
    유사성의 존재 여부가 아니다. 포즈 정합을 통과한 같은 인체 정면샷이므로
    최하 등급에도 구조적 유사성은 남는다. 0점은 "비교 불능"에게만 가능한데,
    비교 불능 부위는 애초에 점수에 들어오지 않는다.
  * **부위 등가중** — 부위별 중요도 가중치는 근거 없는 임의 상수다
    (clothing_ratio > 0.5 를 폐기한 것과 같은 이유). 중요도는 점수가 아니라
    priority_parts 로 따로 전달된다.
  * **판단 불가는 분모에서 제외** — 모르는 것을 감점하면 옷을 입었다는 이유로
    점수가 깎인다. 대신 coverage 를 rationale 에 명시해 "몇 부위로 낸 점수"인지
    숨기지 않는다.

같은 진단이면 언제나 같은 점수다. 재현 가능하고, 부위 카드와 모순될 수 없다
(부위 카드가 곧 입력이므로).
"""

from typing import Any

#: gap_level → 서열 (0 이 목표 체형과 일치). DB CHECK 와 같은 4단계.
_GAP_ORDINAL: dict[str, int] = {
    "NONE": 0,
    "SLIGHT": 1,
    "MODERATE": 2,
    "SIGNIFICANT": 3,
}

_STEP = 25  # 100점 척도 ÷ (등급 수 − 1) 이 아니라, 등급당 등간 간격. 100−25×3 = 25 가 바닥.

#: rationale 에 쓰는 한국어 등급명 (사용자·심사위원에게 그대로 보여도 되는 말)
_GAP_KO = {"NONE": "일치", "SLIGHT": "근소", "MODERATE": "보통", "SIGNIFICANT": "큰 격차"}


def compute_similarity(parts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """판단된 부위 진단들로 유사도 점수를 낸다.

    Args:
        parts: gap_level 이 있는 part_diagnosis 행들 (판단 불가·실패는 호출자가 거른다.
               섞여 들어와도 여기서 다시 거른다 — 이중 방어).

    Returns:
        {"score", "rationale", "breakdown"}  또는 판단 부위가 없으면 None.
        rationale 은 overall_diagnosis.score_rationale 컬럼에 그대로 저장한다.
    """
    judged = [p for p in parts if p.get("gap_level") in _GAP_ORDINAL]
    if not judged:
        return None

    scores = [100 - _STEP * _GAP_ORDINAL[p["gap_level"]] for p in judged]
    score = round(sum(scores) / len(scores))

    counts: dict[str, int] = {}
    for p in judged:
        counts[p["gap_level"]] = counts.get(p["gap_level"], 0) + 1

    dist = " · ".join(f"{_GAP_KO[g]} {counts[g]}" for g in _GAP_ORDINAL if g in counts)
    rationale = f"판단된 {len(judged)}개 부위의 격차 등급을 등간 사상(등급당 25점)한 평균 — {dist}"

    return {
        "score": score,
        "rationale": rationale,
        "breakdown": {
            "formula": "100 - 25*ordinal(gap_level), mean over judged parts",
            "judged": len(judged),
            "counts": counts,
            "per_part": {
                p["class_name"]: 100 - _STEP * _GAP_ORDINAL[p["gap_level"]] for p in judged
            },
        },
    }
