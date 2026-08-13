"""F08 — 부위별 비교 진단 프롬프트 (전 부위 1회 호출).

━━ 왜 부위마다 부르지 않는가 ━━

입력이 크롭이 아니라 **원본 + 오버레이**로 확정된 순간(llm-strategy.md §F08),
부위별 호출은 같은 원본 사진을 부위 수만큼 반복 업로드하는 구조가 된다.
9부위면 레퍼런스 원본 9장 + 사용자 원본 9장 = 18장인데 실제 정보량은 2장이다.

그리고 더 중요한 문제: **비교는 부위 하나만 봐서는 불가능하다.**
"어깨가 좁다"는 골반 대비·전신 비율 대비 판단이다. 부위별로 격리해서 부르면
9개 호출이 서로의 판단을 모르므로 "이 부위가 1순위"라는 답이 9번 나와도
막을 방법이 없다. 한 번에 보면 순위가 한 문맥 안에서 정해진다.

━━ 역할 분담 — 셀 수 있는 건 코드가, 볼 수밖에 없는 건 VLM 이 ━━

면적·너비·비율은 세그멘테이션 맵에 이미 정확히 들어 있다(segmap.compare_parts).
그걸 VLM 에게 눈대중으로 재게 하는 건 정확한 값을 부정확한 방법으로 다시 구하는
것이고, 틀려도 그럴듯해서 검증이 안 된다.

    코드 → 수치 (area_share, width_share, diff_pct, 좌우 대칭, 인바디 실측)
    VLM  → 수치로 표현되지 않는 것 (근육 라인, 실루엣, 자세, 시각적 인상)

프롬프트는 이 경계를 명시적으로 강제한다.
"""

from typing import Any

SYSTEM_PROMPT = """당신은 체형 비교 분석 전문가입니다.
레퍼런스(목표 체형) 사진과 사용자 사진을 부위별로 비교해 진단합니다.

# 입력

이미지 4장이 순서대로 주어집니다.
1. 레퍼런스 원본
2. 레퍼런스 부위 오버레이 — 비교 대상 부위를 색으로 칠한 것
3. 사용자 원본
4. 사용자 부위 오버레이 — 같은 색 규칙

색과 부위의 대응은 아래 "부위 범례"에 있습니다. 범례에 없는 색은 무시하세요.
어둡게 처리된 영역은 비교 대상이 아닙니다.

# 수치는 이미 계산되어 있습니다

부위별 면적·너비 비율과 레퍼런스 대비 차이(%)를 함께 드립니다.
이 값은 세그멘테이션 마스크에서 정확히 계산된 것이며, 촬영 거리와 해상도가
달라도 비교되도록 인물 크기 기준으로 정규화돼 있습니다.

⚠️ 크기·면적·비율을 이미지에서 눈으로 다시 재지 마세요. 주어진 수치를 쓰세요.
⚠️ 주어진 수치와 모순되는 서술을 하지 마세요.
   (예: diff_pct.area_share 가 +8 인데 "레퍼런스보다 얇다"라고 쓰지 않기)

당신이 판단할 것은 **수치로 드러나지 않는 것**입니다.
근육의 윤곽과 선명도, 실루엣의 형태, 자세와 정렬, 좌우 균형의 시각적 인상.

# 판단할 수 없으면 판단하지 마세요

옷에 가려졌거나, 각도 때문에 안 보이거나, 화질이 나빠 확신할 수 없으면
그 부위는 gap_level 을 null, confidence 를 "LOW" 로 두고 blocked_reason 에
이유를 쓰세요. **추측해서 채우지 마세요.**

판단 불가는 실패가 아닙니다. 그 부위는 진단에서 빠질 뿐 운동 루틴은
기본 볼륨으로 정상 생성됩니다. 틀린 진단이 빠진 진단보다 나쁩니다.

# 출력 형식

JSON 하나만 반환합니다. 부위 범례에 있는 **모든 부위**에 대해 항목을 만드세요.
하나도 빠뜨리지 마세요 — 빠진 부위는 실패로 기록됩니다.

{
  "parts": [
    {
      "class_name": "Left_Upper_Arm",
      "differences": ["상완 둘레가 얇음", "삼두 라인이 흐림"],
      "assessment": "레퍼런스 대비 상완 볼륨이 부족하고 근육 경계가 뚜렷하지 않습니다.",
      "gap_level": "MODERATE",
      "priority": 2,
      "confidence": "HIGH",
      "blocked_reason": null
    }
  ]
}

## 필드 규칙

- class_name    : 범례에 있는 이름 그대로. 임의로 바꾸지 마세요.
- differences   : 짧은 한국어 문장의 **배열**. 1~3개. 한 문자열로 이어붙이지 마세요.
- assessment    : 한국어 2문장 이내. 사용자에게 그대로 보여집니다.
- gap_level     : NONE | SLIGHT | MODERATE | SIGNIFICANT (판단 불가면 null)
                  레퍼런스와의 격차입니다. NONE 은 이미 근접했다는 뜻입니다.
- priority      : 1~5 정수. 1이 가장 시급. 격차가 크고 개선 여지가 큰 부위가 1입니다.
                  ⚠️ 모든 부위에 1을 주지 마세요. 부위 간 상대 순위입니다.
- confidence    : LOW | MEDIUM | HIGH — 이 진단을 얼마나 확신하는지
- blocked_reason: 판단 불가 사유 (가능하면 null)

⚠️ gap_level / confidence 는 **반드시 대문자**입니다. 소문자는 저장에 실패합니다.
⚠️ 사용자를 평가하거나 비하하지 마세요. 개선 관점으로 서술하세요.
⚠️ 의학적 진단·질병 언급 금지. 체형과 운동 관점으로만 서술하세요."""


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def _legend_block(parts: list[dict[str, Any]]) -> str:
    lines = [
        f"- {p['class_name']} ({p.get('name_ko') or p['class_name']}) "
        f"= {p.get('color_hex') or '?'}"
        for p in parts
    ]
    return "\n".join(lines)


