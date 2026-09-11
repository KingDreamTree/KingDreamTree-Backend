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
from app.prompts.coach_chat import TOOLS, build_context
from app.schemas.enums import ExerciseKind
from app.services import exercise_catalog, prompt_store
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

#: 사용자 발화에 이게 있으면 "무게 얘기"다 — adjust_intensity 에 load_scale 이 있어야 한다 (#170).
_WEIGHT_TERMS = ("무거", "무겁", "가벼", "무게", "중량", "kg", "킬로")


def _append_safety_footer(finalized: dict[str, Any], tool_events: list[dict[str, Any]]) -> None:
    """통증 흐름(flag_contraindication 있었음)이면 안전 문구를 카드에 보강한다.

    ⚠️ 정확히 _SAFETY_FOOTER 문장인지가 아니라 "상담" 언급 자체로 판단한다 —
       프롬프트로 LLM이 비슷한 문구를 직접 쓰지 말라고 지시해뒀지만, 지시를
       어겨도 다른 표현으로 이미 안전 안내를 했다면 또 붙이면 안 된다(중복 문구 버그,
       예: "...상담하세요. 통증이 계속되면 운동을 중단하고 전문가와 상담하세요.").
    """
    if "상담" in (finalized.get("summary") or ""):
        return
    if any(e["name"] == "flag_contraindication" for e in tool_events):
        finalized["summary"] = f"{finalized['summary']} {_SAFETY_FOOTER}"


# --------------------------------------------------------------------------- #
# 도구 인자 검증 — LLM 이 뭘 보내와도 여기서 걸러진다
# --------------------------------------------------------------------------- #


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _norm(text: str) -> str:
    return "".join(str(text).split()).lower()


def resolve_exercise_name(target: str | None, names: list[str]) -> tuple[str | None, list[str]]:
    """사용자·LLM 이 부른 운동 이름을 Day 의 실제 운동 하나로 특정한다 (#170).

    Returns:
        (특정된 이름, 되물을 후보). 특정되면 후보는 빈 목록. 특정 못 하면 None 과 후보 —
        후보마저 비어 있으면 목록에 아예 없는 이름이다.

    규칙:
      1. 공백 제거·소문자로 완전 일치 → 그 이름 ("레그 프레스" → "레그프레스")
      2. **단어 경계** 일치가 딱 하나 → 그 이름 ("벤치" → "벤치프레스": 단어가 그 말로 시작)
      3. 단어 경계 일치가 여럿이거나, 부분 문자열 일치만 있으면 **특정하지 않는다.**
         후보를 돌려줘 코치가 되묻게 한다 — "프레스" 는 벤치프레스·숄더프레스 둘 다고,
         "컬" 을 "스컬 크러셔" 에 자동으로 붙이면 엉뚱한 운동이 조정된다 (실측 8/20:
         목록에 없는 벤치프레스를 말했는데 코치가 푸시업을 조정하고 벤치프레스를 조정했다고 적었다).

    ⚠️ 후보는 **오늘 Day 의 운동**에서만 찾는다. 카탈로그 전체에서 찾으면 "프레스" 가 9개,
       "푸시업" 이 17개다.
    """
    q = _norm(target or "")
    if len(q) < 2:
        return None, []
    exact = [n for n in names if _norm(n) == q]
    if len(exact) == 1:
        return exact[0], []
    boundary = [
        n
        for n in names
        if any(t == q or t.startswith(q) for t in (_norm(t) for t in str(n).split()))
        or _norm(n).startswith(q)
    ]
    if len(boundary) == 1:
        return boundary[0], []
    if boundary:
        return None, boundary
    return None, [n for n in names if q in _norm(n)]


def _resolve_or_ask(
    target: str | None, names: list[str], day_order: int
) -> tuple[str | None, str | None]:
    """(특정된 이름, None) 또는 (None, LLM 에게 돌려줄 지시).

    ⚠️ 거부 문구에 "없습니다" 만 적지 않는다 — 그 문장이 그대로 "고객님은 그 운동 안 하셨어요"
       로 번역돼 나갔다 (#170 증상 1). 무엇을 해야 하는지(되묻기·대체 금지)를 같이 적는다.
    """
    resolved, cands = resolve_exercise_name(target, names)
    if resolved:
        return resolved, None
    if len(cands) == 1:
        return None, (
            f"'{target}' 은 정확한 이름이 아닙니다. '{cands[0]}' 을 말한 것인지 "
            "사용자에게 확인한 뒤, 맞으면 그 이름으로 다시 호출하세요. 임의로 고르지 마세요."
        )
    if cands:
        return None, (
            f"'{target}' 은 여러 운동에 해당합니다: {', '.join(cands)}. "
            "임의로 고르지 말고, 어느 운동인지 사용자에게 되물으세요."
        )
    return None, (
        f"'{target}' 은 오늘 Day {day_order} 목록에서 찾지 못했습니다. 목록: {', '.join(names)}. "
        "다른 운동으로 대체하지 말고, 목록을 보여주며 어느 운동을 말하는지 되물으세요. "
        "사용자가 틀렸다고 말하지 마세요."
    )


