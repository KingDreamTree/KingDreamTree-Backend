"""운동 카탈로그 — 슬롯별 후보 필터 (L1 슬롯 → ExerciseDB 운동).

이 모듈이 **LLM 환각을 구조적으로 차단하는 지점**이다.
LLM 은 운동명을 쓰지 않고, 여기서 만든 후보 목록의 `exercise_ref` 중에서만 고른다.
→ 없는 운동을 낼 수 없고, 초보 부적합 운동도 후보에 없으면 나올 수 없다.

━━ 실측 확인 (2026-08-14, scripts/fetch_exercisedb.py) ━━

    수집 200개 · STRENGTH 173 / CARDIO 15 / PLYOMETRICS 9 / 기타 3
    ⚠️ 무료 티어 상한이 200개다 (meta.total 고정). "1,500+" 는 다른 플랜 기준.

⚠️ **bodyParts enum 이 추정과 여러 곳 달랐다.** 표준 ExerciseDB 문서를 보고
   추정했던 이름이 이 변종에서는 다르다 — 하드코딩 전에 반드시 실측할 것:

    LOWER ARMS  ->  FOREARMS
    UPPER LEGS  ->  THIGHS   (QUADRICEPS / HAMSTRINGS 도 별도 bodyPart 로 존재)
    LOWER LEGS  ->  CALVES
    CARDIO      ->  bodyPart 에 없음. `exerciseType == "CARDIO"` 로만 판별한다

⚠️ **근육명도 세분화돼 있다.** PECTORALIS MAJOR 는 CLAVICULAR/STERNAL HEAD 로,
   TRAPEZIUS 는 UPPER/MIDDLE/LOWER FIBERS 로 나뉘고, RHOMBOIDS 는 아예 없다.
   그래서 매칭은 `targetMuscles` 가 아니라 **`bodyParts` 를 1차 기준**으로 삼는다
   — 근육명 매칭은 표기 변형에 취약하다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: 배치 수집 산출물. scripts/fetch_exercisedb.py fetch 로 생성한다.
#: ⚠️ 운영에서는 exercise_catalog 테이블로 옮긴다 (docs/routine-schema-draft.md).
CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "exercise_catalog.json"

#: 템플릿 슬롯(한글) → ExerciseDB bodyParts. **실측 enum 으로 확정된 값이다.**
SLOT_BODY_PARTS: dict[str, set[str]] = {
    "가슴": {"CHEST"},
    "등": {"BACK"},
    "어깨": {"SHOULDERS"},
    "후면 어깨": {"SHOULDERS", "BACK"},
    "이두": {"BICEPS", "UPPER ARMS"},
    "삼두": {"TRICEPS", "UPPER ARMS"},
    "팔": {"UPPER ARMS", "BICEPS", "TRICEPS", "FOREARMS"},
    "대퇴사두": {"QUADRICEPS", "THIGHS"},
    "햄스트링·둔근": {"HAMSTRINGS", "HIPS", "THIGHS"},
    "종아리": {"CALVES"},
    "코어": {"WAIST"},
}

#: 진단 부위(Sapiens class_name) → 슬롯. L2 가중이 흐르는 경로.
PART_TO_SLOTS: dict[str, tuple[str, ...]] = {
    "Torso": ("가슴", "등", "어깨", "코어"),
    "Left_Upper_Arm": ("이두", "삼두", "팔"),
    "Right_Upper_Arm": ("이두", "삼두", "팔"),
    "Left_Lower_Arm": ("팔",),
    "Right_Lower_Arm": ("팔",),
    "Left_Upper_Leg": ("대퇴사두", "햄스트링·둔근"),
    "Right_Upper_Leg": ("대퇴사두", "햄스트링·둔근"),
    "Left_Lower_Leg": ("종아리",),
    "Right_Lower_Leg": ("종아리",),
}

#: 슬롯의 주동근 (ExerciseDB `/muscles` 실측 enum).
#:
#: ⚠️ **bodyParts 만으로는 이두/삼두를 못 가른다** — 둘 다 UPPER ARMS 라서,
#:    실호출에서 이두 슬롯에 삼두성 프레스("원암 해머 프레스")가 뽑혔다.
#:    그래서 정렬 1순위를 targetMuscles 일치로 둔다. bodyParts 는 후보를
#:    **거르는** 기준(넓게), targetMuscles 는 **줄 세우는** 기준(정밀하게)이다.
#:    필터로 쓰지 않는 이유: 표기 변형·누락 시 후보가 0개가 되는 쪽이 더 위험하다.
SLOT_TARGET_MUSCLES: dict[str, set[str]] = {
    "가슴": {"PECTORALIS MAJOR CLAVICULAR HEAD", "PECTORALIS MAJOR STERNAL HEAD"},
    "등": {
        "LATISSIMUS DORSI",
        "TRAPEZIUS UPPER FIBERS",
        "TRAPEZIUS MIDDLE FIBERS",
        "TRAPEZIUS LOWER FIBERS",
        "TERES MAJOR",
    },
    "어깨": {"ANTERIOR DELTOID", "LATERAL DELTOID"},
    "후면 어깨": {"POSTERIOR DELTOID", "INFRASPINATUS", "TERES MINOR"},
    "이두": {"BICEPS BRACHII", "BRACHIALIS", "BRACHIORADIALIS"},
    "삼두": {"TRICEPS BRACHII"},
    "팔": {"BICEPS BRACHII", "TRICEPS BRACHII", "WRIST FLEXORS", "WRIST EXTENSORS"},
    "대퇴사두": {"QUADRICEPS"},
    "햄스트링·둔근": {"HAMSTRINGS", "GLUTEUS MAXIMUS", "GLUTEUS MEDIUS"},
    "종아리": {"GASTROCNEMIUS", "SOLEUS"},
    "코어": {"RECTUS ABDOMINIS", "OBLIQUES", "TRANSVERSUS ABDOMINIS"},
}

#: 좌우 비대칭이 클 때 우선할 단측 운동 힌트.
#: ⚠️ API 에 좌우 구분 필드가 없어 이름으로 찾는다. RCT 근거가 아니라 관행이다.
SINGLE_SIDE_HINTS = ("single", "one arm", "one-arm", "one leg", "lunge", "split", "alternate")

#: 이름에 이게 들어가면 근력 후보에서 뺀다.
#:
#: ⚠️ **데이터 라벨 오류 보정이다.** 실측 200개 중 35개(17.5%)가 `exerciseType`
#:    은 STRENGTH 인데 실제로는 스트레칭·모빌리티였다 ("Calves Stretch",
#:    "Neck Side Stretch" 등). 그대로 두면 근력 슬롯에 스트레칭이 배정된다.
#:    D8(초보 부적합 제외)과는 성격이 다르다 — 저건 난이도 판단이고 이건
#:    명백한 오분류 수정이라 PM 승인 대상이 아니다.
NON_STRENGTH_NAME_HINTS = ("stretch", "mobility", "foam roll", "warm up", "warm-up")

# --------------------------------------------------------------------------- #
# 초보자 스크리닝 (D8) — 여기가 유일한 판정 지점
# --------------------------------------------------------------------------- #
#
# ⚠️ **seed 할 때마다 다시 평가한다.** 수집본 JSON 의 값을 믿지 않는다 —
#    목록을 고칠 때마다 API 를 재수집해야 한다면(=한글화가 날아간다) 아무도
#    안 고치게 된다. 원본은 raw 로 두고 판정은 여기서 한다.
#
# ⚠️ 이 목록은 **우리 판단이지 논문이 아니다** (D8, PM 승인 대기).
#    근거를 주장하지 않고, 무엇을 왜 뺐는지만 남긴다.

#: 초보자가 1회도 수행하기 어렵거나 부상 위험이 높은 동작.
#:
#: 실측 근거 — 초기 목록(hanging/pistol/handstand 등)은 200건 중 **3건**만
#: 걸러냈다. 정작 **풀업·친업 계열 7건이 통과**했는데, 초보자 대부분이
#: 1개도 못 하는 동작이라 루틴에 넣으면 그 슬롯이 통째로 무의미해진다.
BEGINNER_UNSAFE_TERMS: tuple[str, ...] = (
    # 자기 체중을 매달아 끌어올리는 동작 — 초보 수행률이 낮다
    "pull-up",
    "pull up",
    "pullup",
    "chin-up",
    "chin up",
    "chinup",
    "muscle up",
    "muscle-up",
    "chest dip",  # ⚠️ bench dip / floor dip 은 초보도 가능해서 제외하지 않는다
    # 폭발적·플라이오메트릭 — 착지 충격이 커서 초보 관절에 부담
    "jump squat",
    "jumping",
    "plyo",
    "box jump",
    "burpee",
    # 고난도 체조·역도 계열
    "pistol",
    "handstand",
    "planche",
    "front lever",
    "dragon flag",
    "hanging",
    "snatch",
    "clean and jerk",
    "clean & jerk",
    "power clean",
    "l-sit",
    "l-pull",
)


def _unsafe_pattern() -> re.Pattern[str]:
    """제외 용어를 단어 경계 + 복수형 허용으로 매칭하는 정규식.

    실측에서 걸린 함정 둘 —
      ⚠️ 단순 `in` 검사는 **`"chin" in "Touching"` 처럼 조용히 걸린다.**
         "Front Toe Touching" 이 친업으로 오분류됐다. → 단어 경계 필수.
      ⚠️ 단수형만 적으면 복수형이 샌다. "Chin-Up" 은 걸리는데 "Chin-ups" 는
         통과했다. → 마지막 단어에 `s?` 를 붙인다.
    하이픈은 공백으로 정규화해 "pull-up"/"pull up" 을 한 번에 잡는다.
    """
    parts = []
    for term in BEGINNER_UNSAFE_TERMS:
        words = term.replace("-", " ").split()
        parts.append(r"\s+".join(re.escape(w) for w in words) + "s?")
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


_UNSAFE_RE = _unsafe_pattern()


def is_beginner_safe(name: str, exercise_type: str = "STRENGTH") -> bool:
    """초보자에게 처방해도 되는 동작인가. **seed 시점에 평가된다.**

    ⚠️ **CARDIO 는 항상 통과시킨다.** 유산소는 속도·시간으로 강도를 조절하므로
       난이도 기준이 근력과 다르다. 실측에서 "Jumping Jack"·"Burpee" 가
       `jumping`/`burpee` 에 걸려 빠졌는데, 둘 다 초보 유산소의 기본 동작이라
       유산소 후보를 오히려 망가뜨렸다.

    ⚠️ 이름만 본다. keywords 는 마케팅 문구가 섞여 있어("Bodyweight exercise
       for waist") 난이도 신호로 못 쓴다 — 오탐만 늘렸다.
    """
    if (exercise_type or "").upper() == "CARDIO":
        return True
    normalized = " ".join(name.lower().replace("-", " ").split())
    return not _UNSAFE_RE.search(normalized)


class CatalogNotBuiltError(RuntimeError):
    """카탈로그 파일이 없다. scripts/fetch_exercisedb.py fetch 를 먼저 돌려야 한다."""


def load_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    src = path or CATALOG_PATH
    if not src.exists():
        raise CatalogNotBuiltError(
            f"{src} 가 없습니다. "
            "RAPIDAPI_KEY=... python scripts/fetch_exercisedb.py fetch 를 먼저 실행하세요."
        )
    return json.loads(src.read_text(encoding="utf-8"))["exercises"]


def _is_compound(item: dict[str, Any]) -> bool:
    """다관절(복합) 운동인가 — ACSM 2009: 초보자에게 다관절 운동 강조."""
    return len(item.get("body_parts") or []) > 1 or len(item.get("secondary_muscles") or []) >= 3


def _uses_equipment(item: dict[str, Any]) -> bool:
    """맨몸이 아닌 장비 운동인가."""
    return bool(set(item.get("equipments") or []) - {"BODY WEIGHT"})


def _is_actually_strength(item: dict[str, Any]) -> bool:
    """STRENGTH 라벨이 붙었지만 실제로는 스트레칭인 항목을 걸러낸다."""
    name = (item.get("name_en") or "").lower()
    return not any(h in name for h in NON_STRENGTH_NAME_HINTS)


def _dedupe_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 이름의 운동이 다른 exerciseId 로 중복 수록돼 있다 (실측 9건).

    후보 목록에 중복이 남으면 LLM 이 같은 운동을 두 번 고를 수 있고,
    사용자 화면에도 같은 이름이 두 줄 뜬다.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("name_en") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def candidates_for_slot(
    slot: str,
    catalog: list[dict[str, Any]],
    *,
    beginner_only: bool = True,
    prefer_single_side: bool = False,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """슬롯 하나의 후보 운동 목록. LLM 은 이 안에서만 고른다.

    정렬 우선순위 (앞일수록 먼저):
        ① 주동근 일치    targetMuscles ∩ SLOT_TARGET_MUSCLES — 이두/삼두처럼
                        bodyParts 가 같은 슬롯을 정밀하게 가른다
        ② 장비 운동      헬스장 전제이므로 장비 운동을 앞세운다
        ③ 복합 운동      ACSM: 초보자에게 다관절 강조
        ④ 단측 운동      좌우 비대칭이 클 때만 (prefer_single_side)

    ⚠️ 장비 운동은 **우선순위이지 필터가 아니다.** 수집된 200개 중 79%가
       BODY WEIGHT 라, 장비로 걸러내면 이두·삼두 슬롯의 후보가 0개가 된다.
       헬스장에서 푸시업·플랭크를 하는 것은 전혀 이상하지 않으므로 남겨둔다.
    """
    body_parts = SLOT_BODY_PARTS.get(slot)
    if not body_parts:
        return []

    pool = [
        e
        for e in catalog
        if e.get("exercise_type") == "STRENGTH"
        and _is_actually_strength(e)
        and body_parts & set(e.get("body_parts") or [])
        and (not beginner_only or e.get("is_beginner_safe", True))
    ]

    primary = SLOT_TARGET_MUSCLES.get(slot, set())

    def rank(e: dict[str, Any]) -> tuple:
        single = any(h in (e.get("name_en") or "").lower() for h in SINGLE_SIDE_HINTS)
        muscle_hit = bool(primary & set(e.get("target_muscles") or []))
        return (
            not muscle_hit,
            not _uses_equipment(e),
            not _is_compound(e),
            not (prefer_single_side and single),
            e.get("name_en") or "",
        )

    return _dedupe_by_name(sorted(pool, key=rank))[:limit]


def cardio_candidates(
    catalog: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """CUT 모드 유산소 후보.

    ⚠️ bodyParts 에 CARDIO 가 없으므로 `exercise_type` 으로만 판별한다.
       실측 15개 중 트레드밀·일립티컬·어썰트바이크·스테퍼 등 헬스장 장비 운동이
       충분히 있어 D3(유산소 슬롯 배치)가 실현 가능하다.
    """
    pool = [e for e in catalog if e.get("exercise_type") == "CARDIO"]
    # 장비(머신) 유산소를 앞에 — 헬스장 전제이고 강도 조절이 쉽다
    ranked = sorted(pool, key=lambda e: (not _uses_equipment(e), e.get("name_en") or ""))
    return _dedupe_by_name(ranked)[:limit]


def coverage_report(catalog: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """슬롯별 후보 수 — 후보가 모자란 슬롯을 조기에 발견하기 위한 진단용."""
    out: dict[str, dict[str, int]] = {}
    for slot in SLOT_BODY_PARTS:
        cands = candidates_for_slot(slot, catalog, limit=999)
        out[slot] = {
            "total": len(cands),
            "with_equipment": sum(1 for c in cands if _uses_equipment(c)),
            "compound": sum(1 for c in cands if _is_compound(c)),
        }
    return out
