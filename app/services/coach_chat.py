"""F12 코치 대화 — 운동 후 피드백을 대화로 받아 다음 운동부터 반영한다.

설계 전문: docs/f12-coach-chat.md

━━ 아키텍처 결정 3개 ━━

1. **Stateless 대화.** 서버는 대화 상태를 저장하지 않는다 — 클라이언트가
   messages 배열을 매 요청에 보낸다. 스키마 변경이 0이고(공유 파일 협의 불필요),
   서버 재시작·워커와 무관하게 동작한다.

   "클라이언트가 히스토리를 조작하면?" — 조작해도 안전하다. 변경은 적용
   시점에 **서버가 도구 호출을 재수집·재검증**하기 때문이다 (아래 3).

2. **말은 자유, 손은 묶는다.** LLM 은 도구 4개(adjust/replace/flag/finalize)로만
   행동하고, 도구 인자는 코드가 검증한다:
     replace  → 후보 집합(candidates_for_slot) 안의 ref 만 허용
     adjust   → 세트 상한(slot_sets_cap)·횟수 범위로 클램프
     finalize → 이걸 불러야만 적용 대상이 된다
   진단 점수(산수는 코드)·루틴 생성(선택만 LLM)과 같은 원칙의 연장이다.

3. **적용은 새 버전으로.** 기존 버전을 고치면 완료 기록과 어긋난다.
   apply 시 FEEDBACK 버전을 만들고 변경을 얹어 활성화한다.
   변경 계산은 순수 함수(apply_changes_to_days)로 분리 — DB 없이 검증 가능.

━━ 대화 품질의 근거 ━━

페르소나는 GPTCoach(Stanford CHI 2025)의 동기부여 면담(MI/OARS) 전략을 옮겼다
(prompts/coach_chat.py 모듈 주석). 파인튜닝하지 않는다 — 이 분야 최신 연구들이
전부 프롬프트 전략 + 도구 제약으로 구현했고, 한국어 코칭 대화 데이터셋은
존재하지 않는다 (조사: docs/f12-coach-chat.md §5).
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from app.config import settings
from app.schemas.enums import ExerciseKind
from app.prompts.coach_chat import SYSTEM_PROMPT, TOOLS, build_context
from app.services import exercise_catalog
from app.services.routine_templates import slot_sets_cap

log = logging.getLogger("services.coach_chat")

#: 사용자 발화 최대 턴. 초과하면 프롬프트가 [마지막 턴]을 붙여 강제 마무리한다.
#: 근거: RP·Freeletics 류의 세션 후 체크인은 1~3문항이다. 8턴이면 통증 확인
#: 왕복까지 충분하고, 그 이상은 비용·이탈만 는다 (PM 확정 2026-08-14).
MAX_TURNS = 8

#: 클램프 범위 — 도구 스키마와 동일하게 유지할 것.
_SETS_DELTA_RANGE = (-2, 2)
_REPS_DELTA_RANGE = (-4, 4)
_REPS_RANGE = (5, 25)  # ACSM 초보 8~12 + 근지구력 15~25 를 포괄하는 안전 범위
_REST_RANGE = (30, 240)

_SAFETY_FOOTER = "통증이 계속되면 운동을 중단하고 전문가와 상담하세요."


# --------------------------------------------------------------------------- #
# 도구 인자 검증 — LLM 이 뭘 보내와도 여기서 걸러진다
# --------------------------------------------------------------------------- #


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def validate_tool_call(
    name: str,
    args: dict[str, Any],
    days: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str | None]:
    """도구 호출 하나를 검증·정규화한다.

    Args:
        candidates: 근육군 → 후보 운동 목록. **근육군별로 받는다** — 합집합만
            받으면 가슴 슬롯을 하체 운동으로 바꿔도 통과한다 (아래 참조).

    Returns:
        (정규화된 args, None)  통과
        (None, 거부 사유)      거부 — 사유는 tool 결과로 LLM 에게 돌려줘
                               스스로 고치게 한다 (조용히 버리지 않는다)
    """
    by_order = {d["day_order"]: d for d in days}

    if name == "flag_contraindication":
        if str(args.get("severity")) not in ("WARN", "BLOCK"):
            return None, "severity 는 WARN 또는 BLOCK 이어야 합니다."
        if not args.get("body_part"):
            return None, "body_part 가 없습니다."
        return args, None

    if name == "finalize_revision":
        changes = args.get("changes")
        if not isinstance(changes, list):
            return None, "changes 는 배열이어야 합니다."
        return args, None

    # 이하 두 도구는 실제 Day·운동을 가리켜야 한다
    day = by_order.get(args.get("day_order"))
    if day is None:
        return None, f"Day {args.get('day_order')} 는 이 루틴에 없습니다."

    names = {e["name"] for e in day.get("exercises", [])}

    if name == "adjust_intensity":
        target = args.get("exercise_name")
        if target not in names:
            return (
                None,
                f"'{target}' 은 Day {day['day_order']} 에 없습니다. 있는 운동: {sorted(names)}",
            )
        out = dict(args)
        out["sets_delta"] = _clamp(int(args.get("sets_delta") or 0), *_SETS_DELTA_RANGE)
        out["reps_delta"] = _clamp(int(args.get("reps_delta") or 0), *_REPS_DELTA_RANGE)
        if args.get("rest_sec_new") is not None:
            out["rest_sec_new"] = _clamp(int(args["rest_sec_new"]), *_REST_RANGE)
        return out, None

    if name == "replace_exercise":
        target = args.get("old_exercise_name")
        if target not in names:
            return None, f"'{target}' 은 Day {day['day_order']} 에 없습니다."
        ref = args.get("new_exercise_ref")

        # ⚠️ **교체는 같은 근육군 후보 안에서만** 허용한다.
        #    후보를 합집합으로 뭉쳐서 검사하면, 이 루틴에 하체 Day 가 있다는
        #    이유만으로 가슴 슬롯에 스쿼트를 넣어도 통과한다 (실측으로 확인).
        #    그러면 운동은 하체인데 muscle_group 은 '가슴'으로 남아
        #    **주간 볼륨 집계와 화면 라벨이 조용히 어긋난다.**
        #    슬롯의 세트·횟수도 그 근육군 기준으로 정해진 값이라 함께 무의미해진다.
        slot = next((e for e in day.get("exercises", []) if e["name"] == target), None)
        group = (slot or {}).get("muscle_group")
        same_group = candidates.get(group) if group else None
        if same_group is None:
            # 근육군을 알 수 없으면 최소한 전체 후보 안에는 있어야 한다
            if ref not in {c["exercise_ref"] for g in candidates.values() for c in g}:
                return None, f"'{ref}' 는 후보 목록에 없습니다. 제공된 후보에서만 고르세요."
            return args, None
        if ref not in {c["exercise_ref"] for c in same_group}:
            return None, (
                f"'{ref}' 는 '{group}' 후보가 아닙니다. "
                f"'{group}' 슬롯은 같은 부위 운동으로만 바꿀 수 있습니다."
            )
        return args, None

    return None, f"알 수 없는 도구: {name}"


# --------------------------------------------------------------------------- #
# 변경 적용 — 순수 함수 (DB 없음, 검증 스크립트가 직접 태운다)
# --------------------------------------------------------------------------- #


def apply_changes_to_days(
    days: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    catalog_by_ref: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """검증된 도구 호출을 Day 목록에 적용한 사본을 돌려준다.

    Returns:
        (새 days, 적용 기록)  — 적용 기록은 routine_revision.changes 로 저장

    ⚠️ 원본을 수정하지 않는다. 세트는 여기서도 한 번 더 클램프한다 —
       adjust 가 여러 번 겹치면 개별 검증을 통과해도 합이 상한을 넘을 수 있다.
    """
    out = copy.deepcopy(days)
    by_order = {d["day_order"]: d for d in out}
    applied: list[dict[str, Any]] = []

    for call in tool_calls:
        name, args = call["name"], call["args"]

        if name == "adjust_intensity":
            day = by_order.get(args["day_order"])
            if day is None:
                continue
            for e in day.get("exercises", []):
                if e["name"] != args["exercise_name"]:
                    continue
                cap = slot_sets_cap(e.get("muscle_group") or "")
                if e.get("sets") is not None:
                    e["sets"] = _clamp(e["sets"] + args.get("sets_delta", 0), 1, cap)
                if e.get("reps") is not None:
                    e["reps"] = _clamp(e["reps"] + args.get("reps_delta", 0), *_REPS_RANGE)
                if args.get("rest_sec_new") is not None:
                    e["rest_sec"] = args["rest_sec_new"]
                applied.append({"function": name, "args": args})
                break

        elif name == "replace_exercise":
            day = by_order.get(args["day_order"])
            new = catalog_by_ref.get(args["new_exercise_ref"])
            if day is None or new is None:
                continue
            for e in day.get("exercises", []):
                if e["name"] != args["old_exercise_name"]:
                    continue
                # 세트·횟수·휴식(코드가 정한 처방)은 유지하고 운동만 바꾼다.
                e["exercise_ref"] = new["exercise_ref"]
                e["name"] = new.get("name_ko") or new["name_en"]
                e["image_url"] = new.get("image_url")
                applied.append({"function": name, "args": args})
                break

        elif name == "flag_contraindication":
            # Day 를 바꾸지 않는다 — 세션 누적은 호출자(라우터)가 한다.
            applied.append({"function": name, "args": args})

    return out, applied


def collect_tool_calls(
    messages: list[dict[str, Any]],
    days: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """대화 히스토리에서 검증 통과한 도구 호출과 finalize 를 재수집한다.

    적용(apply) 시점에 호출된다 — 클라이언트가 보낸 히스토리를 그대로 믿지 않고
    **여기서 다시 검증**하므로, 히스토리가 조작돼도 규칙 밖 변경은 나갈 수 없다.
    """
    calls: list[dict[str, Any]] = []
    finalized: dict[str, Any] | None = None

    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            ok_args, err = validate_tool_call(name, args, days, candidates)
            if err is not None:
                log.info("도구 재검증 거부: %s — %s", name, err)
                continue
            if name == "finalize_revision":
                finalized = ok_args  # 마지막 finalize 가 이긴다
            else:
                calls.append({"name": name, "args": ok_args})

    return calls, finalized


# --------------------------------------------------------------------------- #
# 대화 1턴
# --------------------------------------------------------------------------- #


def _candidates_by_group(
    days: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups = {
        e.get("muscle_group")
        for d in days
        for e in d.get("exercises", [])
        if e.get("muscle_group") and (e.get("exercise_kind") or e.get("kind")) != ExerciseKind.CARDIO
    }
    return {g: exercise_catalog.candidates_for_slot(g, catalog) for g in sorted(groups)}


def _count_user_turns(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def turns_exceeded(messages: list[dict[str, Any]]) -> bool:
    """MAX_TURNS를 이미 넘겼는가 (#113).

    ⚠️ 지금까지 MAX_TURNS는 프롬프트("[마지막 턴]" 문구)로만 강제됐다 —
       이 리포의 원칙("반복 위반은 산문이 아니라 코드로 막는다", vlm.py
       모듈 주석)의 유일한 반례였다. 모델이 지시를 무시하면 무한정 이어질
       수 있었다. 라우트가 요청을 받자마자 이 함수로 먼저 끊는다.
    """
    return _count_user_turns(messages) >= MAX_TURNS


def _mock_reply(messages: list[dict[str, Any]], turn: int) -> dict[str, Any]:
    """USE_MOCK — 데모 시나리오("무릎 아팠어")가 실제 흐름대로 돌게 한다."""
    from app.services.routine import _PAIN_TERMS  # 활용형 목록 재사용

    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    has_pain = any(t in (last or "") for t in _PAIN_TERMS)

    if turn <= 1 and has_pain:
        return {
            "reply": "오늘도 끝까지 해내셨네요! 그런데 통증은 그냥 넘기면 안 돼요. "
            "운동을 멈출 정도였나요, 아니면 살짝 불편한 정도였나요?",
            "tool_calls": [],
            "finalized": None,
        }
    if has_pain or turn >= 2:
        return {
            "reply": "알려주셔서 다행이에요. 다음부터 부담이 적은 운동으로 바꿔볼게요. "
            + _SAFETY_FOOTER,
            "tool_calls": [
                {
                    "name": "flag_contraindication",
                    "args": {
                        "body_part": "무릎",
                        "severity": "WARN",
                        "reason": "사용자 통증 보고 (mock)",
                    },
                }
            ],
            "finalized": {
                "summary": "무릎 부담을 줄이는 방향으로 정리했어요. 다음 운동에서 확인해보세요! (mock)",
                "changes": [{"what": "무릎 → 주의 부위 등록", "why": "통증 보고"}],
            },
        }
    return {
        "reply": "오늘 운동 어떠셨어요? 힘들었던 운동이나 불편한 곳이 있으면 편하게 말씀해주세요.",
        "tool_calls": [],
        "finalized": None,
    }


async def chat_turn(
    messages: list[dict[str, Any]],
    day: dict[str, Any],
    days: list[dict[str, Any]],
    contraindications: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """대화 1턴을 진행한다.

    Args:
        messages: 지금까지의 대화 (user/assistant/tool). 클라이언트가 관리.
        day:      오늘 완료한 Day (운동 포함)
        days:     활성 루틴 전체 (도구 검증용)

    Returns:
        {
          "reply": str,                       # 사용자에게 보여줄 코치 답변
          "messages": [...],                  # 이번 턴 결과가 더해진 전체 히스토리
          "tool_events": [{name, args}...],   # 이번 턴에 통과한 도구 호출 (UI 배지용)
          "finalized": {...} | None,          # finalize 카드. 있으면 대화 종료
          "turn": int, "max_turns": int,
        }
    """
    turn = _count_user_turns(messages)
    candidates = _candidates_by_group(days, catalog)

    if settings.use_mock:
        mock = _mock_reply(messages, turn)
        new_history = list(messages) + [{"role": "assistant", "content": mock["reply"]}]
        return {
            "reply": mock["reply"],
            "messages": new_history,
            "tool_events": mock["tool_calls"],
            "finalized": mock["finalized"],
            "turn": turn,
            "max_turns": MAX_TURNS,
        }

    from openai import AsyncOpenAI

    from app.services.vlm import call_json  # noqa: F401 — provider 준비 확인용 import 경로 공유

    # ⚠️ 여기는 잡이 아니라 **동기 요청**이다 (POST /coach/chat). asyncio.wait_for 로
    #    감싸는 곳도 없어서, timeout 이 없으면 사용자가 최대 30분 스피너를 본다.
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=1)

    context = build_context(day, contraindications, candidates, turn, MAX_TURNS)
    llm_messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context},
        *messages,
    ]

    tool_events: list[dict[str, Any]] = []
    finalized: dict[str, Any] | None = None
    reply = ""

    # 도구 호출 → 결과 반환 → 재호출 루프. 한 턴에 최대 3라운드 —
    # 코치가 flag + replace + finalize 를 연달아 부르는 케이스까지 커버한다.
    for _ in range(3):
        response = await client.chat.completions.create(
            model="gpt-4o",
            temperature=0.4,  # 대화는 약간의 온기, 도구 인자는 검증이 잡는다
            max_tokens=700,
            messages=llm_messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        llm_messages.append(assistant_entry)

        if msg.content:
            reply = msg.content

        if not msg.tool_calls:
            break

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            ok_args, err = validate_tool_call(tc.function.name, args, days, candidates)
            if err is not None:
                # 거부 사유를 돌려줘 LLM 이 같은 턴에서 고치게 한다.
                result = {"ok": False, "error": err}
            else:
                result = {"ok": True}
                if tc.function.name == "finalize_revision":
                    finalized = ok_args
                else:
                    tool_events.append({"name": tc.function.name, "args": ok_args})
            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if finalized is not None and reply:
            break

    # ── finalize 강제 라운드 ────────────────────────────────────────────────
    # 실호출 검증(2026-08-14)에서 드러난 결함: 코치가 교체·금기까지 실행하고도
    # finalize_revision 을 부르지 않아 요약 카드가 안 나왔다. 프롬프트 권고는
    # 이런 걸 보장하지 못한다 — **변경이 실행됐으면 카드는 규칙이다.**
    # tool_choice 로 finalize 를 강제해 결정론적으로 닫는다.
    if finalized is None and tool_events:
        llm_messages.append(
            {
                "role": "system",
                "content": "변경이 실행되었습니다. finalize_revision 으로 지금까지의 변경을 요약해 마무리하세요.",
            }
        )
        response = await client.chat.completions.create(
            model="gpt-4o",
            temperature=0.4,
            max_tokens=500,
            messages=llm_messages,
            tools=TOOLS,
            tool_choice={"type": "function", "function": {"name": "finalize_revision"}},
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            llm_messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    ],
                }
            )
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            ok_args, err = validate_tool_call(tc.function.name, args, days, candidates)
            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"ok": err is None}, ensure_ascii=False),
                }
            )
            if err is None:
                finalized = ok_args

    if finalized is not None and _SAFETY_FOOTER not in (finalized.get("summary") or ""):
        # 통증 흐름이면 안전 문구를 카드에 보강한다 (금기 등록이 있었던 경우).
        if any(e["name"] == "flag_contraindication" for e in tool_events):
            finalized["summary"] = f"{finalized['summary']} {_SAFETY_FOOTER}"

    # system 2개를 뗀 나머지가 다음 요청에 그대로 되돌아올 히스토리다.
    new_history = llm_messages[2:]

    return {
        "reply": reply or (finalized or {}).get("summary", ""),
        "messages": new_history,
        "tool_events": tool_events,
        "finalized": finalized,
        "turn": turn,
        "max_turns": MAX_TURNS,
    }
