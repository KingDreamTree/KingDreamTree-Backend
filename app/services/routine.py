"""루틴 생성·패치 서비스 (F10, F12).

F10 — build_routine(): 확정 로직 (2026-08-14 PM 결정 반영)

    [코드] L0  모드        routine_mode.decide_mode() — 체지방률 1차, BMI 폴백
    [코드] L1  골격        routine_templates.get_template(N, mode) — 주기당 N일
    [코드] L2  가중        apply_weakness_boost() — 약점 부위 +2~4세트/주
    [코드]     후보        exercise_catalog.candidates_for_slot() — 카탈로그 필터
    [LLM ]     선택        후보 중 exercise_ref 만 고름 (환각 구조적 차단)
    [코드]     검증·조립   후보 밖 응답 폐기→1순위 대체, 주간 중복 제한, RIR 부여

    ⚠️ LLM 호출은 1회, 그마저도 "무엇을 골라도 규칙 위반이 아닌" 집합 안이다.
       USE_MOCK 이거나 LLM 이 실패하면 **결정론적 선택**(후보 순서 라운드로빈)으로
       완주한다 — 루틴 생성은 어떤 조합에서도 실패하지 않는다 (D10 취지).

F12 — patch_routine(): 피드백 기반 루틴 패치 (Function Calling) — 기존 유지
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings
from app.prompts.routine_gen import SYSTEM_PROMPT as SELECTION_SYSTEM
from app.prompts.routine_gen import build_selection_prompt
from app.prompts.routine_patch import SYSTEM_PROMPT, TOOLS
from app.services import exercise_catalog, routine_mode
from app.services.routine_templates import (
    CUT_NOTICE,
    SEVEN_DAY_NOTICE,
    DayPlan,
    apply_weakness_boost,
    get_template,
    weekly_sets_by_group,
)

log = logging.getLogger("services.routine")

#: 같은 운동을 주간에 허용하는 최대 횟수. 초과 선택은 코드가 다른 후보로 바꾼다.
#:
#: ⚠️ 숙련도로 분기하지 않는다 — **서비스 타깃이 운동 초보자로 고정**이라
#:    training_experience 입력 자체를 도입하지 않기로 했다 (2026-08-14).
#:    "초보는 기본 종목을 반복해야 자세를 익힌다"는 지적은 타당하지만, 그건
#:    **종목 수를 줄이라**는 뜻이지 상한을 풀라는 뜻이 아니다. 후보 정렬이 이미
#:    복합·머신·덤벨을 1순위로 올려 기본 종목이 반복 선택되는 구조이고,
#:    상한을 풀면 LLM 이 한 종목만 6번 뽑아 주간 자극이 한쪽으로 몰린다.
MAX_WEEKLY_REPEAT = 2

#: RIR 처방값 — "이만큼 남기고 멈추는" 횟수. Zourdos 2016 척도 기준 초보 권장 구간.
DEFAULT_RIR = 2

DISCLAIMER = "본 루틴은 의학적 조언이 아닙니다. 통증이 있으면 중단하고 전문가와 상담하세요."


def _rir_note(reps: int) -> str:
    return f"{reps}회를 마쳤을 때 {DEFAULT_RIR}회 정도 여유가 남는 무게로 하세요."


#: 통증 신호. mock 이 "피드백을 읽은 척"이라도 하려면 최소한 이건 반응해야 한다.
#:
#: ⚠️ **한국어 활용형을 다 적어야 한다.** "아파"만 넣었다가 "무릎이 아팠어요"를
#:    놓쳤다 — 통증 피드백인데 금기가 안 잡히는, 안전에 직결되는 누락이었다.
#:    (실제 LLM 경로는 문맥으로 잡지만 mock 은 이 목록이 전부다)
_PAIN_TERMS = (
    "아파",
    "아팠",
    "아프",
    "아픈",
    "아픔",
    "통증",
    "불편",
    "시큰",
    "쑤시",
    "결려",
    "삐끗",
)


def _mock_patch(
    feedback_text: str, current_routine: dict[str, Any] | None = None
) -> dict[str, Any]:
    """USE_MOCK 용 패치 결과.

    ⚠️ 고정 상수를 돌려주지 않는다. 통증 피드백인데 금기가 안 잡히는 사고를
       mock 경로에서도 잡아야 하기 때문이다 — 전 구간 스모크가 mock 으로 도는데
       거기서 늘 같은 값이 나오면 그 검사는 아무것도 검증하지 못한다.
    """
    text = (feedback_text or "").strip()
    if not text:
        return {
            "interpretation": "변경 사항 없음",
            "changes": [],
            "contraindications_added": [],
            "raw_response": {"mock": True},
        }

    has_pain = any(term in text for term in _PAIN_TERMS)

    # ⚠️ 실제 루틴의 Day·운동 이름을 써야 검증(validate_tool_call)을 통과한다.
    #    고정 문자열을 쓰면 mock 경로에서만 조용히 거부돼 적용이 0건이 된다.
    day = (current_routine or {}).get("days", [{}])[0] if current_routine else {}
    target = next((e for e in day.get("exercises", []) if e.get("exercise_kind") != "CARDIO"), None)
    changes: list[dict[str, Any]] = []
    if target:
        changes.append(
            {
                "function": "adjust_intensity",
                "args": {
                    "day_order": day.get("day_order", 1),
                    "exercise_name": target["name"],
                    "sets_delta": -1,
                    "reps_delta": 0,
                    "reason": "사용자 피드백 반영 (mock)",
                },
            }
        )
    added: list[dict[str, Any]] = []
    if has_pain:
        changes.append(
            {
                "function": "flag_contraindication",
                "args": {"body_part": "무릎", "severity": "WARN", "reason": text[:80]},
            }
        )
        added.append({"body_part": "무릎", "severity": "WARN"})

    return {
        "interpretation": (
            "통증 신호를 확인해 금기로 등록했습니다. (mock)"
            if has_pain
            else "피드백을 반영해 강도를 조정했습니다. (mock)"
        ),
        "llm_message": None,  # 핸들러가 적용 결과로 다시 요약하게 한다
        "changes": changes,
        "contraindications_added": added,
        "raw_response": {"mock": True},
    }


# --------------------------------------------------------------------------- #
# 운동 선택 — LLM(후보 제약) + 결정론 폴백
# --------------------------------------------------------------------------- #


def _slot_id(day: DayPlan, index: int) -> str:
    return f"d{day['day_order']}s{index}"


def _collect_candidates(
    days: list[DayPlan],
    catalog: list[dict[str, Any]],
    single_side_parts: set[str],
    *,
    low_impact: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """슬롯별 후보 목록. 여기 없는 운동은 최종 루틴에 들어갈 수 없다.

    Args:
        low_impact: CUT(=체지방률 컷오프 초과) 일 때 True. 착지 충격이 큰
            동작을 뒤로 민다 — 체중이 무거울수록 관절 반력이 커진다.
    """
    groups_of_asym = {
        g for p in single_side_parts for g in exercise_catalog.PART_TO_SLOTS.get(p, ())
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for day in days:
        for idx, slot in enumerate(day["slots"]):
            if slot.get("kind") == "CARDIO":
                continue
            prefer_single = slot["muscle_group"] in groups_of_asym
            if prefer_single:
                slot["single_side"] = True  # 프롬프트 태그용
            out[_slot_id(day, idx)] = exercise_catalog.candidates_for_slot(
                slot["muscle_group"],
                catalog,
                prefer_single_side=prefer_single,
                prefer_low_impact=low_impact,
            )
    return out


def _fallback_selections(
    days: list[DayPlan],
    candidates: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    """결정론적 선택 — 후보 순서대로, 주간 사용 횟수가 적은 것을 먼저.

    mock 모드와 LLM 실패 시의 최후 보루다. 정렬이 이미
    장비→복합→단측 순이므로 1순위 후보도 충분히 합리적이다.
    """
    used: dict[str, int] = {}
    selections: dict[str, str] = {}
    for day in days:
        picked_today: set[str] = set()
        for idx, slot in enumerate(day["slots"]):
            if slot.get("kind") == "CARDIO":
                continue
            sid = _slot_id(day, idx)
            pool = candidates.get(sid, [])
            if not pool:
                continue
            fresh = [c for c in pool if c["exercise_ref"] not in picked_today]
            if not fresh:
                fresh = pool
            best = min(
                fresh,
                key=lambda c: (used.get(c["exercise_ref"], 0), pool.index(c)),
            )
            ref = best["exercise_ref"]
            selections[sid] = ref
            used[ref] = used.get(ref, 0) + 1
            picked_today.add(ref)
    return selections


async def _llm_selections(
    days: list[DayPlan],
    candidates: dict[str, list[dict[str, Any]]],
    priority_parts: list[str],
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """LLM 에게 후보 중 선택만 시킨다. 실패하면 빈 dict (폴백이 채운다)."""
    from app.services.vlm import call_json  # 공용 JSON 호출 (provider 분기·temp=0)

    try:
        parsed, raw = await call_json(
            SELECTION_SYSTEM,
            [{"type": "text", "text": build_selection_prompt(days, candidates, priority_parts)}],
            max_tokens=1500,
        )
    except Exception as e:  # noqa: BLE001 — 선택 실패는 루틴 실패가 아니다
        log.warning("운동 선택 LLM 실패 — 결정론 폴백으로 진행: %s", e)
        return {}, None

    picks = parsed.get("selections")
    return (picks if isinstance(picks, dict) else {}), {
        "id": raw.get("id"),
        "model": raw.get("model"),
        "usage": raw.get("usage"),
    }


def _sanitize_selections(
    raw_picks: dict[str, str],
    days: list[DayPlan],
    candidates: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, str], int]:
    """LLM 선택 검증 — 후보 밖·중복 초과는 폐기하고 폴백 규칙으로 메꾼다.

    Returns: (확정 선택, 교정된 슬롯 수)
    """
    fallback = _fallback_selections(days, candidates)
    used: dict[str, int] = {}
    fixed = 0
    final: dict[str, str] = {}

    def _acceptable(ref: str | None, pool_refs: set[str], today: set[str]) -> bool:
        return (
            isinstance(ref, str)
            and ref in pool_refs
            and ref not in today
            and used.get(ref, 0) < MAX_WEEKLY_REPEAT
        )

    for day in days:
        picked_today: set[str] = set()
        for idx, slot in enumerate(day["slots"]):
            if slot.get("kind") == "CARDIO":
                continue
            sid = _slot_id(day, idx)
            pool = candidates.get(sid, [])
            allowed = {c["exercise_ref"] for c in pool}
            pick = raw_picks.get(sid)

            if not _acceptable(pick, allowed, picked_today):
                # ⚠️ 폴백표는 "LLM 선택이 하나도 없다"는 가정으로 미리 만든 것이라,
                #    LLM 선택과 **섞이는 순간 그 표의 중복 계산은 무효**가 된다.
                #    그대로 꽂으면 같은 Day 에 같은 운동이 두 번 들어가거나
                #    주간 상한을 넘는다 (퍼징으로 실제 재현됨). 그래서 폴백 값도
                #    지금 상태 기준으로 검사하고, 안 되면 남은 후보에서 다시 고른다.
                candidate_refs = [fallback.get(sid)] + [c["exercise_ref"] for c in pool]
                pick = next(
                    (r for r in candidate_refs if _acceptable(r, allowed, picked_today)),
                    None,
                )
                if pick is None:
                    # 주간 상한까지 모두 소진 — 상한만 풀어 Day 중복은 끝까지 막는다
                    pick = next(
                        (c["exercise_ref"] for c in pool if c["exercise_ref"] not in picked_today),
                        None,
                    )
                if pick is None:
                    continue
                if raw_picks:  # LLM 이 시도했는데 교정된 경우만 센다
                    fixed += 1

            final[sid] = pick
            used[pick] = used.get(pick, 0) + 1
            picked_today.add(pick)

    return final, fixed


# --------------------------------------------------------------------------- #
# Public API — F10
# --------------------------------------------------------------------------- #


async def build_routine(
    days_per_week: int,
    inbody: dict[str, Any] | None,
    priority_parts: list[str] | None = None,
    asymmetric_parts: list[str] | None = None,
) -> dict[str, Any]:
    """4주기 루틴을 조립한다 (주기당 N일 — 확정 모델).

    Args:
        days_per_week:    사용자가 고른 1~7 (N)
        inbody:           inbody 행 (없으면 None → BALANCE 확정)
        priority_parts:   F09 우선 개선 부위 (없으면 가중 없이 기본 볼륨만)
        asymmetric_parts: 좌우 차이가 큰 부위 — 해당 슬롯에서 단측 운동 우선

    Returns:
        {
          "mode", "mode_basis", "mode_reason", "days_per_week", "cycles",
          "goal", "focus_areas", "notice", "disclaimer",
          "boosts": {부위: +세트},          # 개인화 문구 근거
          "weekly_sets": {근육군: 세트},     # 검증·화면용
          "days": [ {day_order, title, exercises:[...]} ],
          "selection": {"source": "LLM"|"FALLBACK", "fixed": int, "meta": {...}},
        }
    """
    priority_parts = priority_parts or []
    asymmetric_parts = asymmetric_parts or []

    # L0 — 모드 (체지방률 1차, BMI 폴백, 인바디 없으면 BALANCE)
    mode_info = routine_mode.decide_mode(inbody)
    mode = str(mode_info["mode"])

    # L1 — 골격 (주기당 N일, CUT 이면 근력일마다 유산소 슬롯)
    #
    # ⚠️ 진단으로 Day 를 "구성"하지 않는다 (2026-08-14 정정). 5일차의 빈 "약점
    #    보완" Day 를 priority_parts 로 채우던 분기를 제거했다 — 진단이 Day 하나의
    #    구성 전체를 결정하는 구조라 D10(진단은 가중치, 포함/제외 아님)과 충돌했다.
    #    이제 5일차도 전신 골격이 깔리고, 개인화는 아래 L2 가중으로만 들어간다.
    days = get_template(days_per_week, mode)

    # L2 — 진단 가중 (진단 실패 부위는 여기 안 들어오고 기본 볼륨 유지 — D10)
    boosts = apply_weakness_boost(days, priority_parts, exercise_catalog.PART_TO_SLOTS)

    # 후보 수집 → 선택 (LLM 1회, 실패 시 결정론 폴백)
    catalog = exercise_catalog.load_catalog()
    candidates = _collect_candidates(
        days, catalog, set(asymmetric_parts), low_impact=(mode == "CUT")
    )

    llm_meta: dict[str, Any] | None = None
    if settings.use_mock:
        raw_picks: dict[str, str] = {}
    else:
        raw_picks, llm_meta = await _llm_selections(days, candidates, priority_parts)

    selections, fixed = _sanitize_selections(raw_picks, days, candidates)
    source = "LLM" if raw_picks else "FALLBACK"

    # 조립 — 유산소는 코드가 직접 고른다 (머신 우선 라운드로빈)
    cardio_pool = exercise_catalog.cardio_candidates(catalog, low_impact_only=(mode == "CUT"))
    cardio_i = 0
    by_ref = {c["exercise_ref"]: c for c in catalog}

    out_days: list[dict[str, Any]] = []
    for day in days:
        exercises: list[dict[str, Any]] = []
        for idx, slot in enumerate(day["slots"]):
            order = len(exercises) + 1
            if slot.get("kind") == "CARDIO":
                if not cardio_pool:
                    continue
                pick = cardio_pool[cardio_i % len(cardio_pool)]
                cardio_i += 1
                exercises.append(
                    {
                        "order_index": order,
                        "exercise_ref": pick["exercise_ref"],
                        "name": pick.get("name_ko") or pick["name_en"],
                        "image_url": pick.get("image_url"),
                        "kind": "CARDIO",
                        "duration_min": slot.get("duration_min", 15),
                        "note": "대화가 가능한 정도의 숨찬 속도로 하세요.",
                    }
                )
                continue

            ref = selections.get(_slot_id(day, idx))
            picked = by_ref.get(ref) if ref else None
            if picked is None:
                continue
            exercises.append(
                {
                    "order_index": order,
                    "exercise_ref": picked["exercise_ref"],
                    "name": picked.get("name_ko") or picked["name_en"],
                    "image_url": picked.get("image_url"),
                    "kind": "STRENGTH",
                    "muscle_group": slot["muscle_group"],
                    "sets": slot["sets"],
                    "reps": slot["reps"],
                    "rest_sec": slot["rest_sec"],
                    "rir": DEFAULT_RIR,
                    "note": _rir_note(slot["reps"]),
                    "boosted_by": slot.get("boosted_by"),
                }
            )
        out_days.append(
            {"day_order": day["day_order"], "title": day["title"], "exercises": exercises}
        )

    notice = SEVEN_DAY_NOTICE if days_per_week == 7 else None
    if mode == "CUT":
        notice = f"{CUT_NOTICE} {notice}" if notice else CUT_NOTICE

    goal = (
        "체지방을 줄이면서 근육을 지키는 4주 전신 루틴"
        if mode == "CUT"
        else (
            "약점 부위를 보완하는 4주 균형 루틴" if boosts else "기초 체력과 균형을 만드는 4주 루틴"
        )
    )

    return {
        "mode": mode,
        "mode_basis": mode_info["basis"],
        "mode_reason": mode_info["reason"],
        "days_per_week": days_per_week,
        "cycles": 4,
        "goal": goal,
        "focus_areas": list(boosts),
        "notice": notice,
        "disclaimer": DISCLAIMER,
        "boosts": boosts,
        "weekly_sets": weekly_sets_by_group(days),
        "days": out_days,
        "selection": {"source": source, "fixed": fixed, "meta": llm_meta},
    }


# --------------------------------------------------------------------------- #
# Public API — F12 (기존 유지)
# --------------------------------------------------------------------------- #


async def patch_routine(
    current_routine: dict[str, Any],
    feedback_text: str,
    contraindications: list[dict],
    candidates_by_group: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """피드백을 Function Calling으로 해석해 루틴 변경 오퍼레이션 목록을 반환한다.

    ⚠️ 반환된 changes 는 **아직 적용 전**이다. 호출부(핸들러)가
       coach_chat.apply_changes_to_days 로 적용하고 새 버전을 만든다 —
       F12-b 와 같은 경로다.

    Args:
        current_routine: 현재 활성 루틴 전체 (day_routine + exercises 포함)
        feedback_text: workout_log.feedback_text
        contraindications: analysis_session.contraindications (기존 누적 금기)
        candidates_by_group: 근육군별 교체 후보. replace_exercise 가 이 안에서만
                             고르게 한다 (없으면 교체 제안이 전부 거부된다)

    Returns:
        {
          "interpretation": str,           # LLM 해석 요약 (routine_revision.interpretation)
          "changes": list[dict],           # 변경 오퍼레이션 목록 (routine_revision.changes)
          "contraindications_added": list, # 이번 회차 증분 (routine_revision.contraindications_added)
          "raw_response": dict | None,     # 원본 API 응답 (routine_revision.raw_response)
        }
    """
    if settings.use_mock:
        return dict(_mock_patch(feedback_text, current_routine))

    from openai import AsyncOpenAI  # 지연 import — services/ocr.py 상단 주석 참고

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    existing = (
        f"\n기존 누적 금기: {json.dumps(contraindications, ensure_ascii=False)}"
        if contraindications
        else ""
    )

    user_message = (
        f"현재 루틴:\n{json.dumps(current_routine, ensure_ascii=False, indent=2)}\n\n"
        f"사용자 피드백: {feedback_text}"
        f"{existing}"
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        tools=TOOLS,
        tool_choice="auto",
    )

    raw = response.model_dump()
    message = response.choices[0].message

    changes: list[dict] = []
    contraindications_added: list[dict] = []

    if message.tool_calls:
        for tc in message.tool_calls:
            args = json.loads(tc.function.arguments)
            changes.append({"function": tc.function.name, "args": args})
            if tc.function.name == "flag_contraindication":
                contraindications_added.append(
                    {"body_part": args["body_part"], "severity": args["severity"]}
                )

    # message.content: 변경 없을 때 LLM이 텍스트로 이유를 설명하기도 함
    # ⚠️ 산문이 없으면 여기서 제안 목록으로 요약해 두지만, 그 요약은 **검증 전**이라
    #    거부된 제안까지 포함한다. 호출부(핸들러)는 llm_message 가 None 이면
    #    적용 결과로 다시 요약해야 한다 (summarize_applied 주석 참고).
    interpretation = message.content or _summarize_changes(changes)

    return {
        "interpretation": interpretation,
        "llm_message": message.content,
        "changes": changes,
        "contraindications_added": contraindications_added,
        "raw_response": raw,
    }


# ── Internal ──────────────────────────────────────────────────────────────────

_CHANGE_LABELS = {
    "replace_exercise": "운동 교체",
    "adjust_intensity": "강도 조정",
    "reschedule_day": "일정 변경",
    "remove_exercise": "운동 제거",
    "flag_contraindication": "금기 등록",
    "enforce_contraindication": "주의 부위 볼륨 조정",
}


def summarize_applied(applied: list[dict], contraindications_added: list[dict]) -> str:
    """**실제로 적용된** 변경으로 해석문을 만든다.

    ⚠️ 이 함수가 따로 있는 이유: `_summarize_changes` 는 LLM 이 **제안한** 목록을
       요약한다. 그런데 제안 중 일부는 검증에서 거부된다 (후보 밖 운동·없는 Day
       등). 거부된 것까지 요약에 들어가면 **하지도 않은 일을 했다고 말하게 된다.**
       실측(2026-08-15): LLM 이 대퇴사두 슬롯을 다른 근육군 운동으로 바꾸려다
       거부됐는데, 해석문에는 "금기 등록, 운동 교체 (2건)" 이 그대로 남았다.
       화면에는 교체가 없는데 문구만 교체를 주장한 셈이다.

    금기는 changes 에 안 들어가므로(세션에 누적) 여기서 따로 세어 준다.
    """
    labels = [_CHANGE_LABELS.get(c["function"], c["function"]) for c in applied]
    if contraindications_added:
        labels = ["금기 등록"] + labels
    if not labels:
        return "변경 사항 없음"
    # 같은 종류가 여러 건이면 묶어서 — "볼륨 조정, 볼륨 조정, …" 은 읽히지 않는다
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    parts = [(f"{k} {v}건" if v > 1 else k) for k, v in counts.items()]
    return ", ".join(parts)


def _summarize_changes(changes: list[dict]) -> str:
    if not changes:
        return "변경 사항 없음"
    labels = [_CHANGE_LABELS.get(c["function"], c["function"]) for c in changes]
    return ", ".join(labels) + f" ({len(changes)}건)"
