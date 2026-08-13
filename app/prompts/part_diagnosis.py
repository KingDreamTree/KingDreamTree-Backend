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

# 근거 위계 — 부위마다 가장 강한 신호 하나로 문장을 시작하세요

부위마다 쓸 수 있는 근거는 세 종류입니다.

1. **면적·너비 수치** — 모든 부위에 있음
2. **인바디 실측** — 세그먼트 단위, 제출됐을 때만. 체내 실측이라 옷·조명에 안 흔들림
3. **이미지 관찰** — 형태가 보일 때만

부위마다 이 중 **가장 강한 신호 하나를 골라 그것으로 문장을 시작**하고,
나머지는 보조로만 씁니다.

- 인바디 표에 `[인용]` 표시가 붙은 부위 → 인바디 수치로 시작
- 그 외 부위 → 면적 격차와 이미지 관찰 중 더 뚜렷한 쪽으로 시작
- 시각 정보와 인바디가 어긋나면 인바디를 우선 (실측이 사진을 이깁니다)
- 표준 대비 100% 미만이면 근육 부족, 130% 초과면 지방 과다 신호입니다.

⚠️ 인바디는 좌우 팔·다리와 몸통 5단위라 **상완과 전완을 구분하지 못합니다.**
   같은 세그먼트를 공유하는 부위들은 값이 똑같으므로, `[인용]` 부위에서 한 번만
   수치를 쓰고 **나머지 부위에서 같은 숫자를 반복하지 마세요.** 그 안에서 어느
   쪽이 더 부족한지는 면적 수치와 이미지로 나눕니다.

⚠️ 아홉 문장이 같은 서두로 시작하면 안 됩니다. 근거가 다르면 서두도 달라집니다.

# 옷에 가려도 인바디가 있으면 진단하세요

이 서비스는 사용자가 옷을 입고 촬영하는 것을 전제합니다.
시각으로 형태를 못 읽는다고 해서 바로 포기하지 마세요.

| 상황 | 처리 |
|---|---|
| 시각 판단 가능 | 이미지 + 수치 + 인바디 종합. confidence 는 HIGH/MEDIUM |
| 시각 판단 불가 + **인바디 있음** | **인바디를 근거로 gap_level 을 매기세요.** confidence 는 MEDIUM, blocked_reason 에 "시각 확인 불가, 인바디 기준 판단" 이라고 남깁니다 |
| 시각 판단 불가 + 인바디 없음 | gap_level 을 null, confidence 를 LOW, blocked_reason 에 이유. **추측해서 채우지 마세요** |

마지막 경우만 판단 불가입니다. 그건 실패가 아니라 그 부위가 진단에서 빠지는
것이고, 운동 루틴은 기본 볼륨으로 정상 생성됩니다.
틀린 진단이 빠진 진단보다 나쁩니다.

# 출력 형식

최상위에 **`parts` 키 하나만** 있는 JSON 객체를 반환합니다.
부위 범례에 있는 **모든 부위**의 항목이 이 배열에 들어가야 합니다.
하나도 빠뜨리지 마세요 — 빠진 부위는 실패로 기록됩니다.

{
  "parts": [
    { "class_name": "...", "differences": [...], "assessment": "...",
      "gap_level": "...", "priority": 1, "confidence": "...", "blocked_reason": null },
    ... 부위 수만큼 ...
  ]
}

⚠️ 부위명을 최상위 키로 쓰지 마세요 (`{"Torso": {...}}` ❌).
   아래 예시들은 배열 **원소 하나**의 모양을 보여주는 것입니다.

## 필드 규칙

- class_name    : 범례에 있는 이름 그대로. 임의로 바꾸지 마세요.
- differences   : **이미지에서 눈으로 본 것만** 적습니다. 0~2개의 짧은 배열.
- assessment    : 한국어 1~2문장. 사용자에게 그대로 보여집니다. (아래 §문체 참고)
- gap_level     : NONE | SLIGHT | MODERATE | SIGNIFICANT (판단 불가면 null)
                  레퍼런스와의 격차입니다. NONE 은 이미 근접했다는 뜻입니다.
- priority      : 1~5 정수. 1이 가장 시급.
- confidence    : LOW | MEDIUM | HIGH
- blocked_reason: 시각 확인이 안 된 사유 (문제없으면 null)

### differences — 수치를 말로 바꾸지 마세요

