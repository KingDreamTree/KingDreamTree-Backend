"""L0 목표 모드 판정 — CUT(감량 병행) / BALANCE(기본).

⚠️ **체지방률이 1차 기준이다. BMI 는 폴백이다.**

    CUT 판정은 인바디가 있을 때만 발동한다. 그런데 인바디가 있다는 건 체지방률을
    이미 알고 있다는 뜻이다. 그 상태에서 지방의 **대리 지표(BMI)** 를 직접
    측정값(체지방률)보다 먼저 보면 근거의 강도 순서가 뒤집힌다.

    실제로 양방향 오분류가 난다:
        근육형   BMI 26 · 체지방 12%  →  BMI 1차면 CUT (감량 불필요한데 감량 처방)
        마른비만 BMI 22 · 체지방 28%  →  BMI 1차면 BALANCE (감량 필요한데 놓침)

    대한비만학회 지침도 BMI 가 근육량을 반영하지 못하는 한계를 명시한다.
    따라서 "실측 체성분이 있으면 그것을 우선한다"가 지침 취지에 더 부합한다.
    (docs/routine-logic-decision.md §2-H8)

⚠️ 인바디가 없으면 무조건 BALANCE. 사진만으로 체지방률을 추정해 "감량하세요"라고
   말하는 것은 방어할 수 없다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

#: 체지방률 컷오프 (%). JCEM 2025 "Defining Overweight and Obesity by Percent Body Fat".
#: ⚠️ D1 회의 결정 대상 — 값이 바뀌면 여기만 고친다.
BODY_FAT_CUTOFF: dict[str, float] = {"MALE": 25.0, "FEMALE": 36.0}

#: 성별 미상일 때. 남성 기준을 쓰면 여성이 과도하게 CUT 으로 떨어지므로
#: **보수적으로 높은 쪽(여성 기준)** 을 쓴다 — 잘못 감량시키는 쪽이 더 해롭다.
BODY_FAT_CUTOFF_UNKNOWN = 36.0

#: BMI 폴백 컷오프. 대한비만학회 아시아-태평양 기준.
BMI_CUTOFF = 25.0


class RoutineMode(StrEnum):
    CUT = "CUT"
    BALANCE = "BALANCE"


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def body_fat_percentage(inbody: dict[str, Any]) -> tuple[float | None, str]:
    """체지방률과 그 출처를 돌려준다.

    폴백 체인:
        1. body_fat_percentage      직접 추출값
        2. body_fat_mass ÷ weight   파생값 — 결과지에 둘 다 있어 동시 실패 확률이 낮고,
                                    ocr.py 가 이미 같은 항등식으로 검증 중이라 일관됨
    """
    direct = _as_float(inbody.get("body_fat_percentage"))
    if direct is not None:
        return direct, "MEASURED"

    fat_mass = _as_float(inbody.get("body_fat_mass"))
    weight = _as_float(inbody.get("weight"))
    if fat_mass is not None and weight is not None:
        return round(fat_mass / weight * 100, 1), "DERIVED"

    return None, "NONE"


def decide_mode(inbody: dict[str, Any] | None) -> dict[str, Any]:
    """L0 모드를 판정한다. **결정론적** — 같은 인바디면 항상 같은 결과.

    Returns:
        {"mode", "basis", "value", "cutoff", "reason"}
        basis: BODY_FAT_MEASURED | BODY_FAT_DERIVED | BMI_FALLBACK | NO_INBODY
        ⚠️ basis 를 job.result 와 진단 문구에 남긴다. BMI_FALLBACK 으로 판정된
           건은 근육형 오분류 가능성이 있으므로 나중에 추적할 수 있어야 한다.
    """
    if not inbody:
        return {
            "mode": RoutineMode.BALANCE,
            "basis": "NO_INBODY",
            "value": None,
            "cutoff": None,
            "reason": "인바디가 없어 기본 모드로 구성했습니다.",
        }

    gender = str(inbody.get("gender") or "").upper()
    cutoff = BODY_FAT_CUTOFF.get(gender, BODY_FAT_CUTOFF_UNKNOWN)

    pct, source = body_fat_percentage(inbody)
    if pct is not None:
        is_cut = pct >= cutoff
        return {
            "mode": RoutineMode.CUT if is_cut else RoutineMode.BALANCE,
            "basis": f"BODY_FAT_{source}",
            "value": pct,
            "cutoff": cutoff,
            "reason": (
                f"체지방률 {pct}%로 감량을 함께 하는 편이 효과적이라 전신 운동과 "
                "유산소를 섞어 구성했습니다."
                if is_cut
                else f"체지방률 {pct}%로 감량이 급하지 않아 근력 중심으로 구성했습니다."
            ),
        }

    # ── 폴백: 체지방률을 못 읽음 → BMI ──────────────────────────────────────
    # ⚠️ 근육형을 오분류할 수 있는 경로다. basis 로 표시해 추적 가능하게 둔다.
    bmi = _as_float(inbody.get("bmi"))
    if bmi is None:
        weight, height = _as_float(inbody.get("weight")), _as_float(inbody.get("height"))
        if weight is not None and height is not None:
            bmi = round(weight / (height / 100) ** 2, 1)

    if bmi is None:
        return {
            "mode": RoutineMode.BALANCE,
            "basis": "NO_INBODY",
            "value": None,
            "cutoff": None,
            "reason": "체성분 수치를 읽지 못해 기본 모드로 구성했습니다.",
        }

    is_cut = bmi >= BMI_CUTOFF
    return {
        "mode": RoutineMode.CUT if is_cut else RoutineMode.BALANCE,
        "basis": "BMI_FALLBACK",
        "value": bmi,
        "cutoff": BMI_CUTOFF,
        "reason": (
            f"체지방률을 읽지 못해 BMI {bmi} 기준으로 감량 병행 구성을 적용했습니다."
            if is_cut
            else f"체지방률을 읽지 못해 BMI {bmi} 기준으로 근력 중심 구성을 적용했습니다."
        ),
    }
