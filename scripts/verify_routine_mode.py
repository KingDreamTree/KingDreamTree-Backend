"""L0 모드 판정 검증 (DB·API 키 불필요).

    python scripts/verify_routine_mode.py

핵심: **근육형을 CUT 으로, 마른비만을 BALANCE 로 떨어뜨리지 않는가.**
BMI 를 1차로 쓰면 둘 다 틀린다 (docs/routine-logic-decision.md §2-H8).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.routine_mode import RoutineMode, decide_mode  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)


def case(label: str, inbody, expected: str, expected_basis: str | None = None) -> None:
    got = decide_mode(inbody)
    ok = got["mode"] == expected and (expected_basis is None or got["basis"] == expected_basis)
    check(label, ok, f"{got['mode']} / {got['basis']} / value={got['value']}")


def main() -> int:
    print("L0 모드 판정 검증\n")

    print("1. 오분류 방지 (H8 — 순서 반전의 이유)")
    # 근육형: BMI 만 보면 CUT 으로 떨어진다. 체지방률이 1차여야 BALANCE.
    case(
        "근육형(BMI 26·체지방 12%)은 BALANCE",
        {"gender": "MALE", "bmi": 26.0, "body_fat_percentage": 12.0, "weight": 82.0},
        RoutineMode.BALANCE,
        "BODY_FAT_MEASURED",
    )
    # 마른비만: BMI 만 보면 BALANCE 로 놓친다. 체지방률이 1차여야 CUT.
    case(
        "마른비만(BMI 22·체지방 28%)은 CUT",
        {"gender": "MALE", "bmi": 22.0, "body_fat_percentage": 28.0, "weight": 65.0},
        RoutineMode.CUT,
        "BODY_FAT_MEASURED",
    )

    print("\n2. 기본 판정")
    case(
        "남성 체지방 27% → CUT",
        {"gender": "MALE", "body_fat_percentage": 27.0},
        RoutineMode.CUT,
    )
    case(
        "남성 체지방 18.9%(샘플) → BALANCE",
        {"gender": "MALE", "body_fat_percentage": 18.9},
        RoutineMode.BALANCE,
    )
    case(
        "여성 체지방 30% → BALANCE (컷 36)",
        {"gender": "FEMALE", "body_fat_percentage": 30.0},
        RoutineMode.BALANCE,
    )
    case(
        "여성 체지방 38% → CUT",
        {"gender": "FEMALE", "body_fat_percentage": 38.0},
        RoutineMode.CUT,
    )
    case(
        "성별 미상 30% → BALANCE (보수적으로 높은 컷 적용)",
        {"body_fat_percentage": 30.0},
        RoutineMode.BALANCE,
    )

    print("\n3. 폴백 — 체지방률을 못 읽으면 BMI 로 CUT 을 트리거하지 않는다")
    # ⚠️ BMI 폴백 CUT 은 폐기됐다 (2026-08-14). 체지방률 1차 원칙을 세워놓고
    #    폴백에서 대리 지표로 감량을 처방하면 그 원칙을 스스로 뒤집는다 —
    #    근육형 오분류를 우리가 만들어내는 셈이다. 확신 없으면 BALANCE.
    case(
        "체지방률 없음 → 체지방량÷체중 파생 (12/63.5=18.9% → BALANCE)",
        {"gender": "MALE", "body_fat_mass": 12.0, "weight": 63.5, "bmi": 26.0},
        RoutineMode.BALANCE,
        "BODY_FAT_DERIVED",
    )
    case(
        "체지방 정보 전무 → BMI 26 이어도 BALANCE",
        {"gender": "MALE", "bmi": 26.0},
        RoutineMode.BALANCE,
        "BODY_FAT_UNAVAILABLE",
    )
    case(
        "BMI 31 (고도비만) 이어도 BALANCE — 대리 지표로 감량 처방 안 함",
        {"gender": "MALE", "bmi": 31.0},
        RoutineMode.BALANCE,
        "BODY_FAT_UNAVAILABLE",
    )
    case(
        "체중/신장만 있어도 BALANCE",
        {"gender": "MALE", "weight": 80.0, "height": 170.0},
        RoutineMode.BALANCE,
        "BODY_FAT_UNAVAILABLE",
    )
    case("인바디 없음 → BALANCE", None, RoutineMode.BALANCE, "NO_INBODY")
    case("빈 인바디 → BALANCE", {}, RoutineMode.BALANCE, "NO_INBODY")
    case(
        "수치를 하나도 못 읽음 → BALANCE",
        {"gender": "MALE", "device_type": "InBody570"},
        RoutineMode.BALANCE,
        "BODY_FAT_UNAVAILABLE",
    )

    print("\n4. 경계·이상값")
    case(
        "정확히 컷값(25.0) → CUT (이상 포함)",
        {"gender": "MALE", "body_fat_percentage": 25.0},
        RoutineMode.CUT,
    )
    case(
        "컷값 직전(24.9) → BALANCE",
        {"gender": "MALE", "body_fat_percentage": 24.9},
        RoutineMode.BALANCE,
    )
    case(
        "체지방률 0/음수는 무효 → 판정 불가로 BALANCE",
        {"gender": "MALE", "body_fat_percentage": 0, "bmi": 26.0},
        RoutineMode.BALANCE,
        "BODY_FAT_UNAVAILABLE",
    )

    print("\n5. 추적 가능성")
    muscular = decide_mode(
        {"gender": "MALE", "bmi": 26.0, "body_fat_percentage": 12.0, "weight": 82.0}
    )
    check("판정 근거(basis) 노출", muscular["basis"] == "BODY_FAT_MEASURED")
    check("사용자용 문구 존재", bool(muscular["reason"]), muscular["reason"])
    unavailable = decide_mode({"gender": "MALE", "bmi": 26.0})
    check(
        "판정 불가는 별도 basis 로 구분",
        unavailable["basis"] == "BODY_FAT_UNAVAILABLE",
        "인바디 자체가 없는 NO_INBODY 와 구분된다",
    )
    check(
        "BMI 는 참고값으로 남는다 (트리거 아님)",
        unavailable["value"] == 26.0 and unavailable["cutoff"] is None,
        f"value={unavailable['value']} cutoff={unavailable['cutoff']}",
    )

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
