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


#: 착지 충격이 큰(체중이 관절에 반복 낙하하는) 동작.
#:
#: ⚠️ BEGINNER_UNSAFE_TERMS 와 **겹치지만 목적이 다르다.** 저쪽은 "초보가
#:    못 한다"이고 이쪽은 "체중이 무거우면 관절이 못 버틴다"다. CARDIO 는
#:    is_beginner_safe 가 항상 통과시키므로(점핑잭·버피는 초보 유산소의 기본)
#:    유산소 쪽 충격 판정은 이 목록이 단독으로 책임진다.
#:
#: 근거 — 무릎 관절 반력은 걷기 체중의 ~3배, 달리기 ~8배까지 오른다.
#:    체지방(체중)이 높을수록 같은 동작의 절대 부하가 커지고, 비만은 무릎
#:    골관절염의 독립 위험인자다. ACSM 도 비만 대상자에게 저충격·비체중부하
#:    유산소(자전거·일립티컬·수중)를 권고한다.
HIGH_IMPACT_TERMS: tuple[str, ...] = (
    "run",  # "Run on Treadmill" — 워킹은 walk 라 안 걸린다
    "running",
    "sprint",
    "jog",
    "jump rope",
    "jumping",
    "jump",
    "burpee",
    "high knee",
    "mountain climber",
    "skater",
    "plyo",
    "hop",
)


#: 이름에 무슨 단어가 들어있든 **장비가 충격을 없애는** 종목.
#:
#: ⚠️ 실측에서 잡은 오탐 — "Assault Bike **Run**"·"Stationary Bike **Run**" 이
#:    `run` 에 걸려 빠졌다. 둘 다 발이 페달에 붙어 있어 착지 충격이 0 인데,
#:    하필 저충격 유산소의 대표 종목이라 빠지면 손해가 크다.
#:    충격은 **동작 이름이 아니라 지지 방식**이 정하므로 장비를 먼저 본다.
LOW_IMPACT_MODALITIES: tuple[str, ...] = (
    "bike",
    "cycle",
    "cycling",
    "elliptical",
    "stepper",
    "rowing",
    "row machine",
    "ski erg",
)


def _high_impact_pattern() -> re.Pattern[str]:
    parts = []
    for term in HIGH_IMPACT_TERMS:
        words = term.replace("-", " ").split()
        parts.append(r"\s+".join(re.escape(w) for w in words) + "s?")
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


_HIGH_IMPACT_RE = _high_impact_pattern()


def is_single_side(name: str) -> bool:
    """한쪽씩 수행하는 운동인가.

    좌우 불균형 교정의 **전제 조건**이다. 양측 운동에서는 강한 쪽이 약한 쪽을
    끌고 가버려서(bilateral compensation) 세트를 아무리 늘려도 격차가 안 좁혀진다.
    단측 운동이어야 약한 쪽이 자기 몫을 온전히 하게 된다.
    """
    return any(h in (name or "").lower() for h in SINGLE_SIDE_HINTS)


def is_low_impact(name: str) -> bool:
    """착지 충격이 낮은 동작인가. 체지방이 높은 사용자의 유산소 선정에 쓴다.

    장비 판정이 먼저다 — 자전거·일립티컬은 이름에 "Run" 이 붙어도 저충격이다.
    """
    normalized = " ".join((name or "").lower().replace("-", " ").split())
    if any(m in normalized for m in LOW_IMPACT_MODALITIES):
        return True
    return not _HIGH_IMPACT_RE.search(normalized)


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
    prefer_low_impact: bool = False,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """슬롯 하나의 후보 운동 목록. LLM 은 이 안에서만 고른다.

    정렬 우선순위 (앞일수록 먼저):
        ① 저충격        체지방이 높을 때만 (prefer_low_impact) — 관절 보호가
                        종목 적합성보다 앞선다
        ② 주동근 일치    targetMuscles ∩ SLOT_TARGET_MUSCLES — 이두/삼두처럼
                        bodyParts 가 같은 슬롯을 정밀하게 가른다
        ③ 장비 운동      헬스장 전제이므로 장비 운동을 앞세운다
        ④ 복합 운동      ACSM: 초보자에게 다관절 강조
        ⑤ 단측 운동      좌우 비대칭이 클 때만 (prefer_single_side)

    ⚠️ 저충격은 근력에서 **필터가 아니라 우선순위**다. 유산소와 달리 근력
       후보는 슬롯별로 수가 적어서, 걸러내면 슬롯이 비는 쪽이 더 위험하다.

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
        # ⚠️ 저장된 is_beginner_safe 를 **읽지 않고 여기서 다시 판정한다.** 수집본
        #    JSON 의 플래그는 fetch_exercisedb.ADVANCED_KEYWORDS 로 찍혔는데 그
        #    목록엔 dip·pull up 이 없다. seed 는 BEGINNER_UNSAFE_TERMS 로 재평가하지만
        #    그건 DB 테이블만 고치고, 런타임은 load_catalog() 로 JSON 을 읽는다.
        #    → 저장값을 믿으면 초보 루틴에 풀업·체스트딥·점프스쿼트 12종이 들어온다.
        and (
            not beginner_only
            or is_beginner_safe(e.get("name_en") or "", e.get("exercise_type") or "STRENGTH")
        )
    ]

    primary = SLOT_TARGET_MUSCLES.get(slot, set())

    def rank(e: dict[str, Any]) -> tuple:
        name = e.get("name_en") or ""
        single = is_single_side(name)
        muscle_hit = bool(primary & set(e.get("target_muscles") or []))
        return (
            not (prefer_low_impact and is_low_impact(name)),
            not muscle_hit,
            # ⚠️ 단측 선호는 **주동근 바로 다음**이어야 한다. 원래 5순위였는데,
            #    앞의 장비·복합 두 키가 순서를 이미 확정해 버려서 실측 발동률이
            #    0 이었다 (34c7349 의 좌우 순서 교정이 배선만 되고 안 돌았다).
            #    prefer_single_side=False 면 항상 True 라 기존 정렬은 안 변한다.
            not (prefer_single_side and single),
            not _uses_equipment(e),
            not _is_compound(e),
            e.get("name_en") or "",
        )

    return _dedupe_by_name(sorted(pool, key=rank))[:limit]


def cardio_candidates(
    catalog: list[dict[str, Any]],
    *,
    low_impact_only: bool = False,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """CUT 모드 유산소 후보.

    ⚠️ bodyParts 에 CARDIO 가 없으므로 `exercise_type` 으로만 판별한다.
       실측 15개 중 트레드밀·일립티컬·어썰트바이크·스테퍼 등 헬스장 장비 운동이
       충분히 있어 D3(유산소 슬롯 배치)가 실현 가능하다.

    Args:
        low_impact_only: 체지방률이 높아 CUT 으로 판정된 사용자에게 True.
            달리기·줄넘기·버피를 후보에서 **뺀다** (§HIGH_IMPACT_TERMS).
            ⚠️ 걸러낸 결과가 비면 필터를 포기하고 원래 풀로 돌아간다 —
               유산소 슬롯이 통째로 비는 것보다 고충격이라도 있는 게 낫다.
    """
    pool = [e for e in catalog if e.get("exercise_type") == "CARDIO"]
    if low_impact_only:
        safe = [e for e in pool if is_low_impact(e.get("name_en") or "")]
        if safe:
            pool = safe
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
