"""F08 · F09 진단 파이프라인 오프라인 검증 (DB · API 키 불필요).

    python scripts/verify_analysis.py

확인하는 것
    1. 워커 핸들러가 VLM_PART / VLM_OVERALL 로 등록되는가
    2. 스케일 불변 수치가 촬영 거리에 흔들리지 않는가  ← 조용히 틀리는 지점
    3. 오버레이가 부위 색을 제대로 칠하는가
    4. 망가진 LLM 응답에서 **성한 부위만** 살아남는가  ← 부분 실패 허용의 핵심
    5. 프롬프트에 부위·수치·인바디가 실제로 들어가는가

⚠️ verify_segmap.py 와 달리 실제 샘플이 필요 없다. 합성 데이터로 규약만 본다.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.prompts.overall_diagnosis import build_overall_prompt  # noqa: E402
from app.prompts.part_diagnosis import build_part_prompt  # noqa: E402
from app.services import segmap, vlm  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)


# --------------------------------------------------------------------------- #


def _segment(class_name: str, px: int, x: int, y: int, w: int, h: int) -> dict:
    return {
        "class_name": class_name,
        "segment_id": f"seg-{class_name}",
        "pixel_count": px,
        "bbox_x": x,
        "bbox_y": y,
        "bbox_w": w,
        "bbox_h": h,
        "is_valid": True,
        "is_truncated": False,
    }


PERSON = {"Torso", "Left_Upper_Arm", "Right_Upper_Arm", "Left_Upper_Leg"}


def test_handlers() -> None:
    print("\n1. 핸들러 등록")
    from app.schemas.enums import JobKind
    from app.worker import registry
    from app.worker.handlers import vlm as _vlm_handler  # noqa: F401

    check("VLM_PART 등록", JobKind.VLM_PART in registry.HANDLERS)
    check("VLM_OVERALL 등록", JobKind.VLM_OVERALL in registry.HANDLERS)


def test_scale_invariance() -> None:
    print("\n2. 스케일 불변 수치")

    ref = {
        "Torso": _segment("Torso", 40000, 100, 100, 200, 300),
        "Left_Upper_Arm": _segment("Left_Upper_Arm", 8000, 60, 120, 60, 200),
        "Right_Upper_Arm": _segment("Right_Upper_Arm", 8000, 300, 120, 60, 200),
        "Left_Upper_Leg": _segment("Left_Upper_Leg", 20000, 120, 400, 90, 250),
    }
    # 같은 사람을 2배 크게(=가까이서) 찍은 것. 픽셀은 4배, 비율은 그대로여야 한다.
    near = {
        name: _segment(
            name,
            r["pixel_count"] * 4,
            r["bbox_x"] * 2,
            r["bbox_y"] * 2,
            r["bbox_w"] * 2,
            r["bbox_h"] * 2,
        )
        for name, r in ref.items()
    }

    names = ["Torso", "Left_Upper_Arm", "Right_Upper_Arm", "Left_Upper_Leg"]
    result = segmap.compare_parts(ref, near, names, PERSON)
    diffs = [abs(result["parts"][n]["diff_pct"]["area_share"]) for n in names]
    check(
        "촬영 거리가 2배 달라도 면적 차이 0%",
        max(diffs) < 0.5,
        f"최대 {max(diffs):.2f}%",
    )

    # 실제로 팔이 굵어진 경우는 잡아내야 한다 (불변성이 둔감함이 되면 안 된다).
    thicker = dict(near)
    thicker["Left_Upper_Arm"] = _segment("Left_Upper_Arm", 8000 * 4 * 2, 120, 240, 170, 400)
    grown = segmap.compare_parts(ref, thicker, names, PERSON)
    delta = grown["parts"]["Left_Upper_Arm"]["diff_pct"]["area_share"]
    check("팔 면적이 실제로 늘면 +로 잡힌다", delta is not None and delta > 20, f"{delta}%")

    asym = dict(ref)
    asym["Right_Upper_Arm"] = _segment("Right_Upper_Arm", 5600, 300, 120, 50, 200)
    sym = segmap.symmetry(asym, names, PERSON)
    check(
        "좌우 비대칭 검출",
        "Upper_Arm" in sym and sym["Upper_Arm"]["diff_pct"] > 25,
        str(sym),
    )
    # 방향이 빠지면 좌·우 문장이 똑같아진다 (segmap.symmetry 주석 참고)
    check("비대칭 방향 포함", sym.get("Upper_Arm", {}).get("larger") == "LEFT", str(sym))


def test_overlay() -> None:
    print("\n3. 오버레이 생성")

    # 라벨 맵: 왼쪽 절반 Torso(22), 오른쪽 절반 Left_Upper_Arm(15)
    seg_map = Image.new("L", (100, 200), 0)
    for x in range(100):
        for y in range(200):
            seg_map.putpixel((x, y), 22 if x < 50 else 15)

    photo = Image.new("RGB", (100, 200), (200, 200, 200))
    label_map = {"22": "Torso", "15": "Left_Upper_Arm"}

    overlay, painted = segmap.build_overlay(
        photo=photo,
        seg_map=seg_map,
        parts=[("Torso", "#FF0000"), ("Left_Upper_Arm", "#0000FF")],
        label_map=label_map,
    )
    check("두 부위 모두 칠해짐", painted == ["Torso", "Left_Upper_Arm"], str(painted))

    left, right = overlay.getpixel((25, 100)), overlay.getpixel((75, 100))
    check("Torso 쪽이 붉게", left[0] > left[2], str(left))
    check("Left_Upper_Arm 쪽이 푸르게", right[2] > right[0], str(right))
    check("질감이 남아 있다(단색 아님)", left[1] > 40, f"G={left[1]}")

    # 맵에 없는 부위는 조용히 빠지고 범례에도 안 들어가야 한다
    _, painted2 = segmap.build_overlay(
        photo=photo,
        seg_map=seg_map,
        parts=[("Torso", "#FF0000"), ("Right_Upper_Leg", "#00FF00")],
        label_map=label_map,
    )
    check("맵에 없는 부위는 제외", painted2 == ["Torso"], str(painted2))


def test_partial_acceptance() -> None:
    print("\n4. 망가진 응답에서 성한 부위만 살리기")

    names = ["Torso", "Left_Upper_Arm", "Right_Upper_Arm", "Left_Upper_Leg"]
    broken = {
        "parts": [
            # 정상
            {
                "class_name": "Torso",
                "differences": ["복부 라인이 흐림"],
                "assessment": "몸통 볼륨이 부족합니다.",
                "gap_level": "MODERATE",
                "priority": 2,
                "confidence": "HIGH",
            },
            # 소문자 enum — 교정되어 살아남아야 한다
            {
                "class_name": "Left_Upper_Arm",
                "differences": "상완이 얇음",  # 배열이 아니라 문자열
                "gap_level": "slight",
                "priority": 99,  # 범위 밖 → None
                "confidence": "high",
            },
            # 판단 불가 — gap_level null 은 정상 경로다
            {
                "class_name": "Right_Upper_Arm",
                "gap_level": None,
                "confidence": "LOW",
                "blocked_reason": "긴팔에 가려 판단 불가",
            },
            # 존재하지 않는 부위 → 버려야 한다
            {"class_name": "Left_Eyebrow", "gap_level": "NONE", "confidence": "HIGH"},
            # 목록에 없는 등급 → 임의 매핑하지 않고 버린다
            {"class_name": "Left_Upper_Leg", "gap_level": "VERY_HIGH", "confidence": "HIGH"},
        ]
    }

    out = vlm.parse_part_response(broken, names)
    got = {p["class_name"]: p for p in out["results"]}

    check("정상 부위 채택", "Torso" in got)
    check("소문자 enum 교정", got.get("Left_Upper_Arm", {}).get("gap_level") == "SLIGHT")
    check(
        "문자열 differences 를 배열로",
        got.get("Left_Upper_Arm", {}).get("differences") == ["상완이 얇음"],
    )
    check("범위 밖 priority 는 None", got.get("Left_Upper_Arm", {}).get("priority") is None)
    check(
        "판단 불가 부위는 살리되 gap_level null",
        "Right_Upper_Arm" in got and got["Right_Upper_Arm"]["gap_level"] is None,
    )
    check("모르는 부위는 버림", "Left_Eyebrow" not in got)
    check("목록에 없는 등급은 실패 처리", "Left_Upper_Leg" in out["missing"])
    check("실패는 그 부위만", len(out["results"]) == 3, f"{len(out['results'])}/4 생존")

    overall = vlm.parse_overall_response(
        {
            # ⚠️ LLM 이 보내와도 **버려야 하는 것 둘.** 유사도는 규칙이 계산하고
            #    (scoring.compute_similarity, score_source=RULE), 우선순위도 규칙이
            #    정한다 (scoring.rank_priority, 2026-08-17). 여기서 통과시키면
            #    "AI 가 뱉은 숫자·순서"가 화면과 루틴 가중에 그대로 나간다.
            "similarity_score": 140,
            "summary": "요약",
            "priority_parts": ["Torso", "Nonexistent_Part"],
            "strengths": "하체가 좋습니다",  # 문자열 → 배열
            "cautions": [],
        }
    )
    check("LLM 이 보낸 점수는 버림 (규칙이 계산)", "similarity_score" not in overall)
    check("LLM 이 보낸 우선순위는 버림 (규칙이 정함)", "priority_parts" not in overall)
    check("strengths 배열화", overall["strengths"] == ["하체가 좋습니다"])


def test_prompts() -> None:
    print("\n5. 프롬프트 구성")

    parts = [
        {
            "class_name": "Torso",
            "name_ko": "몸통",
            "color_hex": "#FF0000",
            "inbody_segment": "TRUNK",
        },
        {
            "class_name": "Left_Upper_Arm",
            "name_ko": "왼팔 상완",
            "color_hex": "#0000FF",
            "inbody_segment": "LEFT_ARM",
        },
    ]
    ref = {
        "Torso": _segment("Torso", 40000, 100, 100, 200, 300),
        "Left_Upper_Arm": _segment("Left_Upper_Arm", 8000, 60, 120, 60, 200),
    }
    user = {
        "Torso": _segment("Torso", 38000, 100, 100, 190, 300),
        "Left_Upper_Arm": _segment("Left_Upper_Arm", 6000, 60, 120, 50, 200),
    }
    names = ["Torso", "Left_Upper_Arm"]
    metrics = segmap.compare_parts(ref, user, names, PERSON)

    inbody = {
        "body": {"weight": 63.5, "skeletal_muscle_mass": 28.8, "body_fat_percentage": 18.9},
        "segments": {
            "TRUNK": {"lean_mass": 22.8, "lean_percentage": 94.5},
            "LEFT_ARM": {"lean_mass": 2.72, "lean_percentage": 89.9},
        },
    }

    prompt = build_part_prompt(
        parts=parts,
        metrics=metrics,
        ref_symmetry={},
        user_symmetry={},
        inbody=inbody,
    )
    check("색 범례 포함", "#FF0000" in prompt and "Left_Upper_Arm" in prompt)
    # 2026-08-14 개정: 사진 간 크기 수치는 프롬프트에서 폐기됐다 (각도·자세·옷
    # 흡수로 사이비 정밀도). 표가 "없어야" 하고, 수치 발명 금지 안내가 "있어야" 한다.
    check("크기 비교 표 없음", "면적몫" not in prompt and "면적 차이" not in prompt)
    check("수치 발명 금지 안내 포함", "크기 비교 수치는 제공하지 않습니다" in prompt)
    check("인바디 부위 매핑 포함", "LEFT_ARM" in prompt and "2.72" in prompt)
    check("부위 개수 명시", f"{len(parts)}개 부위" in prompt)

    no_inbody = build_part_prompt(
        parts=parts, metrics=metrics, ref_symmetry={}, user_symmetry={}, inbody=None
    )
    check("인바디 없어도 프롬프트 생성", "인바디 결과가 없습니다" in no_inbody)

    overall_prompt = build_overall_prompt(
        parts=[
            {
                "class_name": "Torso",
                "gap_level": "MODERATE",
                "priority": 1,
                "confidence": "HIGH",
                "assessment": "몸통 볼륨 부족",
                "differences": ["복부 라인 흐림"],
            }
        ],
        blocked=[{"class_name": "Left_Upper_Arm", "blocked_reason": "긴팔에 가림"}],
        failed=["Right_Upper_Arm"],
        inbody=inbody,
    )
    check("종합 프롬프트에 판단 불가 구분", "판단 불가" in overall_prompt)
    check("종합 프롬프트에 실패 부위 분리", "진단 실패 부위" in overall_prompt)
    check("종합 프롬프트에 이미지 없음", "data:image" not in overall_prompt)


def test_citation_routing() -> None:
    print("\n7. 인바디 인용 라우팅 (표준 편차 큰 순 최대 2곳 · 전완 제외)")

    from app.prompts.part_diagnosis import _citation_targets

    part_to_segment = {
        "Left_Upper_Arm": "LEFT_ARM",
        "Left_Lower_Arm": "LEFT_ARM",
        "Torso": "TRUNK",
    }

    def m(diff: float) -> dict:
        stats = {"area_share": 0.1, "width_share": 0.1, "height_share": 0.1, "aspect": 1.0}
        return {
            "reference": dict(stats),
            "user": dict(stats),
            "diff_pct": {"area_share": diff, "width_share": diff, "height_share": 0.0},
            "user_truncated": False,
        }

    metrics = {
        "parts": {
            "Left_Upper_Arm": m(-23.5),
            "Left_Lower_Arm": m(-6.7),
            "Torso": m(-0.3),
        }
    }
    inbody = {
        "body": {"weight": 63.5},
        "segments": {
            "LEFT_ARM": {"lean_mass": 2.42, "lean_percentage": 82.1},
            "TRUNK": {"lean_mass": 22.8, "lean_percentage": 94.5},
        },
    }

    targets = _citation_targets(part_to_segment, metrics, inbody)
    check("격차 큰 부위가 세그먼트 대표", targets.get("LEFT_ARM") == "Left_Upper_Arm", str(targets))
    check("전완은 대표가 될 수 없음", "Left_Lower_Arm" not in targets.values())

    # ⚠️ 인용은 최대 2곳이다. 세그먼트마다 하나씩 뽑으면 9부위 중 5장에 숫자가
    #    붙어 진단이 표처럼 읽힌다 (2026-08-15 실사용 — 인바디를 항상 제출하는 운영).
    five = {
        **part_to_segment,
        "Left_Upper_Leg": "LEFT_LEG",
        "Right_Upper_Leg": "RIGHT_LEG",
        "Right_Upper_Arm": "RIGHT_ARM",
    }
    many = {
        "parts": {
            **metrics["parts"],
            "Left_Upper_Leg": m(-9),
            "Right_Upper_Leg": m(-9),
            "Right_Upper_Arm": m(-20),
        }
    }
    inbody_five = {
        "body": {},
        "segments": {
            "LEFT_ARM": {"lean_percentage": 89.9},
            "RIGHT_ARM": {"lean_percentage": 90.6},
            "TRUNK": {"lean_percentage": 94.5},
            "LEFT_LEG": {"lean_percentage": 98.2},
            "RIGHT_LEG": {"lean_percentage": 99.1},
        },
    }
    wide = _citation_targets(five, many, inbody_five)
    check("5세그먼트여도 인용은 2곳", len(wide) == 2, str(wide))
    check(
        "표준에서 가장 벗어난 쪽이 뽑힘 (평균에 가까운 다리는 탈락)",
        set(wide) == {"LEFT_ARM", "RIGHT_ARM"},
        str(wide),
    )
    check("인바디가 없으면 인용 없음", _citation_targets(part_to_segment, metrics, None) == {})

    # ⚠️ lean_percentage 는 DB 컬럼이 아니라 raw_ocr 에서 읽는 선택값이다
    #    (inbody_repo.to_prompt_payload). 없다고 인용을 통째로 없애면 인바디를
    #    제출했는데 수치가 한 번도 안 나온다 — 면적 격차 순으로 폴백하되 상한은 유지.
    no_pct = {
        "body": {},
        "segments": {"LEFT_ARM": {"lean_mass": 2.4}, "TRUNK": {"lean_mass": 24.0}},
    }
    fallback = _citation_targets(part_to_segment, metrics, no_pct)
    check("표준 대비 %가 없어도 인용은 남음", len(fallback) == 2, str(fallback))
    check(
        "폴백도 전완은 제외",
        "Left_Lower_Arm" not in fallback.values(),
        str(fallback),
    )

    # 프롬프트에 [인용] 이 대표 부위 줄에만 붙는지
    parts = [
        {"class_name": n, "name_ko": n, "color_hex": "#111111", "inbody_segment": s}
        for n, s in part_to_segment.items()
    ]
    prompt = build_part_prompt(
        parts=parts, metrics=metrics, ref_symmetry={}, user_symmetry={}, inbody=inbody
    )
    marked = [ln for ln in prompt.splitlines() if "[인용]" in ln and ln.startswith("-")]
    check("인용 표시 2줄 (세그먼트 수만큼)", len(marked) == 2, f"{len(marked)}줄")
    check(
        "대표 부위 줄에만 표시",
        any("Left_Upper_Arm" in ln for ln in marked)
        and not any("Left_Lower_Arm" in ln for ln in marked),
    )


def test_retry_policy() -> None:
    print("\n6. 재시도 정책 (결정론적 실패 3배 과금 방지)")

    from app.services.vlm import VlmResponseError
    from app.worker.handlers.vlm import InputNotUsableError
    from app.worker.run import _is_retryable

    check("응답 형식 오류는 재시도 안 함", not _is_retryable(VlmResponseError("형식 오류")))
    check("입력 불가는 재시도 안 함", not _is_retryable(InputNotUsableError("부위 부족")))
    # 네트워크·일시 장애는 재시도해야 한다 — 전부 껐다가 진짜 복구 가능한 실패까지
    # 한 번에 죽이면 반대 방향으로 틀린다.
    check("일시적 오류는 재시도함", _is_retryable(TimeoutError("일시 장애")))
    check("일반 예외는 재시도함", _is_retryable(RuntimeError("알 수 없음")))

    from app.config import settings

    check(
        "죽은 설정값 제거됨",
        not hasattr(settings, "vlm_worker_concurrency"),
        "vlm_worker_concurrency",
    )


def test_blocked_without_inbody() -> None:
    """blocked + 인바디 없음 → gap_level 이 점수에 들어가면 안 된다.

    실연동(2026-08-14)에서 이 경로로 SIGNIFICANT 2건이 점수에 들어가 56점을
    만들었다 — 빼면 88점이었다. 프롬프트가 null 을 강제하지만 LLM 이 어긴다.
    시각으로도 못 보고 실측도 없는 등급은 근거가 0 이라 코드가 무효화한다. (A 발견)
    """
    print("\n[blocked + 인바디 없음]")
    resp = {
        "parts": [
            {
                "class_name": "Torso",
                "gap_level": "SIGNIFICANT",
                "confidence": "LOW",
                "blocked_reason": "옷에 가려 형태 확인 불가",
                "priority": 1,
            },
            {
                "class_name": "Left_Upper_Arm",
                "gap_level": "MODERATE",
                "confidence": "HIGH",
                "blocked_reason": None,
                "priority": 2,
            },
        ]
    }
    names = ["Torso", "Left_Upper_Arm"]

    without = {
        r["class_name"]: r["gap_level"]
        for r in vlm.parse_part_response(resp, names, inbody_available=False)["results"]
    }
    check("인바디 없으면 blocked 등급을 null 로 강등", without["Torso"] is None)
    check("정상 부위는 영향 없음", without["Left_Upper_Arm"] == "MODERATE")

    with_inbody = {
        r["class_name"]: r["gap_level"]
        for r in vlm.parse_part_response(resp, names, inbody_available=True)["results"]
    }
    check("인바디 있으면 유지 (인바디 근거 경로)", with_inbody["Torso"] == "SIGNIFICANT")


def test_baseline_separation() -> None:
    """기준선 분리 — 인바디 '표준 대비 %'가 목표 격차 판정을 오염시키지 않는가.

    ⚠️ 실측(2026-08-15)으로 잡은 결함의 회귀 방지다. 더미 인바디(표준 대비
       89.9~94.5%)가 들어가자, "실측이 사진을 이깁니다" 지시 때문에 모든 부위가
       평균 언저리 = '약간 부족'으로 눌렸다. 시각적으로는 격차가 명확한데도.
       평균과의 비교(인바디)와 목표와의 격차(레퍼런스)는 잣대가 다르다.
    """
    print("\n8. 기준선 분리 (평균 대비 vs 목표 격차)")

    from app.prompts.part_diagnosis import SYSTEM_PROMPT as part_system
    from app.prompts.part_diagnosis import _inbody_lr

    check("'실측이 사진을 이깁니다' 제거됨", "이깁니다" not in part_system)
    check(
        "표준 대비 %는 평균과의 비교라고 명시",
        "일반인 평균과" in part_system,
    )
    check(
        "보이는 부위 gap 은 이미지가 정한다고 명시",
        "gap_level 은 언제나 이미지가 정합니다" in part_system,
    )
    check(
        # ⚠️ 2854032 가 처방 few-shot 을 통째로 제거했다 (few-shot 자석 문제).
        #    그래서 "예시가 발산 케이스를 가르치는가" 는 더 이상 검사 대상이 아니다.
        #    지켜야 할 것은 **규칙 프로즈**가 평균≠목표를 여전히 말하는가다.
        "평균 대비와 목표 격차를 섞지 말라는 규칙이 남아 있음",
        "일반인 평균과" in part_system and "이미지가 정합니다" in part_system,
    )

    # 좌우 실측은 코드가 계산한다 — LLM 산수 금지
    lr = _inbody_lr(
        {
            "segments": {
                "LEFT_ARM": {"lean_mass": 2.72},
                "RIGHT_ARM": {"lean_mass": 2.74},
                "LEFT_LEG": {"lean_mass": 7.78},
                "RIGHT_LEG": {"lean_mass": 7.78},
            }
        }
    )
    check("팔 좌우 차이 계산 (0.7%)", abs(lr["팔"]["diff_pct"] - 0.73) < 0.05)
    check("오른쪽이 많음으로 방향 표기", lr["팔"]["larger"] == "RIGHT")
    check("다리 동일 → 대칭(None)", lr["다리"]["larger"] is None)
    check(
        "한쪽 없으면 계산 안 함", _inbody_lr({"segments": {"LEFT_ARM": {"lean_mass": 2.7}}}) == {}
    )
    check("인바디 없으면 빈 dict", _inbody_lr(None) == {})

    # 프롬프트에 실측 좌우가 픽셀 대칭과 나란히 실리는가
    parts = [
        {
            "class_name": "Left_Upper_Arm",
            "name_ko": "왼팔 상완",
            "color_hex": "#0000FF",
            "inbody_segment": "LEFT_ARM",
        },
        {
            "class_name": "Right_Upper_Arm",
            "name_ko": "오른팔 상완",
            "color_hex": "#00FF00",
            "inbody_segment": "RIGHT_ARM",
        },
    ]
    ref = {
        "Left_Upper_Arm": _segment("Left_Upper_Arm", 8000, 60, 120, 60, 200),
        "Right_Upper_Arm": _segment("Right_Upper_Arm", 8000, 260, 120, 60, 200),
    }
    names = ["Left_Upper_Arm", "Right_Upper_Arm"]
    prompt = build_part_prompt(
        parts=parts,
        metrics=segmap.compare_parts(ref, ref, names, PERSON),
        ref_symmetry={"Upper_Arm": {"diff_pct": 3.0, "larger": "LEFT"}},
        user_symmetry={"Upper_Arm": {"diff_pct": 14.2, "larger": "LEFT"}},
        inbody={
            "segments": {
                "LEFT_ARM": {"lean_mass": 2.72, "lean_percentage": 89.9},
                "RIGHT_ARM": {"lean_mass": 2.74, "lean_percentage": 90.6},
            }
        },
    )
    check("픽셀 좌우와 실측 좌우가 나란히 제공", "인바디 실측 근육량 (팔)" in prompt)
    check("조율 규칙 포함 (실측이 정답)", "좌우 근육량은 실측이 정답" in prompt)
    check(
        "인바디 블록에도 기준선 경고",
        "일반인 평균과의 비교" in prompt,
    )

    # F09 — 우선순위는 코드가 3개로 자른다 (프롬프트 지시만으로는 부족)
    # ⚠️ 2026-08-17 — 우선순위 상한(3개)은 파서가 아니라 규칙이 건다.
    #    scoring.rank_priority 가 정하고 verify_overall_silhouette 이 검증한다.
    from app.services.scoring import rank_priority

    picked, _ = rank_priority(
        [
            {"class_name": "Torso", "gap_level": "SIGNIFICANT", "confidence": "HIGH"},
            {"class_name": "Left_Upper_Arm", "gap_level": "SIGNIFICANT", "confidence": "HIGH"},
            {"class_name": "Right_Upper_Arm", "gap_level": "MODERATE", "confidence": "HIGH"},
            {"class_name": "Left_Lower_Arm", "gap_level": "SLIGHT", "confidence": "HIGH"},
        ]
    )
    check("우선순위 상위 3개만 (규칙)", len(picked) == 3, str(picked))

    from app.prompts.overall_diagnosis import SYSTEM_PROMPT as overall_system

    check("F09 는 우선순위를 정하지 않는다", "우선순위는 당신이 정하지 않습니다" in overall_system)
    # ⚠️ 요약이 "팔부터 하고 몸통은 그다음" 같은 **순서**를 말하면 실제 루틴과
    #    어긋난다. 루틴은 전신 기본 볼륨 + 약점 세트 가산이지 부위를 차례로
    #    돌지 않는다 (routine_templates.apply_weakness_boost). 진단 화면에서
    #    한 말과 다음 화면에서 받는 루틴이 다르면 신뢰가 바로 깨진다.
    check("F09 전신 루틴 전제 명시", "전신 + 약점 보강" in overall_system)
    check("F09 부위 순서 서술 금지", "순서대로 하지 않습니다" in overall_system)

    # 자기모순 게이트 — 관찰(differences)을 적어놓고 blocked 를 선언하면 코드가
    # blocked 를 해제한다. 실측(2026-08-15): few-shot 의 blocked 예시(몸통+평균
    # 94%)가 실데이터(TRUNK 94.5%)와 거의 일치하자 프레임째 복사됐다 —
    # 몸통을 눈으로 봤으면서 "시각 확인 불가"라고 보내왔다.
    contradicted = vlm.parse_part_response(
        {
            "parts": [
                {
                    "class_name": "Torso",
                    "differences": ["몸통 두께가 더 두드러집니다"],
                    "assessment": "옷에 가려 확인하지 못했지만…",
                    "gap_level": "SLIGHT",
                    "confidence": "MEDIUM",
                    "blocked_reason": "시각 확인 불가, 인바디 기준 판단",
                }
            ]
        },
        ["Torso"],
        inbody_available=False,
    )
    row = contradicted["results"][0]
    check("관찰 있는 blocked 는 해제", row["blocked_reason"] is None)
    check(
        "해제되면 gap 은 시각 근거로 유지 (인바디 없어도)",
        row["gap_level"] == "SLIGHT",
    )
    check(
        "blocked 예시가 실데이터와 안 겹침 (몸통·94% 제거)",
        "몸통은 눈으로 확인하지 못했" not in part_system and "평균의 94%" not in part_system,
    )
    check("blocked→differences 빈 배열 규칙 명시", "반드시 빈 배열" in part_system)
    # ⚠️ 처방 few-shot 이 제거되면서(2854032) "처방 문장을 복사하지 말라" 는
    #    지시도 함께 사라졌다 — 복사할 예시가 없으니 당연하다. 대신 더 강한
    #    규칙으로 대체됐다: 부위 카드에는 운동 방향 자체를 쓰지 않는다.
    check(
        "부위 카드에 처방을 넣지 않는다는 규칙 유지",
        "본 것을 말하는 곳" in part_system,
    )
    # ⚠️ 2026-08-17 변경 — 처방을 "상위 1·2 부위만" 에서 **부위 카드 전면 금지**로
    #    바꿨다. 부위마다 방향을 달면 그 방향들이 서로 맞는지 아무도 보증하지 않고,
    #    종목을 지목하면 다음 화면의 루틴과 어긋난다 ("아까는 덤벨 컬이라더니").
    #    방향은 종합 진단(silhouette)이 전체를 보고 한 번만 말한다.
    check("부위 카드에 운동 방향 금지", "부위 카드에는 운동 방향을 쓰지 않습니다" in part_system)
    check("종목·계열 둘 다 금지", "동작 계열·방향도 쓰지 않습니다" in part_system)
    #: 상황별 문형 세 개 — 판단 불가가 ①② 와 다르게 읽혀야 한다 (사용자 요청).
    # ⚠️ 127f9e1(문형 골격 제거)에서 ①②는 "고정 문장 → 담을 내용" 으로 바뀌었다.
    #    예시 문장은 few-shot 자석을 막으려고 일부러 매번 다르게 쓰라고 되어 있어서
    #    (모듈 docstring 참고), 그 예시 문장 자체를 검사 문자열로 쓰면 다음 톤
    #    조정 때마다 또 빨개진다. 그래서 ①②는 **바뀌지 않는 구조 표지**(섹션
    #    제목)로 검사하고, ③만 원래부터 **문장 그대로 고정**이 규칙이라
    #    (🔴 "반드시 이 두 표현을 그대로 쓰세요") 그 리터럴로 검사한다.
    for label, needle in [
        ("차이 있음 문형", "① 차이가 있을 때"),
        ("차이 작음 문형", "② 차이가 작을 때"),
        ("판단 불가 문형 (표현 1)", "충분히 보이지 않아서"),
        ("판단 불가 문형 (표현 2)", "비교하기는"),
    ]:
        check(label, needle in part_system)

    # 옷 흡수 표가 '가림' 선언의 관문이다 — 실측(2026-08-15): 왼쪽 전완만 옷
    # 65% 인데 모델이 "전완은 가려짐"으로 일반화해 맨살인 오른쪽(0%)까지
    # blocked 로 보냈다. 표에 없는 부위는 가림 선언을 못 하게 명시한다.
    ref_seg = {"Left_Lower_Arm": _segment("Left_Lower_Arm", 5000, 60, 300, 50, 150)}
    user_seg = {
        "Left_Lower_Arm": dict(
            _segment("Left_Lower_Arm", 5000, 60, 300, 50, 150), clothing_pixel_count=3250
        )
    }
    clothed_metrics = segmap.compare_parts(ref_seg, user_seg, ["Left_Lower_Arm"], PERSON)
    clothed_prompt = build_part_prompt(
        parts=[
            {
                "class_name": "Left_Lower_Arm",
                "name_ko": "왼팔 전완",
                "color_hex": "#123456",
                "inbody_segment": "LEFT_ARM",
            }
        ],
        metrics=clothed_metrics,
        ref_symmetry={},
        user_symmetry={},
        inbody=None,
    )
    check(
        "옷 흡수 부위가 표에 실림 (65%)",
        "흡수된 픽셀이 포함된 부위" in clothed_prompt and "65%" in clothed_prompt,
    )
    check(
        "옷 흡수 표 밖 부위의 '가림' 선언 금지",
        "이 표에 없는 부위를 '옷에 가려 판단 불가'라고 하지 마세요" in clothed_prompt,
    )
    bare_prompt = build_part_prompt(
        parts=[
            {
                "class_name": "Left_Lower_Arm",
                "name_ko": "왼팔 전완",
                "color_hex": "#123456",
                "inbody_segment": "LEFT_ARM",
            }
        ],
        metrics=segmap.compare_parts(ref_seg, ref_seg, ["Left_Lower_Arm"], PERSON),
        ref_symmetry={},
        user_symmetry={},
        inbody=None,
    )
    check(
        "옷 흡수 0 이면 가림 선언 전면 금지 안내",
        "옷 흡수가 감지된 부위가 없습니다" in bare_prompt,
    )
    excluded_prompt = build_overall_prompt(
        parts=[],
        blocked=[],
        failed=[],
        inbody=None,
        excluded=["Left_Upper_Leg(왼쪽 허벅지)", "Left_Lower_Leg(왼쪽 종아리)"],
    )
    # ⚠️ ff6175d(2026-08-20)에서 **계약이 뒤집혔다.** 종전에는 "빠진 부위를
    #    cautions 에 한 문장으로 묶어 쓰라"였는데, 지금은 **cautions 에 아예
    #    쓰지 말라**로 바뀌었다 — 어느 부위가 왜 빠졌는지는 화면이 excluded
    #    목록에서 직접 뽑아 보여주므로, cautions 에 또 쓰면 중복으로 뜬다.
    #    (그래서 «전부 묶어 한 문장»·«괄호 안의 한글 이름» 지시는 사라졌다)
    check("빠진 부위를 cautions 에 쓰지 말라고 지시", "cautions 에 이 부위들을 언급하지 마세요" in excluded_prompt)
    check("중복으로 뜨는 이유를 함께 설명", "중복으로 뜹니다" in excluded_prompt)
    check("빠진 부위 목록 자체는 여전히 전달됨", "왼쪽 허벅지" in excluded_prompt)


def main() -> int:
    print("F08/F09 진단 파이프라인 검증")
    test_handlers()
    test_scale_invariance()
    test_overlay()
    test_partial_acceptance()
    test_blocked_without_inbody()
    test_prompts()
    test_citation_routing()
    test_retry_policy()
    test_baseline_separation()

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
