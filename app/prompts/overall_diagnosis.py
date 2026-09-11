"""F09 — 종합 진단 프롬프트. 원본 사진 두 장 + 부위별 진단 결과를 받는다.

━━ 왜 이미지를 받게 됐나 (2026-08-17 변경) ━━

부위 9개를 각각 정확히 봐도 **부위 사이의 관계**는 어느 카드에도 안 나온다.
"상체는 목표에 가까운데 하체가 얇아 위아래가 안 맞는다", "어깨 폭 대비 허리가
넓다" 같은 판단은 부위별 등급을 아무리 합쳐도 나오지 않는다 — 합계에는
비율이 없기 때문이다. 그래서 종합은 원본 두 장을 직접 본다.

━━ 그래도 F08 과 한 호출로 합치지 않는 이유 ━━

  * 보는 것이 다르다. F08 은 부위 하나하나의 윤곽을, F09 는 전체 형태를 본다.
    한 호출에 섞으면 모델이 둘 중 하나를 대충 한다 (긴 출력의 뒤쪽이 성기다)
  * F08 이 성공했는데 F09 만 실패해도 부위별 진단은 남는다
  * 종합 문구만 튜닝할 때 부위 진단을 다시 돌리지 않아도 된다

llm-strategy.md §F09 의 "Prompt Chaining" 이 이것이다.

⚠️ 이미지 순서는 vlm.diagnose_overall 이 넣는 순서와 반드시 같아야 한다.
   어긋나면 레퍼런스와 사용자가 뒤바뀌어 종합이 정반대로 나온다.
"""

from typing import Any

# ⚠️ 시스템 프롬프트 본문은 DB(prompt_version 'overall.system')에 있다 (2026-09-11, #164) —
#    app/services/prompt_store.py, vlm.OVERALL_PROMPT. 여기는 사용자 메시지 조립만 한다.


def _part_line(p: dict[str, Any]) -> str:
    # ⚠️ blocked_reason 이 있어도 gap_level 이 있으면 판단 불가가 아니다.
    #    옷에 가렸지만 인바디로 등급을 매긴 경우가 이에 해당한다. 여기서 뭉뚱그리면
    #    인바디로 건진 부위가 다시 "모름"으로 떨어져 점수에서 빠진다.
    if not p.get("gap_level"):
        reason = p.get("blocked_reason") or "사유 미기재"
        return f"- {p['class_name']}: 판단 불가 — {reason}"

    bits = [f"격차 {p['gap_level']}"]
    if p.get("priority") is not None:
        bits.append(f"우선순위 {p['priority']}")
    if p.get("confidence"):
        bits.append(f"확신도 {p['confidence']}")
    if p.get("blocked_reason"):
        # 시각은 막혔지만 인바디로 등급이 나온 부위 — 근거가 다르다는 걸 알려준다.
        bits.append("시각 확인 불가 · 인바디 기준")

    line = f"- {p['class_name']} ({' · '.join(bits)})"
    if p.get("assessment"):
        line += f"\n    {p['assessment']}"
    for d in p.get("differences") or []:
        line += f"\n    · {d}"
    return line


def _inbody_block(inbody: dict[str, Any] | None) -> str:
    if not inbody:
        return "인바디 결과 없음 — 시각 진단만으로 종합하세요."

    body = inbody.get("body") or {}
    bits = []
    for label, key, unit in (
        ("체중", "weight", "kg"),
        ("골격근량", "skeletal_muscle_mass", "kg"),
        ("체지방률", "body_fat_percentage", "%"),
        ("BMI", "bmi", ""),
    ):
        if body.get(key) is not None:
            bits.append(f"{label} {body[key]}{unit}")

    lines = [" · ".join(bits)] if bits else []
    has_pct = False
    for segment, s in (inbody.get("segments") or {}).items():
        if s.get("lean_mass") is not None:
            extra = (
                f" (표준 대비 {s['lean_percentage']}%)"
                if s.get("lean_percentage") is not None
                else ""
            )
            has_pct = has_pct or s.get("lean_percentage") is not None
            lines.append(f"- {segment}: 제지방 {s['lean_mass']}kg{extra}")

    if has_pct:
        lines.append(
            "※ 표준 대비 %는 **일반인 평균과의 비교**입니다. "
            "목표 체형과의 거리처럼 인용하지 마세요."
        )
    return "\n".join(lines) if lines else "인바디 수치 없음"


#: 부위 비중 표의 그룹 전체. ⚠️ handlers/vlm.py `_SHARE_GROUPS` 의 키와 같아야 한다 —
#: 표에 빠진 그룹을 찾아 "언급 금지"를 걸기 위한 기준 목록이다.
_SHARE_GROUP_LABELS = ("몸통", "팔", "하체")

