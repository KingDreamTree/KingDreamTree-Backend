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


def main() -> int:
    rule_validation()
    rule_apply()
    rule_recollect()
    rule_mock_conversation()
    rule_safety_footer()

    print()
    if _failures:
        print(f"실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
