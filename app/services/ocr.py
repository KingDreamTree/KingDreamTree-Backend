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
from io import BytesIO
from typing import Any

from PIL import Image

from app.config import settings
from app.prompts.inbody_ocr import SEGMENT_USER_PROMPT, SYSTEM_PROMPT, USER_PROMPT

# ⚠️ openai 는 모듈 최상단에서 import 하지 않는다.
#    routes/inbody → services/ocr 경로라, 여기서 최상단 import 를 하면
#    배포 서버에 openai 가 없을 때 **API 전체가 기동 실패**한다.
#    (인바디는 선택 기능인데 그것 때문에 앱이 안 뜨면 안 된다)
#    실제 호출 시점에만 가져온다 — services/vlm.py 와 같은 규약.

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


async def extract_inbody(
    pages: bytes | list[bytes], mime_type: str = "image/jpeg"
) -> dict[str, Any]:
    """인바디 결과지 이미지에서 수치를 추출한다.

    Args:
        pages: 결과지 이미지. **한 건의 여러 페이지**면 리스트로 — 한 요청에
               전부 넣어 한 번만 호출한다 (api-spec F07: 요청 1건 = 결과지 1건).

    Returns:
        raw_ocr 원본 dict. ⚠️ 사용자 수정 시에도 이 값은 덮어쓰지 않는다.

    두 번 나눠 읽는다 (2026-08-21):
      1차 — 결과지 전체: 전신 수치. 큰 글씨라 전체 이미지에서도 정확하다.
      2차 — 2×2 확대 조각: 부위별 두 표. segments 만 이 결과로 덮어쓴다.
    ⚠️ gpt-4o 는 이미지를 짧은 변 768px 로 줄여 보므로, 결과지 통짜에서는
       부위별 표의 작은 숫자가 뭉개져 그럴듯한 값을 지어낸다 (실측 90.6→112.7).
       조각을 확대해 주면 같은 모델이 같은 결과지 15개 값을 전부 정확히 읽는다.

    ⚠️ SMI는 추출하지 않는다 — VLM에게 계산을 시키지 않는다는 원칙.
       결과지의 SMI 칸도 비어 있는 경우가 있어, 백엔드가 calc_smi()로 직접 계산한다.
    """
    if isinstance(pages, bytes):
        pages = [pages]

    if settings.use_mock:
        return dict(_MOCK_RAW)

    from openai import AsyncOpenAI  # 지연 import — 모듈 상단 주석 참고

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=1)

    raw = await _extract(client, [_image_block(p, mime_type) for p in pages], USER_PROMPT)

    # 2차가 실패하면 1차의 segments 를 그대로 둔다 — 전신 수치까지 잃을 이유가
    # 없고, 섞어 읽은 값은 _check_percentage_columns 가 WARN 으로 표시한다.
    try:
        tile_blocks = [
            _image_block(t, "image/jpeg") for p in pages for t in _segment_tiles(p)
        ]
        focused = await _extract(client, tile_blocks, SEGMENT_USER_PROMPT)
        if isinstance(focused.get("segments"), dict):
            raw["segments"] = focused["segments"]
    except Exception:  # noqa: BLE001 — 2차는 보강 호출이라 어떤 실패든 1차로 후퇴
        pass
    return raw


# ── VLM 호출 조각 ─────────────────────────────────────────────────────────────

#: gpt-4o vision 입력 상한 — 이보다 크면 API가 줄여서 본다. 조각을 미리 이
#  크기까지 확대해 보내야 부위별 표의 작은 숫자가 살아남는다.
_VLM_SHORT, _VLM_LONG = 768, 2048
_TILE_GRID = 2  # 2×2 = 결과지 하나당 4조각 — 실측으로 15/15 정답이라 더 안 쪼갠다
_TILE_OVERLAP = 0.15  # 표가 조각 경계에 걸려 잘리지 않도록 겹친다


