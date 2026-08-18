#!/usr/bin/env python
"""F09 종합 진단이 사진을 직접 보게 된 변경의 검증 (2026-08-17).

무엇을 잡는가
    이 변경의 실패 형태는 "에러"가 아니라 **조용한 계약 깨짐**이다.
    - 새 필드가 빠져도 200 이 나간다 (프론트에서 undefined 로만 드러난다)
    - 사진을 못 구했는데 프롬프트가 "사진이 주어졌다"고 말하면, 모델이 본 적
      없는 실루엣을 지어낸다 — 에러 없이 그럴듯한 거짓말이 저장된다
    - confidence 를 0~100 으로 보내오면 0.85 대신 85 가 저장된다

    LLM 호출 없이 돈다 (파서·프롬프트 조립만 태운다).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.prompts.overall_diagnosis import build_overall_prompt  # noqa: E402
from app.schemas.analysis import OverallDiagnosisDto  # noqa: E402
from app.services.scoring import decide_direction, rank_priority  # noqa: E402
from app.services.vlm import parse_overall_response  # noqa: E402

FAILED = 0
CLASSES = {"Torso", "Left_Upper_Arm", "Right_Upper_Arm"}


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"  [{'O' if ok else 'X'}] {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED += 1


def main() -> int:
    print("F09 종합 진단 — 사진 입력 변경 검증\n")

    # ── 1. 정상 응답 파싱 ─────────────────────────────────────────────────
    print("1. 응답 파싱")
    out = parse_overall_response(
        {
            "summary": "  전신을 고루 하면서 팔에 세트를 더 얹는 방향입니다.  ",
            "silhouette": "  어깨 대비 허리가 넓어 전체 윤곽이 다릅니다.  ",
            "key_differences": ["상체 대비 하체 볼륨이 적습니다", "어깨-허리 폭 비율이 다릅니다"],
            "strengths": ["하체가 목표에 가깝습니다"],
            "cautions": [],
            "confidence": 0.82,
        }
    )
    check("silhouette 공백 제거", out["silhouette"] == "어깨 대비 허리가 넓어 전체 윤곽이 다릅니다.")
    check("key_differences 보존", out["key_differences"] == [
        "상체 대비 하체 볼륨이 적습니다", "어깨-허리 폭 비율이 다릅니다"])
    check("confidence 그대로", out["confidence"] == 0.82, str(out["confidence"]))
    check("LLM 의 priority_parts 는 버린다", "priority_parts" not in out, str(sorted(out)))

    # ── 2. confidence 방어 ────────────────────────────────────────────────
    print("\n2. confidence 정규화 — 모델이 0~100 으로 답하는 경우가 잦다")
    for raw, want, label in [
        (85, 0.85, "85 → 0.85"),
        (0.5, 0.5, "0.5 그대로"),
        (1, 1.0, "1 은 100% 로 본다"),
        ("높음", None, "문자열은 버린다"),
        (None, None, "없으면 None"),
        (-3, None, "음수는 버린다"),
        (True, None, "bool 은 숫자가 아니다"),
    ]:
        got = parse_overall_response({"confidence": raw})["confidence"]
        check(label, got == want, f"입력={raw!r} → {got!r}")

    # ── 3. 필드가 통째로 없는 응답 (구 모델·형식 붕괴) ───────────────────
    print("\n3. 새 필드가 아예 없는 응답 — 옛 형식으로 답해도 죽지 않아야 한다")
    legacy = parse_overall_response(
        {"summary": "요약", "strengths": [], "cautions": []}
    )
    check("silhouette 은 None", legacy["silhouette"] is None)
    check("key_differences 는 빈 배열", legacy["key_differences"] == [])
    check("confidence 는 None", legacy["confidence"] is None)
    check("기존 필드는 그대로", legacy["summary"] == "요약")

    # ── 4. 프롬프트가 사진 유무를 정직하게 말하는가 ──────────────────────
    print("\n4. 프롬프트 — 사진이 없을 때 있다고 말하면 안 된다")
    with_img = build_overall_prompt(parts=[], blocked=[], failed=[], inbody=None, has_images=True)
    without = build_overall_prompt(parts=[], blocked=[], failed=[], inbody=None, has_images=False)
    check("사진 있음: 순서를 알려준다", "1번째 — 목표 체형" in with_img)
    check("사진 있음: 없다고 말하지 않는다", "제공되지 않았습니다" not in with_img)
    check("사진 없음: 없다고 밝힌다", "제공되지 않았습니다" in without)
    check("사진 없음: 있다고 말하지 않는다", "1번째 — 목표 체형" not in without)
    check("사진 없음: confidence 상한을 지시한다", "0.3" in without)

    # ── 5. API 계약 — 새 필드는 전부 선택이어야 한다 ─────────────────────
    print("\n5. API 계약 — 옛 세션(새 컬럼이 null)도 응답이 만들어져야 한다")
    dto = OverallDiagnosisDto(status="DONE")
    check("새 필드 없이 생성된다", dto.silhouette is None and dto.confidence is None)
    check("key_differences 기본값은 빈 배열", dto.key_differences == [])
    dumped = dto.model_dump()
    check("응답에 새 키가 존재한다", {"silhouette", "key_differences", "confidence"} <= set(dumped))
    check(
        "기존 키가 사라지지 않았다",
        {"similarity_score", "score_source", "summary", "priority_parts",
         "strengths", "cautions", "status"} <= set(dumped),
    )

    # ── 6. 우선순위는 규칙이 정한다 (VLM 아님) ───────────────────────────
    print("\n6. 우선순위 — 규칙이 정하고 LLM 은 설명만 한다")
    parts = [
        {"class_name": "Torso", "gap_level": "SLIGHT", "confidence": "HIGH"},
        {"class_name": "Left_Upper_Arm", "gap_level": "SIGNIFICANT", "confidence": "MEDIUM"},
        {"class_name": "Right_Upper_Arm", "gap_level": "SIGNIFICANT", "confidence": "HIGH"},
        {"class_name": "Left_Upper_Leg", "gap_level": None, "confidence": "LOW"},
    ]
    picked, why = rank_priority(parts)
    check("격차 큰 순", picked[:2] == ["Right_Upper_Arm", "Left_Upper_Arm"], str(picked))
    check("같은 등급이면 확신도 높은 쪽 먼저", picked[0] == "Right_Upper_Arm", str(picked))
    check("판단 불가 부위는 제외", "Left_Upper_Leg" not in picked, str(picked))
    check("3개 상한", len(picked) <= 3, str(picked))
    check("근거 문장이 남는다", "격차가 큰 순" in why, why)
    check("같은 입력 → 같은 출력", rank_priority(parts)[0] == picked)
    check("판단된 부위가 없으면 빈 목록", rank_priority([{"class_name": "X", "gap_level": None}])[0] == [])

    # ── 7. 측정 금지 — 종합 프롬프트에도 있어야 한다 ──────────────────────
    print("\n7. 프롬프트 — VLM 에게 몸을 재게 하지 않는다")
    sys_prompt = __import__("app.prompts.overall_diagnosis", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT
    for label, needle in [
        ("치수 추정 금지", "실제 신체 치수"),
        ("근육량·체지방률 금지", "실제 근육량"),
        ("절대 크기 비교 금지", "절대 크기"),
        ("가치 판단 금지", "가치 판단"),
        ("관찰 어투 제시", "차이가 관찰됩니다"),
        ("우선순위는 규칙이 정한다", "우선순위는 당신이 정하지 않습니다"),
    ]:
        check(label, needle in sys_prompt)

    # ── 8. 비교 한계 필드 ─────────────────────────────────────────────────
    print("\n8. comparison_limitations — 응답 계약")
    dto2 = OverallDiagnosisDto(status="DONE")
    check("기본값은 빈 배열", dto2.comparison_limitations == [])
    check("응답에 키가 있다", "comparison_limitations" in dto2.model_dump())

    # ── 9. 개선 방향도 규칙이 정한다 ─────────────────────────────────────
    print("\n9. 개선 방향 — 체지방 판정은 인바디에서, LLM 은 설명만")
    CUT = {"mode": "CUT", "basis": "BODY_FAT_MEASURED", "reason": "체지방률 27%…"}
    BAL = {"mode": "BALANCE", "basis": "BODY_FAT_MEASURED", "reason": "체지방률 18%…"}
    NOI = {"mode": "BALANCE", "basis": "NO_INBODY", "reason": "인바디가 없어…"}
    big = [{"class_name": "a", "gap_level": "SIGNIFICANT"}]
    small = [{"class_name": "a", "gap_level": "SLIGHT"}]
    for label, mi, parts_, blocked, want in [
        ("CUT → 감량 우선", CUT, big, 0, "FAT_LOSS_FIRST"),
        ("BALANCE+큰격차 → 근력", BAL, big, 0, "STRENGTH_FIRST"),
        ("BALANCE+작은격차 → 유지", BAL, small, 0, "MAINTAIN"),
        ("판단불가 과반 → 제한", BAL, small, 5, "LIMITED"),
        ("판단 0 → 제한", BAL, [], 3, "LIMITED"),
    ]:
        got = decide_direction(mi, parts_, blocked)
        check(label, got["priority"] == want, f"{got['priority']}")
    noi = decide_direction(NOI, big, 0)
    check(
        "인바디 없으면 감량 판단 안 함을 근거에 남긴다",
        "체지방 정보가 없어" in noi["reason"] and "감량이 급한 상태가 아니고" not in noi["reason"],
        noi["reason"][:50],
    )
    check("모드 근거(basis)를 그대로 승계", noi["mode_basis"] == "NO_INBODY")
    check("같은 입력 → 같은 출력", decide_direction(CUT, big, 0) == decide_direction(CUT, big, 0))

    # ── 10. 프로필·전략 파싱 + 단계 발명 금지 ────────────────────────────
    print("\n10. 프로필·전략 — LLM 은 문장만, 판정은 안 보낸다")
    out2 = parse_overall_response({
        "user_profile": {"summary": " 하체 강조 ", "characteristics": ["a", "b", "c", "d"]},
        "reference_profile": {"summary": "상체 강조", "characteristics": ["x"]},
        "direction_summary": " 근력 우선 ",
        "strategy_focus": ["상체 볼륨", "코어", "여분"],
        "next_cycle": "4주 뒤 재측정",
        "priority": "FAT_LOSS_FIRST",   # ← LLM 이 판정을 보내와도
        "mode": "CUT",                   # ← 버려야 한다
    })
    check("user_profile 공백 제거", out2["user_profile"]["summary"] == "하체 강조")
    check("characteristics 3개 상한", len(out2["user_profile"]["characteristics"]) == 3)
    check("strategy_focus 2개 상한", len(out2["strategy_focus"]) == 2)
    check("direction_summary 공백 제거", out2["direction_summary"] == "근력 우선")
    check("LLM 의 priority 는 버린다", "priority" not in out2, str(sorted(out2)))
    check("LLM 의 mode 는 버린다", "mode" not in out2, str(sorted(out2)))
    check("프로필 없으면 None", parse_overall_response({})["user_profile"] is None)

    sysp = __import__("app.prompts.overall_diagnosis", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT
    for label, needle in [
        ("골격 확정 금지", "골격 구조"),
        ("레퍼런스 도달 보장 금지", "도달 보장 대상"),
        ("단계 발명 금지", "로드맵을 만들지 마세요"),
        ("프로필은 비교문이 아님", "비교문이 아닙니다"),
        ("방향은 새로 정하지 않음", "방향을 새로 정하지 마세요"),
    ]:
        check(label, needle in sysp)

    print("\n" + ("[O] 전부 통과" if not FAILED else f"[X] {FAILED}건 실패"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