def user_texts_since_last_change(messages: list[dict[str, Any]]) -> list[str]:
    """마지막으로 조정 도구(adjust/replace)가 **통과한** 뒤의 사용자 발화들 (#170 증상 3).

    "무게 얘기를 했나"를 어디서 볼지의 범위다.
      - 마지막 발화만 보면 "무거웠어요 → 낮출까요? → 네" 처럼 확인이 다음 턴에 올 때
        "네" 만 보고 놓친다 (재현 실측).
      - 대화 전체를 보면 벤치프레스 무게 얘기를 한 뒤 "스쿼트는 횟수 줄여줘" 를
        무게 얘기로 오인해 횟수 조정을 막는다.
    지금 다루는 운동의 구간 — 마지막 조정 이후 — 만 보면 둘 다 잡는다.

    ⚠️ 거부된 호출(tool 결과 ok=false)은 구간을 끊지 않는다. 아직 조정이 안 된 것이다.
    """
    rejected: set[str] = set()
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            try:
                if json.loads(m.get("content") or "{}").get("ok") is False:
                    rejected.add(str(m["tool_call_id"]))
            except (TypeError, ValueError, AttributeError):
                pass
    texts: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            texts.append(str(m.get("content") or ""))
        elif role == "assistant" and any(
            (tc.get("function") or {}).get("name") in ("adjust_intensity", "replace_exercise")
            and str(tc.get("id")) not in rejected
            for tc in m.get("tool_calls") or []
        ):
            texts = []
    return texts


def requires_load_scale(
    name: str, args: dict[str, Any], weight_talk: bool, is_bodyweight: bool
) -> bool:
    """무게 얘기였는데 adjust_intensity 가 load_scale 없이 왔는가 (#170 증상 3).

    ⚠️ 맨몸 운동(BODY WEIGHT)은 제외한다 — 낮출 무게가 없어 세트·횟수로 줄이는 게 맞다.
    """
    return (
        name == "adjust_intensity"
        and weight_talk
        and not is_bodyweight
        and args.get("load_scale") is None
    )


def adjust_is_noop(args: dict[str, Any]) -> bool:
    """세트·횟수·휴식·무게 아무것도 안 바꾸는 adjust — "아 그냥 두세요" 의 표현 (#171).

    취소 도구가 따로 없다. 같은 운동에 대한 호출은 마지막 것만 적용되므로(collect_tool_calls),
    코치가 0 변화로 다시 부르면 이전 조정이 무효가 된다. 이 호출 자체는 적용·카드에 남기지 않는다.
    """
    return (
        not args.get("sets_delta")
        and not args.get("reps_delta")
        and args.get("rest_sec_new") is None
        and args.get("load_scale") is None
    )


def _call_key(call: dict[str, Any]) -> tuple[Any, ...] | None:
    """같은 운동을 가리키는 호출끼리 묶는 키. 금기·finalize 는 묶지 않는다."""
    name, a = call["name"], call["args"]
    if name == "adjust_intensity":
        return ("adjust", a.get("day_order"), a.get("exercise_name"))
    if name == "replace_exercise":
        return ("replace", a.get("day_order"), a.get("old_exercise_name"))
    return None