def _segment_tiles(image_bytes: bytes) -> list[bytes]:
    """결과지를 2×2 겹침 조각으로 잘라 gpt-4o 상한까지 확대한다."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    ox = int(w / _TILE_GRID * _TILE_OVERLAP)
    oy = int(h / _TILE_GRID * _TILE_OVERLAP)
    tiles = []
    for r in range(_TILE_GRID):
        for c in range(_TILE_GRID):
            box = (
                max(0, w * c // _TILE_GRID - ox),
                max(0, h * r // _TILE_GRID - oy),
                min(w, w * (c + 1) // _TILE_GRID + ox),
                min(h, h * (r + 1) // _TILE_GRID + oy),
            )
            tile = img.crop(box)
            scale = min(_VLM_SHORT / min(tile.size), _VLM_LONG / max(tile.size))
            if scale > 1:
                tile = tile.resize(
                    (round(tile.width * scale), round(tile.height * scale)), Image.LANCZOS
                )
            buf = BytesIO()
            tile.save(buf, "JPEG", quality=90)
            tiles.append(buf.getvalue())
    return tiles


def _image_block(data: bytes, mime_type: str) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{base64.b64encode(data).decode()}",
            "detail": "high",
        },
    }


async def _extract(client: Any, image_blocks: list[dict], user_prompt: str) -> dict[str, Any]:
    response = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        temperature=0,  # 수치 추출은 결정론적으로
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [*image_blocks, {"type": "text", "text": user_prompt}]},
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
    checks.extend(_check_percentage_columns(raw))

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


# ── DB CHECK 가드 ─────────────────────────────────────────────────────────────

#: inbody 테이블의 CHECK 제약과 동일한 범위 (db/schema.sql §7).
#  ⚠️ OCR이 범위 밖 값을 뽑으면 그 컬럼만 None으로 빼고 WARN을 남긴다.
#     이게 없으면 CHECK 위반으로 UPDATE 전체가 터져 "검증 실패가 INSERT를
#     막지 않는다" 원칙이 깨진다. 원본 값은 raw_ocr에 그대로 남아 있다.
_COLUMN_RANGE: dict[str, tuple[float, float]] = {
    "age": (1, 120),
    "height": (120, 220),
    "weight": (25, 250),
    "bmi": (10, 60),
    "body_fat_mass": (0, 150),
    "body_fat_percentage": (1, 70),
    "skeletal_muscle_mass": (10, 60),
    "fat_free_mass": (10, 150),
    "bmr_kcal": (500, 5000),
}


def sanitize_columns(cols: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """DB CHECK를 위반할 값을 None으로 바꾸고, 그 사실을 WARN 체크로 돌려준다."""
    out = dict(cols)
    warns: list[dict[str, Any]] = []

    for col, (low, high) in _COLUMN_RANGE.items():
        value = out.get(col)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or not (low <= value <= high):
            warns.append(
                {
                    "rule": f"DB_RANGE_{col.upper()}",
                    "level": "WARN",
                    "actual": value,
                    "expected_range": [low, high],
                    "message": f"{col} 추출값 {value} 이 저장 가능 범위({low}~{high})를 벗어나 비움 — 확인 화면에서 수정 필요",
                }
            )
            out[col] = None

    if out.get("gender") not in ("MALE", "FEMALE", None):
        warns.append(
            {
                "rule": "DB_RANGE_GENDER",
                "level": "WARN",
                "actual": out["gender"],
                "message": "성별 추출값이 MALE/FEMALE이 아니어서 비움",
            }
        )
        out["gender"] = None

    return out, warns


def sanitize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PATCH로 들어온 segments 의 lean_mass 를 부위별 정상 범위로 검증한다.

    ⚠️ DB CHECK는 `lean_mass >= 0` 뿐이다(schema.sql inbody_segment 주석 —
       "부위별 범위 검증은 CHECK가 아니라 애플리케이션에서"). 그래서 -1 은 CHECK
       위반으로 500이 나고, 500(몸통 근육 500kg)은 그냥 통과했다(실측). 여기서
       막지 않으면 사용자 입력 경로에만 검증이 없는 상태가 계속된다 — OCR
       추출값은 이미 _check_segment_ranges 로 같은 범위를 보고 있다.

    Returns:
        위반 목록(WARN dict). 호출부가 이걸로 400을 낼지 정한다 — sanitize_columns 와
        같은 자리(라우트)에서 같은 방식으로 판단하도록 값만 돌려준다.
    """
    warns: list[dict[str, Any]] = []
    for s in segments:
        segment = s.get("segment")
        lean = s.get("lean_mass")
        low_high = _SEGMENT_RANGE.get(segment)
        if lean is None or low_high is None:
            continue
        low, high = low_high
        if not isinstance(lean, (int, float)) or not (low <= lean <= high):
            warns.append(
                {
                    "rule": f"SEGMENT_RANGE_{segment}",
                    "level": "WARN",
                    "actual": lean,
                    "expected_range": [low, high],
                    "message": f"{segment} 근육량 {lean}kg 이 저장 가능 범위({low}~{high}kg)를 벗어남",
                }
            )
    return warns