면적·너비·둘레는 **이미 표에 있습니다.** "둘레가 얇음", "면적이 작음" 같은 문장은
표를 읽으면 알 수 있는 내용이라 아무것도 더하지 않습니다.

여기에는 **이미지를 봐야만 알 수 있는 것**만 쓰세요:
근육의 갈라짐과 경계선, 실루엣의 처짐, 피부 아래 윤곽의 선명도, 자세 정렬, 좌우 비틀림.

⚠️ 이미지에서 확인 못 했으면 **빈 배열 `[]`** 로 두세요.
   수치가 작으니 흐릿하겠거니 하고 **추론해서 쓰지 마세요.** 그건 관찰이 아닙니다.

### priority — 서로 다른 값을 쓰세요

부위 간 **상대 순위**입니다. 같은 값이 세 개 이상 몰리면 순위 정보가 사라집니다.
격차가 큰 순서대로 1, 2, 3… 을 배분하고, 개선이 불필요한 부위에만 5를 겹쳐 쓰세요.

### gap_level 이 NONE 이면 개선 제안을 하지 마세요

이미 목표에 도달한 부위입니다. "조금 더 늘리면 좋습니다" 같은 말을 붙이면
사용자는 모든 부위가 부족하다고 읽습니다. **유지하라고만** 하세요.

## 문체 — assessment 는 설명이 아니라 처방입니다

`[근거] → [무엇을 위해] → [무엇을 하라]` 순서로 한 문장씩 씁니다.
부위명은 «좌측/우측»으로 명시하고, 가능하면 **구체적 근육명**을 씁니다
(상완이두근·삼두근·광배근·대퇴사두근·비복근 등).
확실하지 않은 근육명은 지어내지 말고 부위명을 그대로 쓰세요.

서두와 종결을 다양하게 쓰세요 — 인바디 인용 / 면적 비교 / 이미지 관찰 / 유지
안내는 각각 다른 문장 틀입니다.
⚠️ 단, 다양성을 위해 **없는 사실을 만들지 마세요.** 근거가 정말 같은 부위들
(예: 다리 네 부위가 전부 충분)은 표현만 달리하고 내용은 짧게 유지합니다.

### 예시 — 근거 유형별로 서두가 다릅니다

⚠️ **아래는 전부 가상의 다른 사용자입니다.** 지금 진단하는 사진·수치와 무관합니다.
   문장의 틀(근거→목적→처방)만 따르고 **내용을 복사하지 마세요.**
   특히 "옷에 가렸다", "라인이 흐릿하다" 같은 관찰은 예시 사용자의 것입니다 —
   지금 이미지에서 직접 확인한 경우에만 쓰세요.

인바디 `[인용]` 부위:
{
  "class_name": "(범례의 부위명)",
  "differences": ["(이 이미지에서 직접 본 것. 없으면 빈 배열)"],
  "assessment": "좌측 팔 전체 제지방량이 표준의 82%로 부족합니다. 균형 잡힌 상체 라인을 완성하기 위해 상완이두근 집중 강화 운동이 필요합니다.",
  "gap_level": "SIGNIFICANT", "priority": 1, "confidence": "HIGH", "blocked_reason": null
}

같은 세그먼트의 나머지 부위 (수치 반복 금지 — 면적·이미지로 서술):
{
  "assessment": "좌측 전완은 레퍼런스 대비 둘레가 소폭 얇은 정도입니다. 팔 라인의 균형을 위해 전완 굴곡근을 보조 운동으로 더해주세요.",
  "gap_level": "SLIGHT", "priority": 4, ...
}

목표 도달 부위 (인용 강제 없음, 유지만):
{
  "assessment": "우측 허벅지는 이미 레퍼런스 수준을 넘어서 있습니다. 현재 운동량을 유지하세요.",
  "gap_level": "NONE", "priority": 5, ...
}

옷에 가린 부위 — **지금 이미지에서 실제로 가려진 경우에만** (인바디가 유일한 근거이므로 반드시 인용):
{
  "assessment": "상의에 가려 근육 윤곽은 확인하지 못했으나, 몸통 전체 제지방량이 표준의 94%로 소폭 부족합니다. 코어 안정성 확보를 위해 복부·기립근 보강이 필요합니다.",
  "gap_level": "SLIGHT", "priority": 3, "confidence": "MEDIUM",
  "blocked_reason": "시각 확인 불가, 인바디 기준 판단"
}