def build_card_changes(
    tool_events: list[dict[str, Any]], ref_names: dict[str, str]
) -> list[dict[str, str]]:
    """검증을 통과한 도구 호출만으로 카드의 변경 목록을 만든다 (#170).

    ⚠️ LLM 이 finalize_revision 에 써 보낸 changes 는 쓰지 않는다. 실측(2026-08-20)에서
       코치가 푸시업을 조정하고 카드엔 "벤치프레스의 강도를 조정했습니다" 라고 적었다 —
       사용자는 눈치챌 방법이 없다. 카드는 **실제로 실행된 것**만 보여준다.
    """
    out: list[dict[str, str]] = []
    for event in tool_events:
        name, a = event["name"], event["args"]
        why = str(a.get("reason") or "")
        if name == "adjust_intensity":
            if adjust_is_noop(a):
                continue
            parts: list[str] = []
            if a.get("sets_delta"):
                parts.append(f"세트 {int(a['sets_delta']):+d}")
            if a.get("reps_delta"):
                parts.append(f"횟수 {int(a['reps_delta']):+d}회")
            if a.get("rest_sec_new") is not None:
                parts.append(f"휴식 {a['rest_sec_new']}초")
            if a.get("load_scale") is not None:
                scale = float(a["load_scale"])
                parts.append(
                    "무게 낮춤" if scale < 1 else "무게 올림" if scale > 1 else "무게 유지"
                )
            what = f"{a.get('exercise_name')} {', '.join(parts) if parts else '조정'}"
        elif name == "replace_exercise":
            ref = a.get("new_exercise_ref")
            if ref_names.get(ref) == a.get("old_exercise_name"):
                continue  # 원래 운동으로 되돌린 것 — 변경이 아니다
            what = f"{a.get('old_exercise_name')} → {ref_names.get(ref, ref)}"
        elif name == "flag_contraindication":
            what = f"{a.get('body_part')} → 주의 부위 등록 ({a.get('severity')})"
        else:
            continue
        out.append({"what": what, "why": why})
    return out


def truthful_card(
    finalized: dict[str, Any], tool_events: list[dict[str, Any]], ref_names: dict[str, str]
) -> dict[str, Any]:
    """finalize 카드를 실제 실행 내역에 맞춘다 — changes 는 항상 코드가, summary 는 어긋날 때만.

    summary 는 LLM 의 문장을 두되, **실제로 바꾼 운동을 하나도 언급하지 않으면** 실행 내역으로
    다시 쓴다 — "벤치프레스를 조정했습니다"(실제는 푸시업) 같은 요약이 카드 위에 남지 않게.
    """
    card = dict(finalized)
    card["changes"] = build_card_changes(tool_events, ref_names)
    touched = [
        e["args"].get("exercise_name") or e["args"].get("old_exercise_name")
        for e in tool_events
        if e["name"] in ("adjust_intensity", "replace_exercise")
    ]
    summary = str(card.get("summary") or "")
    if touched and not any(t and t in summary for t in touched):
        card["summary"] = "다음 운동부터 이렇게 바뀌어요: " + "; ".join(
            c["what"] for c in card["changes"]
        )
    return card


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

    names = [e["name"] for e in day.get("exercises", [])]

    if name == "adjust_intensity":
        # ⚠️ 완전 일치만 받던 것을 특정 규칙으로 바꿨다 (#170). 통과하면 인자의 이름을
        #    루틴의 실제 이름으로 바꿔 돌려준다 — 적용(apply_changes_to_days)이 이름으로 찾는다.
        target, err = _resolve_or_ask(args.get("exercise_name"), names, day["day_order"])
        if err is not None:
            return None, err
        out = dict(args)
        out["exercise_name"] = target
        out["sets_delta"] = _clamp(int(args.get("sets_delta") or 0), *_SETS_DELTA_RANGE)
        out["reps_delta"] = _clamp(int(args.get("reps_delta") or 0), *_REPS_DELTA_RANGE)
        if args.get("rest_sec_new") is not None:
            out["rest_sec_new"] = _clamp(int(args["rest_sec_new"]), *_REST_RANGE)
        # ⚠️ 무게는 **배율만** 받는다. kg 을 LLM 이 정하면 D9(1RM 추정 폐기)이
        #    무너진다 — 실제 kg 은 체중으로 코드가 낸다(services/load_guide).
        #    범위를 좁게 잡는다: 한 번의 피드백으로 무게가 급변하면 위험하다.
        if args.get("load_scale") is not None:
            try:
                scale = float(args["load_scale"])
            except (TypeError, ValueError):
                return None, "load_scale 은 숫자여야 합니다 (예: 0.8, 1.2)."
            if not 0.5 <= scale <= 1.5:
                return None, "load_scale 은 0.5~1.5 사이여야 합니다."
            out["load_scale"] = round(scale, 2)
        return out, None

    if name == "replace_exercise":
        target, err = _resolve_or_ask(args.get("old_exercise_name"), names, day["day_order"])
        if err is not None:
            return None, err
        args = {**args, "old_exercise_name": target}
        ref = args.get("new_exercise_ref")

        # ⚠️ **교체는 같은 근육군 후보 안에서만** 허용한다.
        #    후보를 합집합으로 뭉쳐서 검사하면, 이 루틴에 하체 Day 가 있다는
        #    이유만으로 가슴 슬롯에 스쿼트를 넣어도 통과한다 (실측으로 확인).
        #    그러면 운동은 하체인데 muscle_group 은 '가슴'으로 남아
        #    **주간 볼륨 집계와 화면 라벨이 조용히 어긋난다.**
        #    슬롯의 세트·횟수도 그 근육군 기준으로 정해진 값이라 함께 무의미해진다.
        slot = next((e for e in day.get("exercises", []) if e["name"] == target), None)
        if slot is not None and ref and ref == slot.get("exercise_ref"):
            return args, None  # 원래 운동으로 되돌리기 — 교체 취소. 후보 밖이어도 허용 (#171)
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