# ── 프론트 응답용 변환 ────────────────────────────────────────────────────────

#: 검증 rule → 확인 화면의 필드 키 (api-spec F07 응답 형식)
_RULE_TO_FIELD = {
    "WEIGHT_IDENTITY": "weight",
    "BMI_IDENTITY": "bmi",
    "FAT_FREE_MASS": "fat_free_mass",
    "BODY_FAT_PCT": "body_fat_percentage",
    "SYMMETRY_팔": "segments.LEFT_ARM",
    "SYMMETRY_다리": "segments.LEFT_LEG",
}


def to_field_validation(validation: dict[str, Any] | None) -> dict[str, Any]:
    """validation JSONB(checks 배열) → 확인 화면용 {필드: {level, message}}.

    ⚠️ WARN만 내려보낸다. PASS/SKIP까지 다 주면 프론트가 전부 똑같이 강조하고,
       사용자는 대충 넘긴다 (api-spec F07 주의사항).
    """
    if not validation:
        return {}

    out: dict[str, Any] = {}
    for check in validation.get("checks", []):
        if check.get("level") != "WARN":
            continue
        rule = check.get("rule", "")
        if rule.startswith("RANGE_"):
            field = f"segments.{rule.removeprefix('RANGE_')}"
        elif rule.startswith("DB_RANGE_"):
            field = rule.removeprefix("DB_RANGE_").lower()
        else:
            field = _RULE_TO_FIELD.get(rule, rule.lower())
        out[field] = {"level": "warn", "message": check.get("message", "")}
    return out


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


def _check_percentage_columns(raw: dict) -> list[dict[str, Any]]:
    """근육 표와 체지방 표를 **섞어 읽었는지** 본다.

    ⚠️ 실제로 난 사고다 (2026-08-20). 결과지의 [부위별근육분석]과
       [부위별체지방분석]은 5행 막대표라 생김새가 같은데, OCR 프롬프트에서
       규모 힌트(`예: 90.6` / `예: 108.0`)를 빼자 모델이 두 표를 구분하지
       못하고 **체지방 %를 근육량 % 자리에 넣었다.** 그 값이 그대로 부위
       진단문에 "근육량이 평균의 112.7%"로 나갔다 — 사용자 실제 값은 87%.

    판별 근거는 «같은 부위에서 두 %가 정확히 같다»는 것 하나다. 근육량 표준
    대비와 체지방 표준 대비가 소수점까지 일치할 확률은 사실상 없다.

    ⚠️ 값을 고치지 않는다. 어느 쪽이 맞는지 코드는 모른다 — WARN 만 남겨
       "이 인바디는 믿지 말라"고 표시하고, 사용자가 PATCH 로 고칠 수 있게 둔다
       (검증 실패가 INSERT 를 막지 않는다는 모듈 원칙).
    """
    segments = raw.get("segments") or {}
    results = []
    for segment in _SEGMENT_RANGE:
        seg = segments.get(segment) or {}
        lean, fat = seg.get("lean_percentage"), seg.get("fat_percentage")
        if lean is None or fat is None or lean != fat:
            continue
        results.append(
            {
                "rule": f"percentage_columns_{segment.lower()}",
                "level": "WARN",
                "message": (
                    f"{segment} 의 근육량·체지방 표준 대비가 둘 다 {lean}% 로 같습니다 — "
                    "결과지의 두 표를 섞어 읽었을 가능성이 큽니다. 값을 확인해주세요."
                ),
                "lean_percentage": lean,
                "fat_percentage": fat,
            }
        )
    return results


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