def _metrics_block(metrics: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    """부위별 정규화 수치 표. 사람이 읽어도 이해되는 형태로 준다."""
    rows = metrics.get("parts") or {}
    if not rows:
        return "(세그멘테이션 수치를 계산하지 못했습니다. 이미지만으로 판단하세요.)"

    out = [
        "부위 | 레퍼런스 면적몫 | 사용자 면적몫 | 면적 차이 | 너비 차이 | 높이 차이",
        "---|---|---|---|---|---",
    ]
    for p in parts:
        name = p["class_name"]
        m = rows.get(name)
        if not m:
            continue
        d = m["diff_pct"]
        out.append(
            f"{name} | {m['reference']['area_share']:.4f} | {m['user']['area_share']:.4f} | "
            f"{_fmt_pct(d['area_share'])} | {_fmt_pct(d['width_share'])} | "
            f"{_fmt_pct(d['height_share'])}"
        )

    out.append("")
    out.append(
        "※ 면적몫 = 그 부위 픽셀 / 인물 전체 픽셀. 촬영 거리와 무관합니다.\n"
        "※ 차이(%)는 레퍼런스 대비 사용자입니다. +면 사용자가 더 큽니다."
    )

    truncated = [n for n, m in rows.items() if m.get("user_truncated")]
    if truncated:
        out.append(
            f"※ 사용자 사진에서 프레임에 잘린 부위: {', '.join(truncated)} "
            "— 면적·높이 수치가 실제보다 작게 나옵니다. 이 부위는 수치를 신뢰하지 말고 "
            "confidence 를 낮추세요."
        )
    return "\n".join(out)


def _symmetry_block(ref_sym: dict[str, float], user_sym: dict[str, float]) -> str:
    if not user_sym:
        return ""
    lines = ["", "## 좌우 대칭 (같은 사람 안에서의 면적 차이)"]
    for key, value in user_sym.items():
        ref_value = ref_sym.get(key)
        base = f" (레퍼런스 {ref_value:.1f}%)" if ref_value is not None else ""
        lines.append(f"- {key}: 사용자 {value:.1f}%{base}")
    lines.append(
        "※ 자세와 각도만으로도 10~20%는 흔합니다. 참고값으로만 쓰고 "
        "이것만으로 비대칭이라 단정하지 마세요."
    )
    return "\n".join(lines)


def _inbody_block(inbody: dict[str, Any] | None, part_to_segment: dict[str, str]) -> str:
    """인바디 실측을 부위에 붙여 준다.

    ⚠️ 인바디는 선택 입력이다. 없으면 이 블록만 빠지고 진단은 그대로 진행된다
       (llm-strategy.md §F08: 인바디는 선행 조건이 아님).
    """
    if not inbody:
        return "\n## 인바디\n\n" "제출된 인바디 결과가 없습니다. 시각 정보만으로 판단하세요."

    lines = ["", "## 인바디 실측 (부위별 제지방량)", ""]

    body = inbody.get("body") or {}
    summary = [
        f"체중 {body['weight']}kg" if body.get("weight") else None,
        f"골격근량 {body['skeletal_muscle_mass']}kg" if body.get("skeletal_muscle_mass") else None,
        f"체지방률 {body['body_fat_percentage']}%" if body.get("body_fat_percentage") else None,
    ]
    summary = [s for s in summary if s]
    if summary:
        lines.append("전신: " + " · ".join(summary))
        lines.append("")

    segments = inbody.get("segments") or {}
    for class_name, segment in part_to_segment.items():
        s = segments.get(segment)
        if not s:
            continue
        parts_desc = [f"제지방 {s['lean_mass']}kg"] if s.get("lean_mass") is not None else []
        if s.get("lean_percentage") is not None:
            parts_desc.append(f"표준 대비 {s['lean_percentage']}%")
        if s.get("fat_mass") is not None:
            parts_desc.append(f"체지방 {s['fat_mass']}kg")
        if parts_desc:
            lines.append(f"- {class_name} ← {segment}: {' · '.join(parts_desc)}")

    lines.append("")
    lines.append(
        "※ 표준 대비 100% 미만이면 그 부위 근육량이 부족하다는 뜻입니다.\n"
        "※ 이 수치는 옷에 가려도 유효합니다. 시각 정보가 불확실할 때 근거로 쓰세요.\n"
        "※ 인바디는 좌우/몸통 단위라 세부 부위(예: 상완과 전완)를 구분하지 못합니다. "
        "같은 세그먼트를 공유하는 부위에 같은 수치가 붙는 점을 감안하세요."
    )
    return "\n".join(lines)


def build_part_prompt(
    parts: list[dict[str, Any]],
    metrics: dict[str, Any],
    ref_symmetry: dict[str, float],
    user_symmetry: dict[str, float],
    inbody: dict[str, Any] | None,
) -> str:
    """F08 사용자 메시지(텍스트 파트)를 만든다. 이미지는 호출부가 붙인다.

    Args:
        parts:    비교 대상 부위. body_part 마스터 행 그대로
                  (class_name / name_ko / color_hex / inbody_segment).
                  ⚠️ 부위 목록을 코드에 하드코딩하지 않는다 — DB 가 유일한 출처다.
        metrics:  segmap.compare_parts() 결과
        inbody:   {"body": {...}, "segments": {...}} 또는 None
    """
    part_to_segment = {
        p["class_name"]: p["inbody_segment"] for p in parts if p.get("inbody_segment")
    }

    return f"""# 부위 범례 (색 → 부위)

{_legend_block(parts)}

이 {len(parts)}개 부위 전부에 대해 진단 항목을 만드세요.

# 세그멘테이션 수치

{_metrics_block(metrics, parts)}
{_symmetry_block(ref_symmetry, user_symmetry)}
{_inbody_block(inbody, part_to_segment)}

# 요청

위 4장의 이미지와 수치를 종합해 부위별 비교 진단을 JSON 으로 반환하세요."""
