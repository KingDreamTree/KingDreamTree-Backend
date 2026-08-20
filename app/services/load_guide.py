"""시작 중량 가이드 — "이 운동, 몇 kg으로 시작하면 되나요"에 답한다.

━━ D9(중량 추정 폐기)을 뒤집는 게 아니다. 그 위에 얹는다 ━━

docs/routine-logic-decision.md Q7 은 두 가지 이유로 kg 을 뺐다:

    ① 사진·인바디로 **1RM 을 추정할 방법이 없다**
    ② LLM 의 kg 추정은 어떤 출처로도 방어 불가

①은 지금도 사실이고, 이 모듈도 1RM 을 추정하지 않는다. ②는 **LLM 에게
시키지 않고 코드가 계산**하는 것으로 피한다 (점수·우선순위를 코드가 정하는
것과 같은 원칙 — services/scoring.py 모듈 주석).

그래서 이 모듈이 내는 것은 «처방» 이 아니라 **출발점**이다:

    처방(무엇을 목표로 멈추는가)  RIR — 그대로 유지. 이게 여전히 본체다
    출발점(첫 세트를 몇 kg 으로)  이 모듈 — 첫 세트에서 뭘 집을지만 정한다

두 개는 충돌하지 않는다. 첫 세트를 집어 들고 나면 그 다음부터는 RIR 이
무게를 끌고 간다(Zourdos 2016 자가조절). 이 모듈이 없으면 초보자는 첫
세트에서 **아무 근거 없이** 무게를 고르게 되고, 그게 지금 상태다.

━━ ⚠️ 계수는 «임시 보수값» 이다. 공개 규준표가 아니다 ━━

아래 `_LOAD_FRACTION` 은 **공개된 근력 규준표에서 가져온 값이 아니다.**
초보자 보조운동(해머컬·카프레이즈·스컬크러셔 같은 고립운동)의 시작 중량은
빅3(스쿼트·벤치·데드리프트)와 달리 표준화된 공개 규준표가 드물고, 확인되지
않은 수치를 «규준» 이라고 부르면 이 서비스의 «모든 수치에 출처가 있다» 는
강점이 그 자리에서 무너진다.

그래서 지금 값은 **안전 쪽으로 치우친 보수적 출발점**이고, 그렇게만 말한다:

    · 사용자 문구에도 «권장 중량» 이 아니라 «보통 이 정도에서 시작합니다»
    · 범위 하단을 기본으로 제시하고, 올리는 판단은 RIR 에 맡긴다
    · 반올림은 **항상 내림** (무거운 쪽으로 튀지 않게)

⚠️ 교체 경로 — ExRx·Strength Level 같은 공개 규준표를 확인해 `_LOAD_FRACTION`
   만 갈아끼우면 된다. 호출부는 손댈 필요가 없다. 그때 이 절도 같이 지운다.
   (services/vlm.py 의 KEYPOINT_SIGMA 가 COCO 값을 임시로 쓰면서 남긴 것과
   같은 종류의 표시다 — 임시값을 임시값이라고 적어두는 것)

━━ 피드백으로 조정되는 구조 ━━

`adjust` 배율을 받는다. 1.0 이 기본이고, 사용자가 "너무 무거웠다/가벼웠다"고
하면 호출부가 배율을 낮추거나 올려 같은 함수를 다시 부른다. 중량을 DB 에
저장하지 않는 이유가 여기 있다 — 저장하면 체중이 바뀌었을 때 옛 값이 남고,
배율만 저장하면 항상 **현재 체중 기준으로 다시 계산**된다.
"""

from __future__ import annotations

from typing import Any

#: 헬스장에 실제로 있는 덤벨 단위(kg). 계산값을 여기로 스냅한다 —
#: "7.3kg 으로 시작하세요" 는 집을 수 없는 무게라 안내가 안 된다.
_DUMBBELL_STEPS = (2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30)

#: 바벨은 봉 자체가 무게다. 초보자용 경량봉(10~15kg)과 올림픽봉(20kg)이 섞여
#: 있어 «봉만» 을 하한으로 둔다 — 첫 세트는 봉만으로 시작하는 게 표준 지도다.
_BARBELL_MIN_KG = 20

#: 근육군 → 체중 대비 비율 (덤벨 **한 짝** 기준, 초보 첫 세트).
#: ⚠️ 모듈 docstring 참고 — 공개 규준표가 아니라 보수적 임시값이다.
#:    고립운동(레이즈·컬)일수록 작고, 큰 근육의 복합운동일수록 크다.
_LOAD_FRACTION: dict[str, tuple[float, float]] = {
    "등": (0.10, 0.16),
    "가슴": (0.10, 0.16),
    "대퇴사두": (0.12, 0.20),
    "햄스트링·둔근": (0.12, 0.20),
    "종아리": (0.12, 0.20),
    "어깨": (0.04, 0.07),
    "후면 어깨": (0.03, 0.05),
    "삼두": (0.05, 0.08),
    "이두": (0.05, 0.08),
    "코어": (0.05, 0.09),
}

