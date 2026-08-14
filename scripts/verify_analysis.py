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
            # ⚠️ LLM 이 점수를 보내와도 **버려야 한다.** 유사도는 규칙이 계산한다
            #    (scoring.compute_similarity, score_source=RULE). 여기서 통과시키면
            #    "AI 가 뱉은 숫자"가 화면에 그대로 나가고 재현성이 깨진다.
            "similarity_score": 140,
            "summary": "요약",
            "priority_parts": ["Torso", "Nonexistent_Part"],
            "strengths": "하체가 좋습니다",  # 문자열 → 배열
            "cautions": [],
        },
        set(names),
    )
    check("LLM 이 보낸 점수는 버림 (규칙이 계산)", "similarity_score" not in overall)
    check("없는 부위는 우선순위에서 제거", overall["priority_parts"] == ["Torso"])
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
    check("수치 표 포함", "면적몫" in prompt)
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
    print("\n7. 인바디 인용 라우팅 (세그먼트당 대표 1부위)")

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
    targets = _citation_targets(part_to_segment, metrics)
    check("격차 큰 부위가 대표", targets.get("LEFT_ARM") == "Left_Upper_Arm", str(targets))
    check("세그먼트당 1개", len(targets) == 2)

    # 프롬프트에 [인용] 이 대표 부위 줄에만 붙는지
    parts = [
        {"class_name": n, "name_ko": n, "color_hex": "#111111", "inbody_segment": s}
        for n, s in part_to_segment.items()
    ]
    inbody = {
        "body": {"weight": 63.5},
        "segments": {
            "LEFT_ARM": {"lean_mass": 2.42, "lean_percentage": 82.1},
            "TRUNK": {"lean_mass": 22.8, "lean_percentage": 94.5},
        },
    }
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


def main() -> int:
    print("F08/F09 진단 파이프라인 검증")
    test_handlers()
    test_scale_invariance()
    test_overlay()
    test_partial_acceptance()
    test_prompts()
    test_citation_routing()
    test_retry_policy()

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