#: 격차 등급 서열 (동률 판정용). scoring._GAP_ORDINAL 과 같은 값 —
#: import 하면 prompts→services 역방향 의존이 생겨 여기 복사해 둔다.
_GAP_ORDINAL = {"NONE": 0, "SLIGHT": 1, "MODERATE": 2, "SIGNIFICANT": 3}


def _tie_note(parts: list[dict[str, Any]], priority_parts: list[str]) -> list[str]:
    """우선순위 1순위와 같은 등급(동률)인 부위가 더 있으면 그 사실을 명시한다.

    scoring.rank_priority 는 동률을 이름순으로 줄 세울 뿐인데, 목록만 주면
    모델이 [0]번 부위를 "차이가 가장 크다"로 서술한다 — 실측: 팔·몸통이 전부
    MODERATE 동률인 세션에서 "팔의 차이가 가장 큽니다"가 나갔고, 사용자가
    두 부위 다 뚜렷이 다른 사진에서 부자연스럽다고 지적했다. FE 헤드라인이
    동률을 함께 표시하도록 고친 것(kingdreamtree-fe PR #183)과 같은 문제의
    산문 버전이라, 같은 해법(코드가 동률을 감지해 알려줌)을 쓴다.
    """
    by_name = {p.get("class_name"): p for p in parts}
    top = by_name.get(priority_parts[0]) if priority_parts else None
    if not top or top.get("gap_level") not in _GAP_ORDINAL:
        return []
    top_gap = top["gap_level"]
    tied = [
        name for name in priority_parts if (by_name.get(name) or {}).get("gap_level") == top_gap
    ]
    if len(tied) < 2:
        return []
    return [
        f"⚠️ 격차 최상위 동률: {', '.join(tied)} — 전부 같은 등급({top_gap})입니다.",
        "   목록의 순서는 이름순일 뿐이니, 한 부위만 «가장 크다»고 쓰지 말고",
        "   동률인 부위들을 함께 짚으세요.",
    ]