#: 성별 보정. ⚠️ 이것도 임시값이다 — 같은 체중이면 상체 근력에서 평균적으로
#: 차이가 있다는 일반적 관찰을 보수적으로 반영한 것이고, 개인차가 이 차이보다
#: 훨씬 크다. 그래서 폭을 작게 두고, 모르면(성별 미상) 보정하지 않는다.
_GENDER_SCALE = {"MALE": 1.0, "FEMALE": 0.7}

#: 배율 안전 범위. 피드백이 폭주해도 여기서 잘린다.
_ADJUST_RANGE = (0.5, 2.0)


def _snap_down(value: float, steps: tuple[int, ...]) -> int | None:
    """실제 존재하는 단위로 **내림** 스냅. 가장 가벼운 것보다 작으면 None."""
    usable = [s for s in steps if s <= value]
    return max(usable) if usable else None


def starting_load(
    muscle_group: str | None,
    equipments: list[str] | None,
    inbody: dict[str, Any] | None,
    adjust: float = 1.0,
) -> dict[str, Any] | None:
    """이 운동의 시작 중량 가이드. 낼 수 없으면 None.

    None 을 내는 경우 — 그때는 **화면에 아무것도 안 띄운다** (RIR 안내만 남는다):
        · 맨몸 운동            → 들 무게가 없다
        · 인바디 없음·체중 없음 → 계산 근거가 없다. 지어내지 않는다
        · 근육군을 모름         → 어느 비율을 쓸지 정할 수 없다

    Args:
        adjust: 피드백 배율. 1.0 이 기본, 사용자가 무겁다고 하면 <1.0.

    Returns:
        {"min_kg", "max_kg", "equipment", "basis", "adjust"} 또는 None.
        ⚠️ basis 는 **사용자에게 그대로 보여도 되는 근거 문장**이다.
    """
    gear = {g.upper() for g in (equipments or [])}
    external = gear - {"BODY WEIGHT"}
    if not external:
        return None  # 맨몸 — 들 무게가 없다

    body = (inbody or {}).get("body") or inbody or {}
    weight = body.get("weight")
    if not isinstance(weight, (int, float)) or not 25 <= weight <= 250:
        return None  # 체중을 모르면 계산하지 않는다 (인바디는 선택 입력이다)

    span = _LOAD_FRACTION.get((muscle_group or "").strip())
    if span is None:
        return None

    scale = _GENDER_SCALE.get(str(body.get("gender") or "").upper(), 1.0)
    factor = min(max(adjust, _ADJUST_RANGE[0]), _ADJUST_RANGE[1])
    low_raw = weight * span[0] * scale * factor
    high_raw = weight * span[1] * scale * factor

    # 바벨은 봉이 하한이다 — 봉보다 가벼운 제안은 물리적으로 불가능하다.
    if "BARBELL" in external:
        low = _BARBELL_MIN_KG
        high = max(_BARBELL_MIN_KG, int(high_raw + weight * 0.1))
        return {
            "min_kg": low,
            "max_kg": high,
            "equipment": "BARBELL",
            "basis": "봉(20kg)만으로 시작해 10회가 여유로우면 원판을 더합니다.",
            "adjust": factor,
        }

    low = _snap_down(low_raw, _DUMBBELL_STEPS)
    high = _snap_down(high_raw, _DUMBBELL_STEPS)
    if low is None:
        # 계산값이 가장 가벼운 덤벨보다 작다 — 그게 정답이다 (더 낮출 수 없다).
        low = _DUMBBELL_STEPS[0]
        high = max(high or _DUMBBELL_STEPS[0], _DUMBBELL_STEPS[0])
    if high is None or high < low:
        high = low

    return {
        "min_kg": low,
        "max_kg": high,
        "equipment": "DUMBBELL",
        # ⚠️ «권장» 이라고 쓰지 않는다 — 개인 처방이 아니라 출발점이다.
        "basis": "비슷한 체중·성별의 초보자가 보통 시작하는 무게입니다.",
        "adjust": factor,
    }


def guide_text(load: dict[str, Any] | None) -> str | None:
    """화면·문구용 한 줄. ⚠️ **RIR 안내와 함께** 보여야 한다 — 이 숫자는
    출발점일 뿐이고, 실제 조절은 "N회 남기고 멈추기" 가 한다.
    """
    if not load:
        return None
    lo, hi = load["min_kg"], load["max_kg"]
    span = f"{lo}kg" if lo == hi else f"{lo}~{hi}kg"
    return f"{span}로 시작해 보세요 — 가볍게 느껴지면 한 단계 올리면 됩니다."