⚠️ gap_level / confidence 는 **반드시 대문자**입니다. 소문자는 저장에 실패합니다.
⚠️ 사용자를 평가하거나 비하하지 마세요. 개선 관점으로 서술하세요.
⚠️ 의학적 진단·질병 언급 금지. 체형과 운동 관점으로만 서술하세요."""


#: 인바디 세그먼트의 한글 이름. **"전체"를 명시하는 게 핵심이다** —
#: 인바디는 팔 하나를 통째로 재므로 상완·전완을 나눌 수 없는데, 이름이 영문
#: 코드로만 있으면 LLM이 그 값을 세부 부위의 값처럼 인용한다.
_SEGMENT_KO = {
    "LEFT_ARM": "좌측 팔 전체",
    "RIGHT_ARM": "우측 팔 전체",
    "TRUNK": "몸통 전체",
    "LEFT_LEG": "좌측 다리 전체",
    "RIGHT_LEG": "우측 다리 전체",
}


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


def _citation_targets(
    part_to_segment: dict[str, str],
    metrics: dict[str, Any],
) -> dict[str, str]:
    """세그먼트마다 인바디를 인용할 대표 부위 하나를 고른다 — 면적 격차가 가장 큰 부위.

    ⚠️ **이 선택은 코드가 한다.** LLM에게 "세그먼트당 한 번만 인용하라"고 지시하면
       9개 항목을 스스로 대조해야 하는 전역 제약이 되는데, 항목을 하나씩 생성하는
       모델은 그런 제약을 자주 어긴다. 라우팅을 결정론적으로 만들면 어길 방법이
       없고, 어느 부위가 인용했는지 테스트로 검증할 수 있다.

    Returns:
        {segment: 대표 class_name}
    """
    rows = metrics.get("parts") or {}
    best: dict[str, tuple[str, float]] = {}
    for part, segment in part_to_segment.items():
        diff = ((rows.get(part) or {}).get("diff_pct") or {}).get("area_share")
        magnitude = abs(diff) if diff is not None else -1.0
        if segment not in best or magnitude > best[segment][1]:
            best[segment] = (part, magnitude)
    return {segment: part for segment, (part, _) in best.items()}


def _inbody_block(
    inbody: dict[str, Any] | None,
    part_to_segment: dict[str, str],
    citation_targets: dict[str, str],
) -> str:
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
            # ⚠️ 한글 이름을 함께 준다. 이게 없으면 LEFT_ARM(팔 전체) 값을
            #    "전완 제지방량"처럼 세부 부위의 값인 양 인용한다 (실측 확인).
            mark = " **[인용]**" if citation_targets.get(segment) == class_name else ""
            lines.append(
                f"- {class_name} ← {_SEGMENT_KO.get(segment, segment)}"
                f"({segment}): {' · '.join(parts_desc)}{mark}"
            )

    lines.append("")
    lines.append(
        "🔴 `[인용]` 이 붙은 부위만 assessment 에 수치를 인용합니다. 같은 세그먼트의\n"
        "   나머지 부위는 **같은 숫자를 반복하지 말고** 면적·이미지 근거로 서술하세요.\n"
        "   인용할 때는 화살표 오른쪽의 한글 이름 그대로 부릅니다.\n"
        '   O "좌측 팔 전체 제지방량이 표준의 82%"\n'
        '   X "좌측 전완 제지방량이 표준의 82%"  ← 전완만 잰 값이 아닙니다\n'
        "※ 옷에 가려 시각 판단이 불가한 부위는 예외 — `[인용]` 여부와 무관하게\n"
        "   인바디가 유일한 근거이므로 수치를 인용합니다.\n"
        "※ 표준 대비 100% 미만이면 근육 부족, 130% 초과면 지방 과다 신호입니다.\n"
        "※ 이 값은 체내 실측이라 시각 정보와 어긋나면 이쪽을 우선하세요."
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
    citation_targets = _citation_targets(part_to_segment, metrics)

    return f"""# 부위 범례 (색 → 부위)

{_legend_block(parts)}

이 {len(parts)}개 부위 전부에 대해 진단 항목을 만드세요.

# 세그멘테이션 수치

{_metrics_block(metrics, parts)}
{_symmetry_block(ref_symmetry, user_symmetry)}
{_inbody_block(inbody, part_to_segment, citation_targets)}

# 요청

위 4장의 이미지와 수치를 종합해 부위별 비교 진단을 JSON 으로 반환하세요."""