def build_overall_prompt(
    parts: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    failed: list[str],
    inbody: dict[str, Any] | None,
    score: int | None = None,
    excluded: list[str] | None = None,
    has_images: bool = True,
    priority_parts: list[str] | None = None,
    direction: dict[str, Any] | None = None,
    cut_notice: str | None = None,
    shares: dict[str, Any] | None = None,
) -> str:
    """F09 사용자 메시지.

    ⚠️ 이미지는 이 함수가 만들지 않는다. vlm.diagnose_overall 이 이 텍스트 **앞에**
       레퍼런스 원본 → 사용자 원본 순서로 붙인다. 아래 §사진 안내가 그 순서를
       설명하므로, 순서를 바꾸려면 두 곳을 같이 고쳐야 한다.

    Args:
        parts:   판단에 성공한 부위 진단 (gap_level 이 있는 것)
        blocked: 판단 불가로 처리된 부위 (gap_level 이 null)
        failed:  응답 자체가 없거나 형식이 깨진 부위의 class_name
        excluded: **분석 자체가 안 된** 비교 대상 부위 (미검출·교집합 탈락).
                 이 목록이 없으면 LLM 이 "하체 균형이 좋다"처럼 본 적도 없는
                 부위를 언급한다 — 실연동에서 실제로 났던 사고다 (A 발견)
        score:   scoring.compute_similarity() 가 계산한 유사도 점수.
                 LLM 은 이 값을 **받아서 쓸 뿐** 만들지 않는다 (score_source=RULE).
        has_images: 사진 두 장이 함께 전송되는가. False 면 "사진 없음"을 명시해
                 모델이 보지 않은 형태를 지어내지 않게 한다.
        priority_parts: **규칙이 이미 정한** 개선 우선순위 (scoring.rank_priority).
                 LLM 은 이 순서를 바꾸지 않고 설명만 한다. 안 넘기면 그 절을 생략한다.
        direction: **규칙이 이미 정한** 개선 방향 (scoring.decide_direction).
                 mode / mode_reason / priority / reason 을 담는다. LLM 은 설명만 한다.
        cut_notice: CUT 모드일 때의 전략 설명 (routine_templates.CUT_NOTICE).
                 ⚠️ LLM 이 감량 전략을 새로 만들지 않게 **완성된 문장을 준다.**
        shares: 상체·하체 면적 비중 (segmap.compare_parts 집계).
                 ⚠️ 이게 없으면 모델이 실루엣의 **방향을 뒤집는다** — 실측
                    (2026-08-18): 몸통이 +13%, 하체가 −2% 인데 "하체 비중이 더
                    높게 나타납니다" 라고 썼다. 눈으로만 보면 어느 쪽이 더 큰지
                    헷갈리므로 숫자로 못 박는다.
    """
    sections = [
        *(
            [
                "# 사진",
                "",
                "위에 이미지 두 장이 순서대로 주어졌습니다.",
                "",
                "  1번째 — 목표 체형(레퍼런스) 원본",
                "  2번째 — 현재 체형(사용자) 원본",
                "",
                "두 장을 직접 비교해 silhouette · key_differences 를 쓰세요.",
                "부위별 등급은 아래 결과를 참고하되, 사진과 충돌하면 사진을 우선합니다.",
                "",
            ]
            if has_images
            # ⚠️ 사진이 못 붙은 경우. 있다고 말하면 모델이 본 적 없는 것을 지어낸다.
            else [
                "# 사진",
                "",
                "이번에는 사진이 제공되지 않았습니다. 아래 부위별 진단 결과만으로",
                "판단하세요. silhouette 과 key_differences 에는 사진에서 확인한 것처럼",
                "쓰지 말고, 확인할 수 없었다고 밝히거나 비워 두세요.",
                "confidence 는 0.3 을 넘기지 마세요.",
                "",
            ]
        ),
        *(
            [
                "# 개선 우선순위 (규칙이 이미 정했습니다)",
                "",
                "  " + " → ".join(priority_parts),
                "",
                "이 순서를 바꾸거나 다른 부위를 끼워 넣지 마세요. summary 에서 이 결과를",
                "설명하되, 어느 부위를 먼저 할지 **당신이 새로 고르지 않습니다.**",
                *_tie_note(parts, priority_parts),
                "",
            ]
            if priority_parts
            else []
        ),
        *(
            [
                "# 개선 방향 (규칙이 이미 정했습니다)",
                "",
                f"  판정      : {direction.get('priority')}",
                f"  근거      : {direction.get('reason')}",
                f"  운동 모드 : {direction.get('mode')}  (판정 근거: {direction.get('mode_basis')})",
                *(
                    [f"  모드 설명 : {direction['mode_reason']}"]
                    if direction.get("mode_reason")
                    else []
                ),
                *(
                    [
                        "",
                        "  감량 모드 전략 (이미 정해진 내용 — 새로 만들지 마세요):",
                        f"  {cut_notice}",
                    ]
                    if cut_notice
                    else []
                ),
                "",
                "🔴 이 판정을 바꾸지 마세요. direction_summary·strategy_focus 는",
                "   위 내용을 **사용자 말로 옮기는 것**이지 새로 정하는 게 아닙니다.",
                "⚠️ 체지방·감량 판단은 인바디에서 나왔습니다. 사진으로 판단하지 마세요.",
                # ⚠️ 실측(2026-08-20) — 이 블록의 근거 문자열("근력 강화를 우선…")이
                #    summary/silhouette 로 새어 "팔의 근력 차이가 가장 큽니다" 같은
                #    측정 판정 문장이 됐다. 근력은 사진으로도 인바디로도 못 잰다.
                #    단어의 용도를 블록 바로 옆에서 못 박는다.
                "⚠️ 위 근거의 «근력» 은 훈련 계획의 언어입니다. summary·silhouette 의",
                "   차이 서술에는 옮기지 마세요 — 사진이 보여주는 것은 형태(두께·볼륨·",
                "   윤곽)뿐이고, «근력 차이» 는 어떤 데이터로도 잰 적이 없습니다.",
                "",
            ]
            if direction
            else []
        ),
        *(
            [
                "# 두 사진의 부위 비중 (계산 완료 — 이 방향을 뒤집지 마세요)",
                "",
                "  같은 사람 안에서 «비교 대상 부위 전체» 대비 각 부위가 차지하는 면적 비율입니다.",
                "  촬영 거리·해상도가 상쇄되므로 두 사진을 그대로 견줄 수 있습니다.",
                "",
                *[
                    f"  {label:<10} 레퍼런스 {v['reference']:.3f} → 현재 {v['user']:.3f}"
                    f"  ({v['delta']:+.0f}%{' — 현재가 더 큼' if v['delta'] > 3 else (' — 현재가 더 작음' if v['delta'] < -3 else ' — 비슷함')})"
                    for label, v in shares.items()
                ],
                "",
                "🔴 silhouette·key_differences 는 **이 수치와 같은 방향**이어야 합니다.",
                "   숫자가 «현재가 더 큼» 이라고 말하는 부위를 «작다»고 쓰면 안 됩니다.",
                "",
                # ⚠️ 표에 없는 그룹 = 그쪽 부위가 비교 대상에 하나도 못 들었다는 뜻.
                #    실측(2026-08-18) — 다리 4부위가 전부 미검출(프레임 잘림)인 세션에서
                #    모델이 사진에 조금 보이는 잘린 다리를 근거로 "상체 대비 하체
                #    볼륨이 눈에 띄게 적습니다"를 summary·key_differences·프로필에
                #    걸쳐 썼다. 잘린 부위는 작아 «보이는» 것이지 작은 게 아니다.
                *(
                    [
                        f"🔴 위 표에 없는 그룹({', '.join(g for g in _SHARE_GROUP_LABELS if g not in shares)})은",
                        "   그쪽 부위가 비교 대상에 하나도 들지 못했다는 뜻입니다 (사진에서 잘렸거나",
                        "   검출되지 않음). 사진에 일부가 보여도 프레임에 잘린 모습이라 크기·비중을",
                        "   판단할 수 없습니다. silhouette · key_differences · summary · 프로필",
                        "   어디에서도 그 그룹의 크기·비중·볼륨을 언급하지 마세요.",
                        "",
                    ]
                    if any(g not in shares for g in _SHARE_GROUP_LABELS)
                    else []
                ),
            ]
            if shares
            else []
        ),
        "# 부위별 진단 결과",
        "",
        "\n".join(_part_line(p) for p in parts) if parts else "(판단된 부위 없음)",
    ]

    if score is not None:
        # ⚠️ 해석(구간 라벨)까지 코드가 정한다. 숫자만 주면 모델이 90점대를 보고도
        #    "개선이 필요"라고 쓰는 것을 실측했다 — 어조 기준선을 결정론적으로 준다.
        band = (
            "목표에 거의 근접한 상태 — 남은 차이는 «미세 보완»으로 서술"
            if score >= 90
            else (
                "목표에 가까운 편 — 남은 차이를 문제로 부풀리지 말 것"
                if score >= 70
                else (
                    "차이가 분명한 상태 — 무엇이 다른지 구체적으로"
                    if score >= 40
                    else "차이가 큰 상태 — 얼버무리지 말되, 개선 폭이 크다는 관점으로"
                )
            )
        )
        sections += [
            "",
            f"# 유사도 점수 (계산 완료): {score}점 — {band}",
            "",
            "※ 부위별 격차 등급을 규칙으로 합산한 값입니다. 점수를 새로 만들지 말고,",
            "  summary·silhouette 의 어조를 위 해석에 맞추세요.",
        ]

    if blocked:
        sections += [
            "",
            "## 판단 불가 부위",
            "",
            "\n".join(_part_line(p) for p in blocked),
            "",
            "※ 이 부위들은 점수에 반영하지 마세요. cautions 에 이걸 언급하지 마세요 —",
            "  어떤 부위가 왜 빠졌는지는 화면이 이 목록에서 이름과 사유를 직접",
            "  뽑아 이미 보여줍니다. cautions 에 다시 쓰면 뭉뚱그린 문장이",
            "  중복으로 뜹니다.",
        ]

    if failed:
        sections += [
            "",
            f"## 진단 실패 부위: {', '.join(failed)}",
            "",
            "※ 기술적 실패입니다. 점수에 반영하지 말고 언급도 하지 마세요.",
        ]

    if excluded:
        sections += [
            "",
            f"## 분석되지 않은 부위: {', '.join(excluded)}",
            "",
            "※ 사진에서 검출되지 않아 **아무 정보가 없는** 부위입니다.",
            "🔴 **silhouette · key_differences · summary · strengths 어디에도",
            "   이 부위를 넣지 마세요.** 사진에 그 부위가 보이더라도 마찬가지입니다 —",
            "   분석이 안 됐다는 건 형태를 신뢰할 만큼 못 봤다는 뜻입니다.",
            "   실측(2026-08-19): 하체가 검출되지 않은 사용자에게 silhouette 이",
            "   '하체의 비중이 더 높게 나타납니다' 라고 썼고, key_differences 는",
            "   '상체 대비 하체의 볼륨이 적습니다' 라고 정반대로 썼습니다.",
            "   둘 다 근거가 0 인 문장이고, 서로 모순되기까지 했습니다.",
            "🔴 실루엣은 **분석된 부위들 사이의 관계**로만 쓰세요.",
            "   위아래(상하체) 축이 막히면 좌우 폭·두께·윤곽 축으로 옮기면 됩니다.",
            "  cautions 에 이 부위들을 언급하지 마세요 — 어떤 부위가 왜 빠졌는지는",
            "  화면이 excluded 목록에서 이름과 사유를 직접 뽑아 이미 보여줍니다.",
            "  cautions 에 다시 쓰면 뭉뚱그린 문장이 중복으로 뜹니다.",
        ]

    sections += [
        "",
        "# 인바디",
        "",
        _inbody_block(inbody),
        "",
        "# 요청",
        "",
        "위 결과를 종합해 최종 진단을 JSON 으로 반환하세요.",
    ]
    return "\n".join(sections)
