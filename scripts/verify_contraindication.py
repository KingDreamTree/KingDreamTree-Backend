"""금기 강제 검증 — 통증이 기록만 되고 루틴에 반영 안 되는 누수를 막는가.

    python scripts/verify_contraindication.py

이 검사가 존재하는 이유: F12 프롬프트는 "통증 부위 운동을 바꿔라"고 지시하지만
그건 지시일 뿐 보장이 아니다. LLM 이 금기만 등록하고 운동을 안 건드리면
"주의 부위 등록됨" 배지만 뜨고 루틴은 그대로 나간다 — 화면상 처리된 것처럼
보여서 더 위험하다.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services import contraindication  # noqa: E402
from app.services.contraindication import JOINT_GROUPS, affected_groups, enforce  # noqa: E402
from app.services.exercise_catalog import SLOT_BODY_PARTS  # noqa: E402

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


def _days() -> list[dict]:
    return [
        {
            "day_order": 1,
            "exercises": [
                {"name": "바벨 스쿼트", "muscle_group": "대퇴사두", "sets": 4, "reps": 10},
                {"name": "레그컬", "muscle_group": "햄스트링·둔근", "sets": 3, "reps": 12},
                {"name": "벤치프레스", "muscle_group": "가슴", "sets": 4, "reps": 10},
                {
                    "name": "트레드밀",
                    "muscle_group": None,
                    "exercise_kind": "CARDIO",
                    "duration_min": 20,
                },
            ],
        },
        {
            "day_order": 2,
            "exercises": [
                {"name": "랫풀다운", "muscle_group": "등", "sets": 4, "reps": 10},
                {"name": "사이드 레이즈", "muscle_group": "어깨", "sets": 3, "reps": 12},
            ],
        },
    ]


def rule_mapping_names_are_real() -> None:
    """근육군 이름 오타는 조용히 아무것도 매칭하지 않는다 — 가장 위험한 실패."""
    print("\n[근육군 이름 정합]")
    known = set(SLOT_BODY_PARTS)
    for joint, groups in JOINT_GROUPS.items():
        unknown = groups - known
        check(f"{joint} 매핑이 실제 근육군만 참조", not unknown, str(unknown) if unknown else "")


def rule_warn_reduces_volume() -> None:
    print("\n[WARN — 볼륨 감소]")
    days, enforced = enforce(_days(), [{"body_part": "무릎", "severity": "WARN"}], [])
    d1 = {e["name"]: e for e in days[0]["exercises"]}

    check("스쿼트 세트 감소 (4→3)", d1["바벨 스쿼트"]["sets"] == 3, str(d1["바벨 스쿼트"]["sets"]))
    check("레그컬 세트 감소 (3→2)", d1["레그컬"]["sets"] == 2, str(d1["레그컬"]["sets"]))
    check("무관한 가슴은 그대로 (4)", d1["벤치프레스"]["sets"] == 4, str(d1["벤치프레스"]["sets"]))
    check("유산소는 건드리지 않음", "트레드밀" in d1)
    check("강제 기록 남김", len(enforced) == 2, f"{len(enforced)}건")
    check(
        "기록에 사유 포함",
        all(c["args"].get("reason") for c in enforced),
        str(enforced[0]["args"].get("reason")),
    )


def rule_block_removes() -> None:
    print("\n[BLOCK — 운동 제외]")
    days, enforced = enforce(_days(), [{"body_part": "무릎", "severity": "BLOCK"}], [])
    names = [e["name"] for e in days[0]["exercises"]]

    check("스쿼트 제외됨", "바벨 스쿼트" not in names, str(names))
    check("레그컬 제외됨", "레그컬" not in names, str(names))
    check("가슴은 남음", "벤치프레스" in names)
    check("유산소는 남음", "트레드밀" in names)
    check("제외 기록에 removed 표기", any(c["args"].get("removed") for c in enforced))


def rule_never_empties_a_day() -> None:
    """전부 BLOCK 대상이어도 Day 를 비우지 않는다 — 빈 날은 화면에서 설명이 안 된다."""
    print("\n[Day 를 비우지 않는다]")
    legs_only = [
        {
            "day_order": 1,
            "exercises": [
                {"name": "스쿼트", "muscle_group": "대퇴사두", "sets": 4, "reps": 10},
                {"name": "런지", "muscle_group": "햄스트링·둔근", "sets": 3, "reps": 12},
            ],
        }
    ]
    days, enforced = enforce(legs_only, [{"body_part": "무릎", "severity": "BLOCK"}], [])
    left = days[0]["exercises"]
    check("Day 가 비지 않음", len(left) >= 1, f"{len(left)}개")
    check("남은 운동은 최소 볼륨(1세트)", left[0]["sets"] == 1, str(left[0]["sets"]))


def rule_respects_llm_work() -> None:
    """LLM 이 이미 처리한 운동은 중복으로 깎지 않는다."""
    print("\n[LLM 이 처리한 건 건드리지 않는다]")
    applied = [
        {
            "function": "replace_exercise",
            "args": {"day_order": 1, "old_exercise_name": "바벨 스쿼트"},
        }
    ]
    days, enforced = enforce(_days(), [{"body_part": "무릎", "severity": "WARN"}], applied)
    d1 = {e["name"]: e for e in days[0]["exercises"]}
    check("LLM 이 바꾼 스쿼트는 그대로 (4)", d1["바벨 스쿼트"]["sets"] == 4)
    check("나머지는 강제 적용됨", d1["레그컬"]["sets"] == 2)
    check("강제 기록은 1건만", len(enforced) == 1, f"{len(enforced)}건")


def rule_part_name_normalization() -> None:
    print("\n[부위명 정규화 — LLM 이 자유 문자열로 준다]")
    for raw, expected in [
        ("무릎", "무릎"),
        ("왼쪽 무릎", "무릎"),
        ("양무릎", "무릎"),
        ("회전근개", "어깨"),
        ("요추", "허리"),
        ("엘보", "팔꿈치"),
    ]:
        got = contraindication.normalize_part(raw)
        check(f"'{raw}' → {expected}", got == expected, str(got))

    check("모르는 부위는 None (강제 안 함)", contraindication.normalize_part("새끼발가락") is None)


def rule_severity_precedence() -> None:
    print("\n[BLOCK 이 WARN 을 이긴다]")
    groups = affected_groups(
        [
            {"body_part": "무릎", "severity": "WARN"},
            {"body_part": "무릎", "severity": "BLOCK"},
        ]
    )
    check("대퇴사두 = BLOCK", groups.get("대퇴사두") == "BLOCK", str(groups.get("대퇴사두")))


def rule_noop_when_no_contraindication() -> None:
    print("\n[금기 없으면 아무것도 하지 않는다]")
    original = _days()
    days, enforced = enforce(original, [], [])
    check("변경 없음", enforced == [])
    check("원본 그대로 반환", days is original)


def rule_does_not_mutate_input() -> None:
    print("\n[원본 불변]")
    original = _days()
    before = original[0]["exercises"][0]["sets"]
    enforce(original, [{"body_part": "무릎", "severity": "WARN"}], [])
    check("입력 days 가 바뀌지 않음", original[0]["exercises"][0]["sets"] == before)


def rule_interpretation_reflects_applied() -> None:
    """해석문이 **거부된 제안**까지 말하지 않는가.

    ⚠️ 실측(2026-08-15 라이브): LLM 이 대퇴사두 슬롯을 다른 근육군 운동으로
       바꾸려다 검증에서 거부됐는데, 해석문에는 "금기 등록, 운동 교체 (2건)" 이
       그대로 남았다. 화면에는 교체가 없는데 문구만 교체를 주장한 셈 —
       "통증이 기록만 되고 반영 안 됨"과 같은 계열의 사고다.
    """
    print("\n[해석문 = 실제 적용된 것만]")
    from app.worker.handlers.routine import _interpretation

    rejected = {"llm_message": None, "interpretation": "금기 등록, 운동 교체 (2건)"}
    applied = [{"function": "enforce_contraindication", "args": {}} for _ in range(4)]
    added = [{"body_part": "무릎", "severity": "WARN"}]

    got = _interpretation(rejected, applied, added)
    check("거부된 '운동 교체'는 해석문에서 빠진다", "운동 교체" not in got, got)
    check("실제 적용분은 들어간다", "볼륨 조정" in got and "금기 등록" in got, got)
    check("같은 종류는 묶어서 표기", "4건" in got, got)
    check("적용 0건이면 금기만", _interpretation(rejected, [], added) == "금기 등록")
    check("전부 없으면 변경 없음", _interpretation(rejected, [], []) == "변경 사항 없음")
    check(
        "LLM 이 직접 쓴 산문은 존중",
        _interpretation({"llm_message": "무릎 부담을 줄였어요"}, applied, added)
        == "무릎 부담을 줄였어요",
    )


def main() -> int:
    print("금기 강제 검증")
    rule_mapping_names_are_real()
    rule_warn_reduces_volume()
    rule_block_removes()
    rule_never_empties_a_day()
    rule_respects_llm_work()
    rule_part_name_normalization()
    rule_severity_precedence()
    rule_noop_when_no_contraindication()
    rule_does_not_mutate_input()
    rule_interpretation_reflects_applied()

    print()
    if _failures:
        print(f"실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