def merge_load_adjust(
    routine: dict[str, Any], applied: list[dict[str, Any]]
) -> dict[str, float]:
    """이전 배율 + 이번 피드백의 load_scale 을 합친다 (exercise_ref → 배율).

    ⚠️ **두 피드백 경로가 공유한다** — 한 방 피드백(ROUTINE_PATCH)과 코치
       대화(F12-b). 한쪽에만 두면 다른 쪽에서 무게 피드백이 조용히 버려진다
       (실측 2026-08-20: 한 방 피드백이 그 상태였다).

    ⚠️ **곱하지 않고 덮어쓴다.** 0.8 을 두 번 받으면 0.64 가 되는데, 사용자는
       "무겁다"고 두 번 말했을 뿐 절반으로 줄이라고 한 적이 없다. 매번 마지막
       판단을 쓰고, 폭주는 load_guide 의 _ADJUST_RANGE 가 한 번 더 막는다.

    ⚠️ 키가 exercise_ref 인 이유 — 운동 이름은 교체·번역으로 바뀔 수 있지만
       ref 는 카탈로그 식별자라 안정적이다. ref 를 못 찾으면 그 항목은 버린다.
    """
    merged = dict((routine.get("raw_response") or {}).get("load_adjust") or {})
    for item in applied:
        if item.get("function") != "adjust_intensity":
            continue
        args = item.get("args") or {}
        scale, ref = args.get("load_scale"), args.get("exercise_ref")
        if scale is None or not ref:
            continue
        merged[str(ref)] = float(scale)
    return merged


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
            if day is None or adjust_is_noop(args):
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
                # ⚠️ 무게 배율은 운동 행에 쓰지 않는다 — routine_day_exercise 에
                #    무게 컬럼이 없고, 만들지 않기로 했다(D9). 대신 어느 운동에
                #    대한 배율인지 **ref 로** 적용 기록에 남겨 호출부가 루틴
                #    단위로 저장한다 (routes/coach_chat._merged_load_adjust).
                record = dict(args)
                if args.get("load_scale") is not None and e.get("exercise_ref"):
                    record["exercise_ref"] = e["exercise_ref"]
                applied.append({"function": name, "args": record})
                break

        elif name == "replace_exercise":
            day = by_order.get(args["day_order"])
            new = catalog_by_ref.get(args["new_exercise_ref"])
            if day is None or new is None:
                continue
            for e in day.get("exercises", []):
                if e["name"] != args["old_exercise_name"]:
                    continue
                if e.get("exercise_ref") == new["exercise_ref"]:
                    break  # 원래 운동으로 되돌리기 — 변경 없음
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

    # ⚠️ 대화 때 거부된 호출(tool 결과 ok=false)은 재검증을 통과하더라도 적용하지 않는다 (#170).
    #    무게 확인처럼 validate_tool_call 밖에서 거부한 사유는 여기서 재현되지 않기 때문이다 —
    #    거부됐던 "횟수 -2" 가 [적용] 때 슬그머니 반영되는 경로를 막는다.
    rejected: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            try:
                if json.loads(msg.get("content") or "{}").get("ok") is False:
                    rejected.add(str(msg["tool_call_id"]))
            except (TypeError, ValueError, AttributeError):
                pass

    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            if str(tc.get("id")) in rejected:
                continue
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

    # ⚠️ 같은 운동에 대한 조정·교체는 **마지막 호출만** 적용한다 (#171 철회).
    #    취소 도구가 없으므로 "아 그냥 두세요" 는 같은 운동을 0 변화로(또는 원래 운동으로)
    #    다시 부르는 것으로 표현한다 — 이전 호출이 남아 있으면 취소가 불가능하다.
    #    누적이 아니라 최종값이다 (프롬프트에도 그렇게 지시).
    seen: set[tuple[Any, ...]] = set()
    kept: list[dict[str, Any]] = []
    for call in reversed(calls):
        key = _call_key(call)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        kept.append(call)
    return list(reversed(kept)), finalized


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
    # ⚠️ ">" 다 (#171). ">=" 였을 때 8번째 발화가 [마지막 턴] 신호를 받기 전에 차단돼
    #    finalize 강제가 한 번도 실행되지 못했다 — 변경 없이 7턴을 쓰면 대화도 [적용]도
    #    막히는 막다른 길. 8번째는 처리하고 카드를 만들고, 9번째부터 끊는다.
    return _count_user_turns(messages) > MAX_TURNS


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
    ref_names = {
        c["exercise_ref"]: (c.get("name_ko") or c.get("name_en") or c["exercise_ref"])
        for group in candidates.values()
        for c in group
    }
    # Day 의 운동 자체도 넣는다 — 원래 운동으로 되돌린 교체를 카드가 "변경 아님"으로 알아보게.
    ref_names.update(
        {
            e["exercise_ref"]: e["name"]
            for d in days
            for e in d.get("exercises", [])
            if e.get("exercise_ref")
        }
    )
    # ⚠️ 무게 얘기(#170 증상 3) — 사용자가 무겁다/가볍다고 했는데 adjust_intensity 에
    #    load_scale 이 없으면 되돌린다. 프롬프트 지시만으로는 안 지켜졌다 (실측 8/20: 횟수를 줄였다).
    #    범위는 "마지막 조정 이후의 사용자 발화" (user_texts_since_last_change 주석 참고).
    weight_talk = any(
        t in text for text in user_texts_since_last_change(messages) for t in _WEIGHT_TERMS
    )
    bodyweight_refs = {
        c["exercise_ref"]
        for c in catalog
        if c.get("equipments") and set(c["equipments"]) <= {"BODY WEIGHT"}
    }
    ref_by_name = {e["name"]: e.get("exercise_ref") for d in days for e in d.get("exercises", [])}

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
        {"role": "system", "content": prompt_store.get("coach.system")[0]},
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
            if err is None and requires_load_scale(
                tc.function.name,
                ok_args,
                weight_talk,
                ref_by_name.get(ok_args.get("exercise_name")) in bodyweight_refs,
            ):
                err = (
                    "사용자가 무게를 말했습니다. 세트·횟수 대신 load_scale 로 넘기세요 "
                    "(0.8=낮춤 / 1.2=올림). kg 은 적지 마세요."
                )
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
    # ⚠️ 마지막 턴(turn == MAX_TURNS)도 강제한다 (#171) — 프롬프트의 [마지막 턴] 지시를
    #    모델이 무시하면 카드가 없어 [적용]이 막힌다. 카드 없이 상한에 닿는 길을 없앤다.
    if finalized is None and (tool_events or turn >= MAX_TURNS):
        llm_messages.append(
            {
                "role": "system",
                "content": (
                    "변경이 실행되었습니다. finalize_revision 으로 지금까지의 변경을 요약해 마무리하세요."
                    if tool_events
                    else "대화 상한입니다. finalize_revision 으로 마무리하세요 — 변경이 없으면 changes 는 빈 배열입니다."
                ),
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

    if finalized is not None:
        # 카드의 변경 목록은 실제 실행된 도구로 다시 만든다 — LLM 이 적은 것은 쓰지 않는다 (#170).
        # ⚠️ 이번 턴의 tool_events 가 아니라 **대화 전체**에서 재수집한다 (#171). 2턴에 조정하고
        #    3턴에 finalize 하면 이번 턴엔 이벤트가 없어 카드가 비었다. 재수집은 [적용]과 같은
        #    함수(collect_tool_calls)라 카드와 실제 적용이 항상 같다 — 마지막 호출만 반영·취소 포함.
        effective, _ = collect_tool_calls(llm_messages[2:], days, candidates)
        finalized = truthful_card(finalized, effective, ref_names)
        _append_safety_footer(finalized, effective)

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
