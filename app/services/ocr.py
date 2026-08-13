"""인바디 결과지 추출 + 검증 (F07).

기술 선택: **VLM(GPT-4o vision) 단독. 별도 OCR 엔진 없음.**

  추출은 VLM이, 검증은 결정론적 규칙이 한다.
  VLM은 "초안 작성자"이고 최종 확정은 규칙 + 사용자다.
  → 할루시네이션은 항등식·범위·대칭성 검사로 걸러 validation JSONB에 남긴다.
  → 근거 전문은 docs/llm-strategy.md 참고.

⚠️ 검증 실패가 INSERT를 막지 않는다. "OCR이 이상한 값을 뽑았다"는 사실 자체를
   기록해야 나중에 정확도를 평가할 수 있다 (work-b.md §6).
"""

import base64
import json
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.prompts.inbody_ocr import SYSTEM_PROMPT, USER_PROMPT

# ── 검증 임계값 ───────────────────────────────────────────────────────────────
# config.py는 공유 파일(A 리뷰 필요)이라 B 전용 임계값은 여기 둔다.
# 튜닝 후 값이 굳으면 config로 승격한다.

_TOLERANCE = 0.03  # 항등식 허용 오차 3%
_SYMMETRY_TOLERANCE = 0.30  # 좌우 근육량 차이 경고 기준 30%

# 부위별 제지방량(lean_mass) 정상 범위 (kg)
_SEGMENT_RANGE = {
    "LEFT_ARM": (0.5, 8.0),
    "RIGHT_ARM": (0.5, 8.0),
    "TRUNK": (10.0, 40.0),
    "LEFT_LEG": (2.0, 20.0),
    "RIGHT_LEG": (2.0, 20.0),
}

# ── Mock — 실제 InBody570 샘플(inbody_ex.jpg) 값 ──────────────────────────────

_MOCK_RAW: dict[str, Any] = {
    "device_type": "InBody570",
    "measured_at": "2025-01-21",
    "age": 22,
    "gender": "MALE",
    "height": 170,
    "weight": 63.5,
    "bmi": 22.0,
    "body_fat_mass": 12.0,
    "body_fat_percentage": 18.9,
    "skeletal_muscle_mass": 28.8,
    "fat_free_mass": 51.5,
    "bmr_kcal": 1482,
    "total_body_water": 37.7,
    "protein": 10.3,
    "minerals": 3.52,
    "visceral_fat_level": 5,
    "abdominal_fat_ratio": 0.86,
    "ecw_ratio": 0.375,
    "inbody_score": 75,
    "weight_control": {"fat_control_kg": -2.5, "muscle_control_kg": 2.6},
    "segments": {
        "RIGHT_ARM": {
            "lean_mass": 2.74,
            "lean_percentage": 90.6,
            "fat_mass": 0.6,
            "fat_percentage": 108.0,
        },
        "LEFT_ARM": {
            "lean_mass": 2.72,
            "lean_percentage": 89.9,
            "fat_mass": 0.6,
            "fat_percentage": 112.7,
        },
        "TRUNK": {
            "lean_mass": 22.8,
            "lean_percentage": 94.5,
            "fat_mass": 6.0,
            "fat_percentage": 149.4,
        },
        "RIGHT_LEG": {
            "lean_mass": 7.78,
            "lean_percentage": 92.6,
            "fat_mass": 1.9,
            "fat_percentage": 113.1,
        },
        "LEFT_LEG": {
            "lean_mass": 7.78,
            "lean_percentage": 92.7,
            "fat_mass": 1.8,
            "fat_percentage": 111.6,
        },
    },
}


# ── Public API ────────────────────────────────────────────────────────────────


async def extract_inbody(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, Any]:
    """인바디 결과지 이미지에서 수치를 추출한다.

    Returns:
        raw_ocr 원본 dict. ⚠️ 사용자 수정 시에도 이 값은 덮어쓰지 않는다.
    """
    if settings.use_mock:
        return dict(_MOCK_RAW)

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    b64 = base64.b64encode(image_bytes).decode()

    response = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        temperature=0,  # 수치 추출은 결정론적으로
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}", "detail": "high"},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            },
        ],
    )
    return json.loads(response.choices[0].message.content)


