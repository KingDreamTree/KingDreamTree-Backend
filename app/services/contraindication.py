"""금기 강제 — 통증이 보고됐는데 루틴이 그대로 나가는 것을 코드가 막는다.

━━ 왜 코드가 또 하는가 (LLM 이 이미 지시받았는데) ━━

F12 프롬프트는 "통증 부위에 부담을 주는 운동은 replace_exercise 로 바꿔라"고
지시한다. 하지만 그건 **지시일 뿐 보장이 아니다.** LLM 이 금기만 등록하고
운동은 안 건드리는 경우가 실제로 나오고, 그러면 이런 상태가 된다:

    사용자: "스쿼트 할 때 무릎이 아팠어요"
    시스템: 무릎 → 주의 부위 등록 ✓
    루틴  : 스쿼트 4세트 그대로 ✗      ← 조용히 샌다

금기가 **기록만 되고 운동에 반영되지 않는** 것은 안전에 직결되고, 화면에는
"주의 부위 등록됨" 배지가 떠서 처리된 것처럼 보인다. 그래서 더 위험하다.

이 모듈은 LLM 이 놓친 부분만 **사후에** 메꾼다. 진단·선택 로직과 같은 원칙이다 —
판단은 LLM 이, 보장은 코드가.

━━ 무엇을 하고, 무엇을 하지 않는가 ━━

    WARN   → 해당 근육군 운동 세트 -1 (최소 1)     볼륨을 줄여 부하를 낮춘다
    BLOCK  → 해당 근육군 운동 제거                  그 부위를 아예 쓰지 않는다

⚠️ **대체 운동을 고르지 않는다.** "이 운동이 무릎에 더 안전하다"는 판단은
   근거 있는 주장이 필요한데 카탈로그에는 그 정보가 없다. 대체는 LLM 이 후보
   목록을 보고 하는 일이고, 여기는 그게 실패했을 때의 바닥이다. 바닥은
   **덜 하게 만드는 것**까지만 한다 — 없는 근거로 종목을 갈아끼우지 않는다.

⚠️ **Day 를 비우지 않는다.** BLOCK 이라도 그 Day 운동이 전부 해당 근육군이면
   (예: 하체 Day + 무릎 BLOCK) 하나는 1세트로 남긴다. 빈 Day 가 저장되면
   화면에 아무것도 없는 날이 뜨고, 사용자는 이유를 알 수 없다.

⚠️ **유산소는 건드리지 않는다.** sets 가 없고 duration_min 으로 처방되며,
   CUT 모드에서 감량의 핵심이라 통증 부위와 무관하게 유지하는 편이 낫다.
   (달리기가 무릎에 부담이라는 지적은 타당하지만, 그 판단은 종목별이라
   위 "대체를 고르지 않는다"와 같은 이유로 여기서 하지 않는다.)
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.enums import ExerciseKind

log = logging.getLogger("services.contraindication")

#: 관절·부위 → 그 관절에 부하가 실리는 근육군.
#:
#: ⚠️ 정밀한 생체역학 매핑이 아니라 **보수적으로 넓게** 잡은 것이다. 놓치는 것보다
#:    과하게 줄이는 쪽이 안전하고, 과하게 줄어도 사용자는 다음 피드백에서 되돌릴 수
#:    있다. 반대 방향(놓침)은 되돌릴 기회가 통증이 온 뒤에 온다.
#:
#: ⚠️ 근육군 이름은 routine_templates 의 슬롯 이름과 정확히 같아야 한다.
#:    오타가 나면 조용히 아무것도 매칭되지 않는다 — verify 가 이걸 검사한다.
JOINT_GROUPS: dict[str, frozenset[str]] = {
    "무릎": frozenset({"대퇴사두", "햄스트링·둔근", "종아리"}),
    "발목": frozenset({"종아리", "대퇴사두", "햄스트링·둔근"}),
    "고관절": frozenset({"햄스트링·둔근", "대퇴사두"}),
    "허리": frozenset({"등", "햄스트링·둔근", "코어", "대퇴사두"}),
    "어깨": frozenset({"가슴", "어깨", "후면 어깨", "삼두", "등"}),
    "팔꿈치": frozenset({"삼두", "이두", "가슴", "등", "팔"}),
    "손목": frozenset({"가슴", "삼두", "이두", "어깨", "팔"}),
    "목": frozenset({"어깨", "후면 어깨", "등"}),
}

#: 근육 이름 → 제한할 근육군.
#:
#: ⚠️ **2026-08-18 추가 — 이게 없어서 근육 통증이 통째로 무시되고 있었다.**
#:    실사용 사례: "가슴이 아프다" → LLM 은 flag_contraindication(body_part="가슴")
#:    을 정상 호출했는데, JOINT_GROUPS 에는 관절 이름(무릎·어깨·허리…)만 있어
#:    normalize_part("가슴") 이 None 을 돌려주고 **조용히 버려졌다.** 다음 날
#:    루틴에 가슴 운동이 그대로 남았다 — 이 모듈이 막으려던 바로 그 누수다.
#:
#: ⚠️ 관절과 달리 **그 근육군만** 제한한다. 관절은 여러 근육군이 함께 부하를
#:    받으므로 넓게 잡지만(JOINT_GROUPS 주석), 근육통은 그 근육이 원인이라
#:    넓히면 "빈 Day 를 만들지 않는다" 규칙과 충돌할 만큼 과하게 깎인다.
#:    예외는 허벅지처럼 앞뒤를 구분하지 않고 말하는 부위뿐이다.
MUSCLE_GROUPS: dict[str, frozenset[str]] = {
    "가슴": frozenset({"가슴"}),
    "등": frozenset({"등"}),
    "삼두": frozenset({"삼두"}),
    "이두": frozenset({"이두"}),
    "대퇴사두": frozenset({"대퇴사두"}),
    "햄스트링·둔근": frozenset({"햄스트링·둔근"}),
    "종아리": frozenset({"종아리"}),
    "코어": frozenset({"코어"}),
    # 앞뒤를 구분하지 않고 말하는 부위 — 어느 쪽인지 모르므로 둘 다 줄인다.
    "허벅지": frozenset({"대퇴사두", "햄스트링·둔근"}),
}

#: 조회용 통합표. 관절이 먼저다 — 이름이 겹치면(예: "어깨") 관절의 넓은 규칙을 쓴다.
PART_GROUPS: dict[str, frozenset[str]] = {**MUSCLE_GROUPS, **JOINT_GROUPS}

#: 매핑에 없는 부위명이 왔을 때 쓸 별칭. LLM 이 자유 문자열로 주므로 흔한 변형을 흡수한다.
_ALIASES: dict[str, str] = {
    "무릎관절": "무릎",
    "왼무릎": "무릎",
    "오른무릎": "무릎",
    "양무릎": "무릎",
    "허리디스크": "허리",
    "요추": "허리",
    "척추": "허리",
    "어깨관절": "어깨",
    "회전근개": "어깨",
    "왼어깨": "어깨",
    "오른어깨": "어깨",
    "손목관절": "손목",
    "팔꿈치관절": "팔꿈치",
    "엘보": "팔꿈치",
    "골반": "고관절",
    "엉덩관절": "고관절",
    # ⚠️ "발등"은 "등"을 포함한다 — 별칭으로 먼저 잡지 않으면 등(광배) 운동이 막힌다.
    "발등": "발목",
    # 근육 별칭 (2026-08-18)
    "대흉근": "가슴",
    "흉근": "가슴",
    "가슴근육": "가슴",
    "광배근": "등",
    "승모근": "등",
    "등근육": "등",
    "삼두근": "삼두",
    "상완삼두근": "삼두",
    "이두근": "이두",
    "상완이두근": "이두",
    "둔근": "햄스트링·둔근",
    "엉덩이": "햄스트링·둔근",
    "햄스트링": "햄스트링·둔근",
    "허벅지앞": "대퇴사두",
    "허벅지뒤": "햄스트링·둔근",
    "장딴지": "종아리",
    "복근": "코어",
    "배": "코어",
}

_BLOCK, _WARN = "BLOCK", "WARN"


def normalize_part(body_part: str) -> str | None:
    """자유 문자열 부위명 → JOINT_GROUPS 키. 모르면 None."""
    text = (body_part or "").strip()
    if not text:
        return None
    if text in PART_GROUPS:
        return text
    if text in _ALIASES:
        return _ALIASES[text]
    # "왼쪽 무릎이" 처럼 수식어가 붙은 경우 — 키를 포함하면 그것으로 본다.
    # ⚠️ **긴 키부터** 본다. "등"(1글자)이 "발등"·"허벅지" 같은 말에 먼저 걸리면
    #    엉뚱한 근육군이 막힌다. 별칭에서 못 잡은 조합의 마지막 방어선이다.
    for key in sorted(PART_GROUPS, key=len, reverse=True):
        if key in text:
            return key
    return None


def affected_groups(contraindications: list[dict[str, Any]]) -> dict[str, str]:
    """금기 목록 → {근육군: 최고 심각도}. BLOCK 이 WARN 을 이긴다."""
    out: dict[str, str] = {}
    for c in contraindications or []:
        part = normalize_part(str(c.get("body_part") or ""))
        if part is None:
            # ⚠️ INFO 가 아니라 WARNING 이다. 여기로 빠지면 통증이 기록만 되고
            #    루틴은 그대로 나간다 — 모듈 주석이 말하는 "조용히 새는" 경로다.
            #    로그에 이게 쌓이면 PART_GROUPS/_ALIASES 에 그 이름을 추가할 것.
            log.warning(
                "금기 부위를 매핑하지 못해 **루틴에 반영되지 않았습니다**: %r "
                "— PART_GROUPS 에 추가가 필요합니다",
                c.get("body_part"),
            )
            continue
        severity = _BLOCK if str(c.get("severity")) == _BLOCK else _WARN
        for group in PART_GROUPS[part]:
            if out.get(group) != _BLOCK:
                out[group] = severity
    return out


def _llm_touched(applied: list[dict[str, Any]]) -> set[tuple[int, str]]:
    """LLM 이 이미 손댄 (day_order, 운동명) 집합. 여기는 건드리지 않는다."""
    touched: set[tuple[int, str]] = set()
    for change in applied or []:
        args = change.get("args") or {}
        order = args.get("day_order")
        if order is None:
            continue
        name = args.get("exercise_name") or args.get("old_exercise_name")
        if name:
            touched.add((int(order), str(name)))
    return touched


def enforce(
    days: list[dict[str, Any]],
    contraindications: list[dict[str, Any]],
    applied: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """금기를 루틴에 강제 반영한 사본을 돌려준다.

    Args:
        days: apply_changes_to_days 를 이미 통과한 Day 목록
        contraindications: 세션 누적 금기 (이번 증분 포함)
        applied: LLM 변경 기록 — 여기 있는 운동은 이미 처리됐다고 보고 건드리지 않는다

    Returns:
        (새 days, 강제 적용 기록) — 기록은 routine_revision.changes 에 함께 저장한다.
        강제할 것이 없으면 (원본 그대로, [])
    """
    groups = affected_groups(contraindications)
    if not groups:
        return days, []

    touched = _llm_touched(applied or [])
    out = [dict(d, exercises=[dict(e) for e in d.get("exercises", [])]) for d in days]
    enforced: list[dict[str, Any]] = []

    for day in out:
        order = day.get("day_order")
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []

        for ex in day.get("exercises", []):
            group = ex.get("muscle_group") or ""
            severity = groups.get(group)
            is_cardio = (ex.get("exercise_kind") or ex.get("kind")) == ExerciseKind.CARDIO

            if severity is None or is_cardio or (order, ex.get("name")) in touched:
                kept.append(ex)
                continue

            if severity == _BLOCK:
                dropped.append(ex)
                continue

            if ex.get("sets"):
                before = ex["sets"]
                ex["sets"] = max(1, before - 1)
                if ex["sets"] != before:
                    enforced.append(
                        {
                            "function": "enforce_contraindication",
                            "args": {
                                "day_order": order,
                                "exercise_name": ex.get("name"),
                                "muscle_group": group,
                                "severity": _WARN,
                                "sets_before": before,
                                "sets_after": ex["sets"],
                                "reason": f"{group} 은 주의 부위와 관련돼 볼륨을 낮췄습니다.",
                            },
                        }
                    )
            kept.append(ex)

        # ⚠️ Day 를 비우지 않는다 — 전부 BLOCK 이면 하나는 1세트로 남긴다.
        if dropped and not kept:
            survivor = dropped.pop(0)
            if survivor.get("sets"):
                survivor["sets"] = 1
            enforced.append(
                {
                    "function": "enforce_contraindication",
                    "args": {
                        "day_order": order,
                        "exercise_name": survivor.get("name"),
                        "muscle_group": survivor.get("muscle_group"),
                        "severity": _BLOCK,
                        "sets_after": survivor.get("sets"),
                        "reason": "이 날 운동이 모두 주의 부위와 관련돼 최소량만 남겼습니다.",
                    },
                }
            )
            kept.append(survivor)

        for ex in dropped:
            enforced.append(
                {
                    "function": "enforce_contraindication",
                    "args": {
                        "day_order": order,
                        "exercise_name": ex.get("name"),
                        "muscle_group": ex.get("muscle_group"),
                        "severity": _BLOCK,
                        "removed": True,
                        "reason": f"{ex.get('muscle_group')} 은 통증 보고 부위라 제외했습니다.",
                    },
                }
            )

        day["exercises"] = kept

    if enforced:
        log.warning(
            "금기 강제 적용 %d건 — LLM 이 처리하지 않은 운동 (%s)",
            len(enforced),
            ", ".join(sorted(groups)),
        )
    return out, enforced
