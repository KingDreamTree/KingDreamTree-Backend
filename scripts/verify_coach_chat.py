"""코치 대화 검증 — 도구 검증·적용·재수집이 규칙대로 동작하는지.

    python scripts/verify_coach_chat.py

DB·API 키 없이 돈다. 여기 규칙들도 전부 **에러 없이 조용히 깨지는** 종류다 —
후보 밖 운동이 들어와도, 상한 밖 세트가 들어와도 대화는 정상 진행된다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings  # noqa: E402

settings.use_mock = True

from app.services.coach_chat import (  # noqa: E402
    MAX_TURNS,
    _append_safety_footer,
    _SAFETY_FOOTER,
    apply_changes_to_days,
    chat_turn,
    collect_tool_calls,
    validate_tool_call,
)

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


from app.services.coach_chat import (  # noqa: E402
    MAX_TURNS,
    adjust_is_noop,
    build_card_changes,
    requires_load_scale,
    resolve_exercise_name,
    truthful_card,
    turns_exceeded,
    user_texts_since_last_change,
)

# ── 픽스처 ───────────────────────────────────────────────────────────────────

DAYS = [
    {
        "day_order": 1,
        "title": "하체 A",
        "exercises": [
            {
                "name": "레그프레스",
                "muscle_group": "대퇴사두",
                "sets": 4,
                "reps": 10,
                "rest_sec": 90,
            },
            {
                "name": "레그컬",
                "muscle_group": "햄스트링·둔근",
                "sets": 3,
                "reps": 12,
                "rest_sec": 90,
            },
        ],
    },
    {
        "day_order": 2,
        "title": "상체 A",
        "exercises": [
            {"name": "체스트프레스", "muscle_group": "가슴", "sets": 4, "reps": 10, "rest_sec": 90},
        ],
    },
]
# 후보는 **근육군별로** 준다 — 합집합으로 뭉치면 교차 교체가 뚫린다
CANDIDATES = {
    "대퇴사두": [{"exercise_ref": "ref-legext", "name_ko": "레그 익스텐션"}],
    "햄스트링·둔근": [{"exercise_ref": "ref-hipthrust", "name_ko": "힙 쓰러스트"}],
}
CATALOG_BY_REF = {
    "ref-legext": {
        "exercise_ref": "ref-legext",
        "name_ko": "레그 익스텐션",
        "name_en": "leg extension",
    },
    "ref-hipthrust": {
        "exercise_ref": "ref-hipthrust",
        "name_ko": "힙 쓰러스트",
        "name_en": "hip thrust",
    },
}


def tc(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        ],
    }


# ── 검증 ─────────────────────────────────────────────────────────────────────


def rule_validation() -> None:
    print("\n[도구 인자 검증]")

    _, err = validate_tool_call(
        "replace_exercise",
        {
            "day_order": 1,
            "old_exercise_name": "레그프레스",
            "new_exercise_ref": "ref-made-up",
            "reason": "x",
        },
        DAYS,
        CANDIDATES,
    )
    check("후보 밖 운동 거부", err is not None, err or "")

    _, err = validate_tool_call(
        "replace_exercise",
        {
            "day_order": 1,
            "old_exercise_name": "레그프레스",
            "new_exercise_ref": "ref-legext",
            "reason": "x",
        },
        DAYS,
        CANDIDATES,
    )
    check("후보 안 운동 통과", err is None)

    # ⚠️ 회귀 방지 — 후보를 합집합으로 검사하던 시절엔 이게 통과했다.
    #    운동만 하체로 바뀌고 muscle_group 라벨은 그대로 남아 볼륨 집계가 어긋났다.
    _, err = validate_tool_call(
        "replace_exercise",
        {
            "day_order": 1,
            "old_exercise_name": "레그프레스",
            "new_exercise_ref": "ref-hipthrust",
            "reason": "x",
        },
        DAYS,
        CANDIDATES,
    )
    check("다른 근육군 운동으로 교체 거부", err is not None, err or "통과해버림")

    _, err = validate_tool_call(
        "adjust_intensity",
        {"day_order": 9, "exercise_name": "레그프레스", "reason": "x"},
        DAYS,
        CANDIDATES,
    )
    check("없는 Day 거부", err is not None)

    ok_args, err = validate_tool_call(
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "레그프레스", "sets_delta": 99, "reason": "x"},
        DAYS,
        CANDIDATES,
    )
    check("세트 증감 클램프 (+99 → +2)", err is None and ok_args["sets_delta"] == 2)

    _, err = validate_tool_call(
        "flag_contraindication",
        {"body_part": "무릎", "severity": "FATAL", "reason": "x"},
        DAYS,
        CANDIDATES,
    )
    check("잘못된 severity 거부", err is not None)


def rule_apply() -> None:
    print("\n[변경 적용 (순수 함수)]")

    calls = [
        {
            "name": "replace_exercise",
            "args": {
                "day_order": 1,
                "old_exercise_name": "레그프레스",
                "new_exercise_ref": "ref-legext",
                "reason": "무릎 부담",
            },
        },
        {
            "name": "adjust_intensity",
            "args": {
                "day_order": 2,
                "exercise_name": "체스트프레스",
                "sets_delta": 2,
                "reps_delta": 0,
                "reason": "쉬움",
            },
        },
        {
            "name": "adjust_intensity",  # 중첩 — 합이 상한(가슴=복합=4)을 넘는 케이스
            "args": {
                "day_order": 2,
                "exercise_name": "체스트프레스",
                "sets_delta": 2,
                "reps_delta": 0,
                "reason": "더",
            },
        },
    ]
    new_days, applied = apply_changes_to_days(DAYS, calls, CATALOG_BY_REF)

    ex0 = new_days[0]["exercises"][0]
    check(
        "교체 반영 (이름·ref)",
        ex0["name"] == "레그 익스텐션" and ex0["exercise_ref"] == "ref-legext",
    )
    check("교체해도 세트·횟수는 처방 유지", ex0["sets"] == 4 and ex0["reps"] == 10)
    check("원본 불변", DAYS[0]["exercises"][0]["name"] == "레그프레스")

    ex1 = new_days[1]["exercises"][0]
    check("중첩 adjust 도 슬롯 상한(복합=4)에 걸림", ex1["sets"] == 4, f"sets={ex1['sets']}")
    check("적용 기록 수", len(applied) == 3)


def rule_recollect() -> None:
    print("\n[히스토리 재수집 — 클라이언트 불신 지점]")

    messages = [
        {"role": "user", "content": "무릎 아팠어"},
        tc("flag_contraindication", {"body_part": "무릎", "severity": "WARN", "reason": "통증"}),
        tc(
            "replace_exercise",
            {
                "day_order": 1,
                "old_exercise_name": "레그프레스",
                "new_exercise_ref": "ref-made-up",
                "reason": "조작된 항목",
            },
        ),
        tc(
            "replace_exercise",
            {
                "day_order": 1,
                "old_exercise_name": "레그프레스",
                "new_exercise_ref": "ref-legext",
                "reason": "정상 항목",
            },
        ),
        tc("finalize_revision", {"summary": "정리", "changes": [{"what": "교체", "why": "통증"}]}),
    ]
    calls, finalized = collect_tool_calls(messages, DAYS, CANDIDATES)

    check("finalize 수집", finalized is not None)
    refs = [c["args"].get("new_exercise_ref") for c in calls if c["name"] == "replace_exercise"]
    check("조작된 후보 밖 항목은 걸러짐", refs == ["ref-legext"], str(refs))
    check("금기 호출 수집", any(c["name"] == "flag_contraindication" for c in calls))

    _, no_fin = collect_tool_calls(messages[:-1], DAYS, CANDIDATES)
    check("finalize 없으면 None (apply 400 경로)", no_fin is None)


def rule_mock_conversation() -> None:
    print("\n[mock 대화 — 데모 시나리오]")

    r1 = asyncio.run(
        chat_turn(
            messages=[{"role": "user", "content": "스쿼트 할 때 무릎이 좀 아팠어"}],
            day=DAYS[0],
            days=DAYS,
            contraindications=[],
            catalog=[],
        )
    )
    check(
        "1턴: 통증 정도를 먼저 묻는다 (바로 안 바꿈)",
        r1["finalized"] is None and "정도" in r1["reply"],
    )

    r2 = asyncio.run(
        chat_turn(
            messages=r1["messages"] + [{"role": "user", "content": "살짝 불편한 정도였어"}],
            day=DAYS[0],
            days=DAYS,
            contraindications=[],
            catalog=[],
        )
    )
    check("2턴: finalize 카드 도착", r2["finalized"] is not None)
    check(
        "2턴: 금기 tool_event 발생",
        any(e["name"] == "flag_contraindication" for e in r2["tool_events"]),
    )
    check("턴 카운트", r2["turn"] == 2 and r2["max_turns"] == MAX_TURNS)


def rule_safety_footer() -> None:
    print("\n[안전 문구 중복 방지 — 실제 버그 재현]")

    no_pain = {"summary": "스쿼트 세트를 3→4로 늘렸습니다."}
    _append_safety_footer(no_pain, [])
    check("금기 없으면 안 붙는다", no_pain["summary"] == "스쿼트 세트를 3→4로 늘렸습니다.")

    plain = {"summary": "어깨 통증으로 푸시업을 코브라 푸시업으로 교체했습니다."}
    _append_safety_footer(plain, [{"name": "flag_contraindication", "args": {}}])
    check("금기 있고 안내 없으면 붙는다", plain["summary"].endswith(_SAFETY_FOOTER))
    check("한 번만 붙는다", plain["summary"].count("상담") == 1)

    # 실제 리포트된 버그: LLM이 프롬프트 지시를 어기고 비슷한 문구를 이미 썼을 때
    already = {
        "summary": "어깨에 살짝 불편함이 있어 푸시업을 코브라 푸시업으로 교체했습니다. "
        "통증이 계속되면 전문가와 상담하세요."
    }
    _append_safety_footer(already, [{"name": "flag_contraindication", "args": {}}])
    check(
        "LLM이 이미 비슷한 문구를 썼으면 또 안 붙인다",
        already["summary"].count("상담") == 1,
        already["summary"],
    )


def rule_name_matching() -> None:
    print("\n[운동 이름 특정 — #170]")
    names = ["레그프레스", "레그컬"]
    r, c = resolve_exercise_name("레그 프레스", names)
    check("공백 차이는 완전 일치로 특정", r == "레그프레스")
    r, c = resolve_exercise_name("레그컬", names)
    check("완전 일치", r == "레그컬")
    r, c = resolve_exercise_name("프레스", names)
    check(
        "'프레스' 는 자동 특정하지 않고 되묻기 (부분 일치)",
        r is None and c == ["레그프레스"],
        str(c),
    )
    r, c = resolve_exercise_name("레그", names)
    check("'레그' 는 둘 다 걸려 되묻기", r is None and set(c) == set(names), str(c))
    r, c = resolve_exercise_name("벤치프레스", names)
    check("목록에 없음 → 후보도 없음", r is None and c == [])
    r, c = resolve_exercise_name("컬", ["덤벨 라잉 플로어 스컬 크러셔", "레그프레스"])
    check("'컬' 이 '스컬 크러셔' 에 자동으로 붙지 않음", r is None, str(c))
    r, c = resolve_exercise_name("벤치", ["벤치프레스", "스쿼트", "플랭크"])
    check("'벤치' → 벤치프레스 (단어 경계 유일)", r == "벤치프레스")
    r, c = resolve_exercise_name("벤치", ["벤치프레스", "벤치 딥 온 플로어"])
    check("'벤치' 가 둘이면 되묻기", r is None and len(c) == 2, str(c))

    ok_args, err = validate_tool_call(
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "레그 프레스", "reps_delta": -2, "reason": "x"},
        DAYS,
        CANDIDATES,
    )
    check(
        "통과 시 인자 이름이 루틴 이름으로 정규화",
        err is None and ok_args["exercise_name"] == "레그프레스",
    )
    _, err = validate_tool_call(
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "벤치프레스", "reason": "x"},
        DAYS,
        CANDIDATES,
    )
    check(
        "목록에 없는 이름 → 되물으라는 지시 ('없습니다' 프레임 아님)",
        err is not None and "되물" in err and "없습니다" not in err,
        err or "",
    )
    _, err = validate_tool_call(
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "레그", "reason": "x"},
        DAYS,
        CANDIDATES,
    )
    check(
        "애매한 이름 → 후보와 함께 되물으라는 지시",
        err is not None and "여러 운동" in err,
        err or "",
    )
    ok_args, err = validate_tool_call(
        "replace_exercise",
        {
            "day_order": 1,
            "old_exercise_name": "레그 프레스",
            "new_exercise_ref": "ref-legext",
            "reason": "x",
        },
        DAYS,
        CANDIDATES,
    )
    check("교체도 이름 정규화", err is None and ok_args["old_exercise_name"] == "레그프레스")


def rule_truthful_card() -> None:
    print("\n[카드는 코드가 그린다 — #170]")
    events = [
        {
            "name": "adjust_intensity",
            "args": {
                "day_order": 1,
                "exercise_name": "레그컬",
                "sets_delta": 0,
                "reps_delta": -2,
                "reason": "무거웠음",
            },
        },
        {
            "name": "replace_exercise",
            "args": {
                "day_order": 1,
                "old_exercise_name": "레그프레스",
                "new_exercise_ref": "ref-legext",
                "reason": "무릎",
            },
        },
        {
            "name": "flag_contraindication",
            "args": {"body_part": "무릎", "severity": "WARN", "reason": "통증"},
        },
    ]
    ref_names = {"ref-legext": "레그 익스텐션"}
    whats = [c["what"] for c in build_card_changes(events, ref_names)]
    check(
        "변경 목록이 실행된 도구 3건 그대로",
        whats == ["레그컬 횟수 -2회", "레그프레스를 레그 익스텐션으로 교체", "무릎 주의 필요"],
        str(whats),
    )

    # 실측 2026-08-20 — 요약은 벤치프레스를 조정했다는데 실제 실행은 다른 운동
    lie = {
        "summary": "벤치프레스의 강도를 조정했습니다.",
        "changes": [{"what": "벤치프레스 횟수 감소", "why": "x"}],
    }
    card = truthful_card(lie, events[:1], ref_names)
    check(
        "LLM 이 적은 changes 를 버리고 실행 내역으로 교체",
        card["changes"] == [{"what": "레그컬 횟수 -2회", "why": "무거웠음"}],
    )
    check(
        "실제 바꾼 운동을 언급 안 한 요약은 실행 내역으로 재작성",
        "레그컬" in card["summary"] and "벤치프레스" not in card["summary"],
        card["summary"],
    )
    honest = {"summary": "레그컬 횟수를 2회 줄였어요.", "changes": []}
    check(
        "실제 운동을 언급한 요약은 그대로",
        truthful_card(honest, events[:1], ref_names)["summary"] == honest["summary"],
    )
    card = truthful_card(
        {"summary": "바꿀 건 없었어요.", "changes": [{"what": "가짜", "why": "x"}]}, [], ref_names
    )
    check(
        "실행된 도구가 없으면 changes 는 빈 목록",
        card["changes"] == [] and card["summary"] == "바꿀 건 없었어요.",
    )


def rule_rejected_calls_not_applied() -> None:
    print("\n[대화 때 거부된 호출은 적용에서 제외 — #170]")

    def call(cid: str, name: str, args: dict) -> dict:
        c = tc(name, args)
        c["tool_calls"][0]["id"] = cid
        return c

    adjust = call(
        "c1",
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "레그프레스", "reps_delta": -2, "reason": "x"},
    )
    fin = call("c2", "finalize_revision", {"summary": "s", "changes": []})
    rejected = [
        adjust,
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": json.dumps({"ok": False, "error": "무게"}),
        },
        fin,
    ]
    calls, finalized = collect_tool_calls(rejected, DAYS, CANDIDATES)
    check(
        "ok=false 였던 호출은 재수집에서 빠짐 (finalize 는 살아있음)",
        calls == [] and finalized is not None,
        str(calls),
    )
    accepted = [
        adjust,
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"ok": True})},
        fin,
    ]
    calls, _ = collect_tool_calls(accepted, DAYS, CANDIDATES)
    check("ok=true 였던 호출은 그대로", len(calls) == 1)


def rule_weight_talk_scope() -> None:
    print()
    print("[무게 얘기 감지 범위 — #170 증상 3]")

    def u(text: str) -> dict:
        return {"role": "user", "content": text}

    def a(text: str) -> dict:
        return {"role": "assistant", "content": text}

    def call(cid: str, name: str, args: dict) -> dict:
        c = tc(name, args)
        c["tool_calls"][0]["id"] = cid
        return c

    def result(cid: str, ok: bool) -> dict:
        return {"role": "tool", "tool_call_id": cid, "content": json.dumps({"ok": ok})}

    heavy = ("무거", "무겁", "가벼", "무게", "중량", "kg", "킬로")

    def weight_talk(msgs: list[dict]) -> bool:
        return any(t in text for text in user_texts_since_last_change(msgs) for t in heavy)

    # "무거웠어요 → 낮출까요? → 네" — 확인이 다음 턴에 와도 잡힌다
    msgs = [u("벤치프레스 무거웠어요"), a("무게를 낮춰볼까요?"), u("네")]
    check("확인이 다음 턴에 와도 무게 얘기로 본다", weight_talk(msgs))

    # 벤치프레스 무게 조정이 끝난 뒤 스쿼트 횟수 얘기는 무게 얘기가 아니다
    adjust = call(
        "c1",
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "벤치프레스", "load_scale": 0.8, "reason": "x"},
    )
    msgs = [
        u("벤치프레스 무거웠어요"),
        adjust,
        result("c1", True),
        a("낮췄어요"),
        u("스쿼트는 횟수 좀 줄여주세요"),
    ]
    check("조정이 끝난 뒤의 다른 운동 얘기는 구간이 끊긴다", not weight_talk(msgs))
    check(
        "구간에 남는 발화는 마지막 것뿐",
        user_texts_since_last_change(msgs) == ["스쿼트는 횟수 좀 줄여주세요"],
    )

    # 거부된 호출은 구간을 끊지 않는다 — 아직 조정이 안 됐다
    rejected = call(
        "c2",
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "벤치프레스", "reps_delta": -2, "reason": "x"},
    )
    msgs = [
        u("벤치프레스 무거웠어요"),
        rejected,
        result("c2", False),
        a("무게로 낮춰볼게요"),
        u("네"),
    ]
    check("거부된 호출은 구간을 끊지 않는다", weight_talk(msgs))

    check(
        "무게 얘기가 없으면 False", not weight_talk([u("스쿼트 횟수 줄여줘"), a("네"), u("좋아요")])
    )


def rule_weight_needs_load_scale() -> None:
    print()
    print("[무게 얘기면 load_scale — #170 증상 3]")
    no_scale = {"exercise_name": "벤치프레스", "reps_delta": -2}
    with_scale = {"exercise_name": "벤치프레스", "load_scale": 0.8}
    check(
        "무게 얘기 + 바벨 + load_scale 없음 → 되돌림",
        requires_load_scale("adjust_intensity", no_scale, True, False),
    )
    check(
        "load_scale 있으면 통과",
        not requires_load_scale("adjust_intensity", with_scale, True, False),
    )
    check(
        "무게 얘기가 없었으면 통과",
        not requires_load_scale("adjust_intensity", no_scale, False, False),
    )
    check(
        "맨몸 운동(푸시업)은 세트·횟수 조정 허용",
        not requires_load_scale("adjust_intensity", no_scale, True, True),
    )
    check(
        "교체 도구엔 적용 안 함", not requires_load_scale("replace_exercise", no_scale, True, False)
    )


def rule_turn_boundary() -> None:
    print()
    print("[8턴 경계 — #171 증상 1]")
    user = {"role": "user", "content": "x"}
    check("8번째 발화는 처리된다 (마지막 턴)", not turns_exceeded([user] * MAX_TURNS))
    check("9번째 발화부터 차단", turns_exceeded([user] * (MAX_TURNS + 1)))
    check("7번째까지는 당연히 통과", not turns_exceeded([user] * (MAX_TURNS - 1)))


def rule_last_call_wins() -> None:
    print()
    print("[같은 운동은 마지막 호출만 — #171 증상 2 (취소)]")

    def call(cid: str, name: str, args: dict) -> dict:
        c = tc(name, args)
        c["tool_calls"][0]["id"] = cid
        return c

    def ok(cid: str) -> dict:
        return {"role": "tool", "tool_call_id": cid, "content": json.dumps({"ok": True})}

    first = call(
        "c1",
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "레그프레스", "sets_delta": -1, "reason": "힘듦"},
    )
    cancel = call(
        "c2",
        "adjust_intensity",
        {
            "day_order": 1,
            "exercise_name": "레그프레스",
            "sets_delta": 0,
            "reps_delta": 0,
            "reason": "취소",
        },
    )
    other = call(
        "c3",
        "adjust_intensity",
        {"day_order": 1, "exercise_name": "레그컬", "reps_delta": -2, "reason": "x"},
    )
    fin = call("c4", "finalize_revision", {"summary": "s", "changes": []})

    msgs = [first, ok("c1"), other, ok("c3"), cancel, ok("c2"), fin, ok("c4")]
    calls, finalized = collect_tool_calls(msgs, DAYS, CANDIDATES)
    names = [(c["name"], c["args"]["exercise_name"], c["args"].get("sets_delta", 0)) for c in calls]
    check(
        "레그프레스는 마지막(취소) 호출만, 레그컬은 그대로",
        names == [("adjust_intensity", "레그컬", 0), ("adjust_intensity", "레그프레스", 0)],
        str(names),
    )
    check("취소 호출은 무효 adjust 로 판정", adjust_is_noop(calls[-1]["args"]))

    days_after, applied = apply_changes_to_days(DAYS, calls, CATALOG_BY_REF)
    legpress = next(e for e in days_after[0]["exercises"] if e["name"] == "레그프레스")
    check(
        "취소된 조정은 적용되지 않는다 (세트 4 그대로)",
        legpress["sets"] == 4,
        str(legpress["sets"]),
    )
    check(
        "적용 기록에도 취소 호출은 남지 않는다",
        [a["args"]["exercise_name"] for a in applied] == ["레그컬"],
        str(applied),
    )

    card = build_card_changes(calls, {})
    check(
        "카드에도 취소된 조정은 없다", [c["what"] for c in card] == ["레그컬 횟수 -2회"], str(card)
    )

    # 카드는 대화 전체 기준 — 2턴에 조정, 3턴에 finalize 여도 변경이 실린다
    check("finalize 는 살아있다", finalized is not None)


def rule_replace_back() -> None:
    print()
    print("[교체 취소 = 원래 운동으로 되돌리기 — #171]")
    day = {
        "day_order": 1,
        "title": "t",
        "exercises": [
            {
                "name": "레그프레스",
                "exercise_ref": "ref-legpress",
                "muscle_group": "대퇴사두",
                "sets": 4,
                "reps": 10,
            }
        ],
    }
    ok_args, err = validate_tool_call(
        "replace_exercise",
        {
            "day_order": 1,
            "old_exercise_name": "레그프레스",
            "new_exercise_ref": "ref-legpress",
            "reason": "취소",
        },
        [day],
        CANDIDATES,
    )
    check("원래 운동 ref 로의 교체는 후보 밖이어도 통과", err is None, err or "")
    catalog = {
        **CATALOG_BY_REF,
        "ref-legpress": {
            "exercise_ref": "ref-legpress",
            "name_ko": "레그프레스",
            "name_en": "leg press",
        },
    }
    days_after, applied = apply_changes_to_days(
        [day], [{"name": "replace_exercise", "args": ok_args}], catalog
    )
    check(
        "적용해도 운동이 그대로고 기록도 없다",
        days_after[0]["exercises"][0]["name"] == "레그프레스" and applied == [],
    )
    card = build_card_changes(
        [{"name": "replace_exercise", "args": ok_args}], {"ref-legpress": "레그프레스"}
    )
    check("카드에도 실리지 않는다", card == [])


def main() -> int:
    rule_validation()
    rule_apply()
    rule_recollect()
    rule_mock_conversation()
    rule_safety_footer()
    rule_name_matching()
    rule_truthful_card()
    rule_rejected_calls_not_applied()
    rule_weight_talk_scope()
    rule_weight_needs_load_scale()
    rule_turn_boundary()
    rule_last_call_wins()
    rule_replace_back()

    print()
    if _failures:
        print(f"실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