def validate_inbody(raw: dict[str, Any]) -> dict[str, Any]:
    """추출값을 결정론적 규칙으로 검증한다. **예외를 던지지 않는다.**

    Returns:
        inbody.validation 컬럼에 그대로 저장할 dict.
        {"ok": bool, "checks": [{"rule", "level", "message", ...}]}
    """
    checks: list[dict[str, Any]] = []

    checks.append(_check_weight_identity(raw))
    checks.append(_check_bmi(raw))
    checks.append(_check_fat_free_mass(raw))
    checks.append(_check_body_fat_percentage(raw))
    checks.extend(_check_segment_ranges(raw))
    checks.extend(_check_symmetry(raw))

    return {
        "ok": all(c["level"] != "WARN" for c in checks),
        "checks": checks,
    }


def to_columns(raw: dict[str, Any]) -> dict[str, Any]:
    """raw_ocr → inbody 테이블 컬럼 dict.

    ⚠️ 항등식 전용 항목(total_body_water·protein·minerals·visceral_fat_level 등)은
       컬럼으로 만들지 않는다. raw_ocr에서 읽어 검증만 하고 버린다 (work-b.md §6).
    """
    cols = (
        "device_type",
        "measured_at",
        "age",
        "gender",
        "height",
        "weight",
        "bmi",
        "body_fat_mass",
        "body_fat_percentage",
        "skeletal_muscle_mass",
        "fat_free_mass",
        "bmr_kcal",
    )
    return {c: raw.get(c) for c in cols}


def to_segment_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """raw_ocr → inbody_segment 행 목록.

    ⚠️ lean_percentage / fat_percentage는 컬럼이 없어 여기 담기지 않는다.
       진단 프롬프트에서는 raw_ocr에서 직접 읽어 쓴다 (부위별 취약 판단 기준).
    """
    segments = raw.get("segments") or {}
    rows = []
    for segment, values in segments.items():
        if segment not in _SEGMENT_RANGE or not values:
            continue
        rows.append(
            {
                "segment": segment,
                "lean_mass": values.get("lean_mass"),
                "fat_mass": values.get("fat_mass"),
            }
        )
    return rows


def calc_smi(raw: dict[str, Any]) -> float | None:
    """SMI(골격근지수) = 골격근량 ÷ 신장(m)².

    ⚠️ 골격근량 절대값을 단독으로 쓰면 안 된다 — 체격이 큰 사람은 근육량 자체가 많다.
       체격 보정을 거친 SMI라야 비교에 의미가 있다.
       결과지의 SMI 칸은 비어 있는 경우가 있어 직접 계산한다.
       한국 남성 기준 7.0 kg/m² 미만이면 근감소 위험.
    """
    smm, height = raw.get("skeletal_muscle_mass"), raw.get("height")
    if not smm or not height:
        return None
    return round(smm / ((height / 100) ** 2), 2)


# ── 검증 규칙 ─────────────────────────────────────────────────────────────────


def _skip(rule: str, reason: str) -> dict[str, Any]:
    return {"rule": rule, "level": "SKIP", "message": reason}


def _compare(rule: str, expected: float, actual: float, label: str) -> dict[str, Any]:
    diff_ratio = abs(expected - actual) / expected if expected else 1.0
    passed = diff_ratio <= _TOLERANCE
    return {
        "rule": rule,
        "level": "PASS" if passed else "WARN",
        "expected": round(expected, 2),
        "actual": round(actual, 2),
        "diff_pct": round(diff_ratio * 100, 1),
        "message": (
            f"{label} 일치 (오차 {diff_ratio * 100:.1f}%)"
            if passed
            else f"{label} 불일치 — 계산값 {expected:.2f} vs 추출값 {actual:.2f} (오차 {diff_ratio * 100:.1f}%)"
        ),
    }


def _check_weight_identity(raw: dict) -> dict[str, Any]:
    """체중 ≈ 체수분 + 단백질 + 무기질 + 체지방량."""
    parts = [raw.get(k) for k in ("total_body_water", "protein", "minerals", "body_fat_mass")]
    weight = raw.get("weight")
    if weight is None or any(p is None for p in parts):
        return _skip("WEIGHT_IDENTITY", "체성분 항목이 부족해 항등식 검증을 건너뜀")
    return _compare("WEIGHT_IDENTITY", sum(parts), weight, "체중 항등식")


