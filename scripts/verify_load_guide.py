"""시작 중량 가이드 검증 — 계산 규칙 + 피드백 배율 왕복.

    python scripts/verify_load_guide.py

━━ 무엇을 지키려는 검사인가 ━━

이 기능은 **D9(1RM 추정 폐기)의 경계 위에** 있다. 경계를 넘는 순간 서비스의
"모든 수치에 출처가 있다"가 무너지므로, 넘지 않았다는 것을 검사로 고정한다:

  · 체중을 모르면 kg 을 내지 않는다 — 인바디 없이 숫자를 지어내면 안 된다
  · 맨몸 운동에는 안 낸다 — 들 무게가 없다
  · 항상 **내림** 스냅 — 반올림으로 무거운 쪽에 서지 않는다
  · 실제 존재하는 덤벨 단위로만 — "7.3kg" 은 집을 수 없다
  · 배율은 범위 안에서만 — 피드백이 폭주해도 무게가 튀지 않는다
  · 배율은 **덮어쓰기**지 곱하기가 아니다 — "무겁다" 두 번에 절반이 되면 안 된다
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.load_guide import (  # noqa: E402
    _ADJUST_RANGE,
    _DUMBBELL_STEPS,
    guide_text,
    starting_load,
)

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []

M70 = {"body": {"weight": 70, "gender": "MALE"}}
F55 = {"body": {"weight": 55, "gender": "FEMALE"}}


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(label)


def rule_no_guess() -> None:
    print("1. 근거 없으면 숫자를 내지 않는다 (D9 경계)")
    check("맨몸 운동은 안 냄", starting_load("가슴", ["BODY WEIGHT"], M70) is None)
    check("장비 목록이 비면 안 냄", starting_load("등", [], M70) is None)
    check("인바디 없으면 안 냄", starting_load("등", ["DUMBBELL"], None) is None)
    check("체중 없으면 안 냄", starting_load("등", ["DUMBBELL"], {"body": {"gender": "MALE"}}) is None)
    check(
        "체중이 비상식적이면 안 냄",
        starting_load("등", ["DUMBBELL"], {"body": {"weight": 5}}) is None,
    )
    check("모르는 근육군은 안 냄", starting_load("가슴지느러미", ["DUMBBELL"], M70) is None)
    check("가이드 없으면 문구도 없음", guide_text(None) is None)


def rule_snap() -> None:
    print("\n2. 실제 집을 수 있는 무게로만")
    for mg in ("등", "어깨", "대퇴사두", "삼두", "코어"):
        load = starting_load(mg, ["DUMBBELL"], M70)
        assert load, mg
        ok = load["min_kg"] in _DUMBBELL_STEPS and load["max_kg"] in _DUMBBELL_STEPS
        check(f"{mg}: 덤벨 실물 단위", ok, f'{load["min_kg"]}~{load["max_kg"]}kg')
    load = starting_load("등", ["DUMBBELL"], M70)
    check("범위가 뒤집히지 않음", load["min_kg"] <= load["max_kg"])

    # 아주 가벼운 사람이라도 가장 가벼운 덤벨 밑으로 내려가지 않는다
    tiny = starting_load("후면 어깨", ["DUMBBELL"], {"body": {"weight": 30, "gender": "FEMALE"}})
    check("최소 단위 아래로 안 내려감", tiny is not None and tiny["min_kg"] >= _DUMBBELL_STEPS[0])

    bar = starting_load("가슴", ["BARBELL"], M70)
    check("바벨은 봉(20kg)이 하한", bar is not None and bar["min_kg"] == 20, str(bar))


def rule_conservative() -> None:
    print("\n3. 보수적인가 (안전 방향)")
    # 고립운동이 복합운동보다 가벼워야 한다 — 뒤집히면 레이즈를 로우처럼 든다
    row = starting_load("등", ["DUMBBELL"], M70)
    raise_ = starting_load("어깨", ["DUMBBELL"], M70)
    rear = starting_load("후면 어깨", ["DUMBBELL"], M70)
    check("측면 레이즈 < 로우", raise_["max_kg"] < row["min_kg"], f'{raise_["max_kg"]} < {row["min_kg"]}')
    check("후면 어깨 ≤ 측면 레이즈", rear["max_kg"] <= raise_["max_kg"])

    # 같은 근육군이면 체중이 큰 쪽이 더 무겁거나 같다 (역전되면 계산이 깨진 것)
    light = starting_load("등", ["DUMBBELL"], {"body": {"weight": 50, "gender": "MALE"}})
    heavy = starting_load("등", ["DUMBBELL"], {"body": {"weight": 90, "gender": "MALE"}})
    check("체중이 크면 시작 무게도 크거나 같음", heavy["min_kg"] >= light["min_kg"])

    check(
        "문구가 «권장» 이 아니라 출발점으로 읽힌다",
        "시작" in guide_text(row) and "권장" not in guide_text(row),
        guide_text(row),
    )
    check("근거 문장이 개인 처방으로 읽히지 않음", "비슷한 체중" in row["basis"], row["basis"])


def rule_adjust() -> None:
    print("\n4. 피드백 배율")
    base = starting_load("등", ["DUMBBELL"], M70)
    down = starting_load("등", ["DUMBBELL"], M70, adjust=0.7)
    up = starting_load("등", ["DUMBBELL"], M70, adjust=1.3)
    check("낮추면 가벼워짐", down["min_kg"] <= base["min_kg"], f'{down["min_kg"]} ≤ {base["min_kg"]}')
    check("올리면 무거워짐", up["min_kg"] >= base["min_kg"], f'{up["min_kg"]} ≥ {base["min_kg"]}')

    # 폭주 방어 — 범위 밖 배율은 잘린다
    insane_low = starting_load("등", ["DUMBBELL"], M70, adjust=0.01)
    clamped_low = starting_load("등", ["DUMBBELL"], M70, adjust=_ADJUST_RANGE[0])
    insane_high = starting_load("등", ["DUMBBELL"], M70, adjust=99.0)
    clamped_high = starting_load("등", ["DUMBBELL"], M70, adjust=_ADJUST_RANGE[1])
    check("과소 배율은 하한에서 잘림", insane_low == clamped_low)
    check("과대 배율은 상한에서 잘림", insane_high == clamped_high)
    check("적용된 배율을 응답에 남김", base["adjust"] == 1.0 and down["adjust"] == 0.7)


def rule_gender() -> None:
    print("\n5. 성별 보정")
    m = starting_load("등", ["DUMBBELL"], M70)
    f = starting_load("등", ["DUMBBELL"], {"body": {"weight": 70, "gender": "FEMALE"}})
    unknown = starting_load("등", ["DUMBBELL"], {"body": {"weight": 70}})
    check("같은 체중이면 여성 ≤ 남성", f["min_kg"] <= m["min_kg"], f'{f["min_kg"]} ≤ {m["min_kg"]}')
    check("성별 미상이면 보정 안 함(남성과 동일)", unknown == m)
    check("55kg 여성도 최소 단위 이상", starting_load("어깨", ["DUMBBELL"], F55)["min_kg"] >= 2)


def rule_merge() -> None:
    print("\n6. 배율 병합 — 곱하지 않고 덮어쓴다")
    from app.services.coach_chat import merge_load_adjust

    routine = {"raw_response": {"load_adjust": {"ref-a": 0.8}}}
    applied = [
        {"function": "adjust_intensity", "args": {"load_scale": 0.8, "exercise_ref": "ref-a"}},
        {"function": "adjust_intensity", "args": {"load_scale": 1.2, "exercise_ref": "ref-b"}},
        {"function": "adjust_intensity", "args": {"sets_delta": 1}},  # 무게 언급 없음
        {"function": "replace_exercise", "args": {"load_scale": 0.5}},  # 다른 도구
    ]
    merged = merge_load_adjust(routine, applied)
    check("같은 운동을 또 낮춰도 0.64 가 되지 않음", merged["ref-a"] == 0.8, str(merged))
    check("새 운동 배율이 추가됨", merged["ref-b"] == 1.2)
    check("무게 언급 없는 조정은 안 들어감", len(merged) == 2, str(merged))

    kept = merge_load_adjust({"raw_response": {"load_adjust": {"ref-x": 0.9}}}, [])
    check("이전 배율은 승계된다 (버전이 바뀌어도)", kept == {"ref-x": 0.9})


def rule_persistence() -> None:
    """배율이 **DB 에 남고 버전이 바뀌어도 살아남는가.**

    ⚠️ 이게 이 기능의 진짜 실패 지점이다. 계산이 맞아도 배율이 유실되면
       사용자는 "무겁다고 말했는데 다음에 또 그 무게"를 보게 된다.
       raw_response 를 통째로 갈아치우는 곳이 **세 군데**라 각각 확인한다.
    """
    print("\n7. 배율이 버전 전환에서 살아남는가 (유실 지점 3곳)")
    root = Path(__file__).resolve().parent.parent

    coach = (root / "app/routes/coach_chat.py").read_text(encoding="utf-8")
    check("① 코치챗 apply 가 배율을 병합·저장", "merge_load_adjust(routine, applied)" in coach)

    handler = (root / "app/worker/handlers/routine.py").read_text(encoding="utf-8")
    # ⚠️ 문자열 검사인 이유 — 이 두 곳은 실제 LLM·DB 왕복이라 단위 검사로
    #    태우기 어렵다. 대신 "그 줄이 사라지면 잡힌다"만 보장한다.
    check(
        "② 한 방 피드백(_patch)이 새 배율을 **병합**한다 (승계만 하면 안 된다)",
        "merge_load_adjust(row, applied)" in handler,
    )
    check("③ 재생성(_generate)이 이전 버전 배율을 물려받음", '"load_adjust": carried_load_adjust' in handler)

    # ⚠️ 프롬프트가 금지하면 도구가 있어도 LLM 이 안 쓴다 — 실측으로 겪었다.
    patch_prompt = (root / "app/prompts/routine_patch.py").read_text(encoding="utf-8")
    check("한 방 피드백 프롬프트가 load_scale 사용을 안내", "load_scale" in patch_prompt)
    check(
        "«세트·횟수·휴식으로만» 같은 전면 금지 문구가 남아있지 않음",
        "휴식으로만 조정한다" not in patch_prompt,
    )
    chat_prompt = (root / "app/prompts/coach_chat.py").read_text(encoding="utf-8")
    check("코치 대화 프롬프트도 load_scale 안내", "load_scale" in chat_prompt)

    # 두 경로가 **같은 도구 객체**를 쓰는가 (복사하면 한쪽만 바뀐다)
    from app.prompts.routine_patch import TOOLS as PATCH_TOOLS

    adj = next(t for t in PATCH_TOOLS if t["function"]["name"] == "adjust_intensity")
    check("한 방 피드백 도구에도 load_scale 인자가 있음", "load_scale" in adj["function"]["parameters"]["properties"])

    routes = (root / "app/routes/routines.py").read_text(encoding="utf-8")
    check("조회가 저장된 배율을 실제로 읽음", '.get("load_adjust")' in routes)


def main() -> int:
    print("시작 중량 가이드 검증\n")
    rule_no_guess()
    rule_snap()
    rule_conservative()
    rule_adjust()
    rule_gender()
    rule_merge()
    rule_persistence()

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