def _check_bmi(raw: dict) -> dict[str, Any]:
    """BMI ≈ 체중 ÷ 신장(m)²."""
    weight, height, bmi = raw.get("weight"), raw.get("height"), raw.get("bmi")
    if not weight or not height or bmi is None:
        return _skip("BMI_IDENTITY", "체중·신장·BMI 중 누락이 있어 건너뜀")
    return _compare("BMI_IDENTITY", weight / ((height / 100) ** 2), bmi, "BMI")


def _check_fat_free_mass(raw: dict) -> dict[str, Any]:
    """제지방량 ≈ 체중 - 체지방량."""
    weight, fat, ffm = raw.get("weight"), raw.get("body_fat_mass"), raw.get("fat_free_mass")
    if weight is None or fat is None or ffm is None:
        return _skip("FAT_FREE_MASS", "체중·체지방량·제지방량 중 누락이 있어 건너뜀")
    return _compare("FAT_FREE_MASS", weight - fat, ffm, "제지방량")


def _check_body_fat_percentage(raw: dict) -> dict[str, Any]:
    """체지방률 ≈ 체지방량 ÷ 체중 × 100."""
    weight, fat, pct = raw.get("weight"), raw.get("body_fat_mass"), raw.get("body_fat_percentage")
    if not weight or fat is None or pct is None:
        return _skip("BODY_FAT_PCT", "체중·체지방량·체지방률 중 누락이 있어 건너뜀")
    return _compare("BODY_FAT_PCT", fat / weight * 100, pct, "체지방률")


def _check_segment_ranges(raw: dict) -> list[dict[str, Any]]:
    """부위별 제지방량이 생리학적 정상 범위 안에 있는지."""
    segments = raw.get("segments") or {}
    results = []
    for segment, (low, high) in _SEGMENT_RANGE.items():
        value = (segments.get(segment) or {}).get("lean_mass")
        if value is None:
            results.append(_skip(f"RANGE_{segment}", f"{segment} 근육량 없음"))
            continue
        ok = low <= value <= high
        results.append(
            {
                "rule": f"RANGE_{segment}",
                "level": "PASS" if ok else "WARN",
                "actual": value,
                "expected_range": [low, high],
                "message": (
                    f"{segment} 근육량 {value}kg 정상 범위"
                    if ok
                    else f"{segment} 근육량 {value}kg 이 정상 범위({low}~{high}kg)를 벗어남"
                ),
            }
        )
    return results


def _check_symmetry(raw: dict) -> list[dict[str, Any]]:
    """좌우 근육량 차이. ⚠️ 경고만 하고 자동 수정하지 않는다 — 실제 비대칭일 수 있다."""
    segments = raw.get("segments") or {}
    results = []
    for left, right, label in (
        ("LEFT_ARM", "RIGHT_ARM", "팔"),
        ("LEFT_LEG", "RIGHT_LEG", "다리"),
    ):
        lv = (segments.get(left) or {}).get("lean_mass")
        rv = (segments.get(right) or {}).get("lean_mass")
        rule = f"SYMMETRY_{label}"
        if lv is None or rv is None:
            results.append(_skip(rule, f"좌우 {label} 근육량 중 누락이 있어 건너뜀"))
            continue
        base = max(lv, rv)
        diff_ratio = abs(lv - rv) / base if base else 0.0
        ok = diff_ratio <= _SYMMETRY_TOLERANCE
        results.append(
            {
                "rule": rule,
                "level": "PASS" if ok else "WARN",
                "left": lv,
                "right": rv,
                "diff_pct": round(diff_ratio * 100, 1),
                "message": (
                    f"좌우 {label} 균형 (차이 {diff_ratio * 100:.1f}%)"
                    if ok
                    else f"좌우 {label} 근육량 차이 {diff_ratio * 100:.1f}% — 확인 필요 (자동 수정하지 않음)"
                ),
            }
        )
    return results
