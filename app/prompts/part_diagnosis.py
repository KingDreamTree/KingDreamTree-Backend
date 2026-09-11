"""F08 — 부위별 비교 진단 프롬프트 (전 부위 1회 호출).

━━ 왜 부위마다 부르지 않는가 ━━

입력이 크롭이 아니라 **원본 + 오버레이**로 확정된 순간(llm-strategy.md §F08),
부위별 호출은 같은 원본 사진을 부위 수만큼 반복 업로드하는 구조가 된다.
9부위면 레퍼런스 원본 9장 + 사용자 원본 9장 = 18장인데 실제 정보량은 2장이다.

그리고 더 중요한 문제: **비교는 부위 하나만 봐서는 불가능하다.**
"어깨가 좁다"는 골반 대비·전신 비율 대비 판단이다. 부위별로 격리해서 부르면
9개 호출이 서로의 판단을 모르므로 "이 부위가 1순위"라는 답이 9번 나와도
막을 방법이 없다. 한 번에 보면 순위가 한 문맥 안에서 정해진다.

━━ 역할 분담 (2026-08-14 개정 — 사진 간 크기 수치 폐기) ━━

원래는 "면적·너비는 코드가 재서 표로 준다"였다. 실연동에서 폐기했다 —
두 사진은 각도·거리·자세·옷이 전부 달라, 분모를 고정해도 소매 흡수와 자세만으로
±70% 씩 출렁였다. 그 수치를 "모순되지 말라"고 강제하면 VLM 이 노이즈에 맞춰
진단문을 쓴다. 정밀해 보이는 노이즈는 없는 것보다 나쁘다.

지금의 경계:

    코드 → 각도가 소거되는 수치만
           (같은 사진 안의 좌우 대칭 · 인바디 실측 · 옷 흡수 비율 · 잘림 신호)
    VLM  → 크기·형태의 비교 판단 자체 (이미지의 실루엣·비율로, 수치 발명 금지)

diff_pct 등은 내부 로깅·대표부위 선정용으로만 남는다. 프롬프트에는 안 나간다.

━━ 부위 카드 규칙은 여기 없다 (2026-09-11) ━━

규칙은 part_rules.py 한 벌을 라이브 경로(part_comparison.py)와 같이 쓴다. 이 파일은
«이 경로의 입력이 무엇인가» 만 만든다 — 원본+오버레이 4장(_INTRO), 색 범례와 옷·잘림·
좌우 쌍 표시(_legend_block), 세그 신뢰도 신호(_metrics_block), 좌우 대칭(_symmetry_block),
인바디 인용 지정(_citation_targets · _inbody_block).
"""

from typing import Any

from app.prompts.part_rules import PART_OUTPUT, PART_RULES, look_at

_INTRO = """당신은 두 체형 사진을 부위별로 비교하는 전문가입니다.

이미지 4장이 순서대로 주어집니다.

  1. 레퍼런스(목표 체형) 원본
  2. 레퍼런스 부위 오버레이 — 비교 대상 부위를 색으로 칠한 것
  3. 사용자(현재 체형) 원본
  4. 사용자 부위 오버레이 — 같은 색 규칙

색과 부위의 대응은 부위 범례에 있습니다. 범례에 없는 색과 어둡게 처리된 영역은 비교 대상이
아닙니다. 범례의 각 부위 줄에 붙은 옷 비율·잘림·좌우 쌍 표시는 그 부위를 판단할 때 따르세요 —
옷을 이유로 못 봤다고 할 수 있는지는 그 줄이 정합니다."""

#: ⚠️ 부위 카드 규칙은 여기 없다 — part_rules 한 벌을 라이브 경로와 같이 쓴다 (그 모듈 주석).
SYSTEM_PROMPT = "\n\n".join((_INTRO, PART_RULES, PART_OUTPUT))


#: 인바디 세그먼트의 한글 이름. **"전체"를 명시하는 게 핵심이다** —
#: 인바디는 팔 하나를 통째로 재므로 상완·전완을 나눌 수 없는데, 이름이 영문
#: 코드로만 있으면 LLM이 그 값을 세부 부위의 값처럼 인용한다.
_SEGMENT_KO = {
    "LEFT_ARM": "왼팔 전체",
    "RIGHT_ARM": "오른팔 전체",
    "TRUNK": "몸통 전체",
    "LEFT_LEG": "왼쪽 다리 전체",
    "RIGHT_LEG": "오른쪽 다리 전체",
}


#: 이 비율 이상일 때만 '옷에 가려 판단 불가' 선언 자격을 준다.
#: ⚠️ **튜닝 대상.** 실측 — 몸에 붙는 셔츠에서 몸통 39% · 전완 50% 가 나왔고
#:    둘 다 실루엣은 읽혔다. 헐렁한 옷은 그보다 높다. 그 사이인 65% 를 잠정선으로.
_BLOCK_ELIGIBLE_RATIO = 0.65


def _legend_block(parts: list[dict[str, Any]], metrics: dict[str, Any] | None = None) -> str:
    """부위 범례. **가림 선언 자격을 부위 줄에 직접 박는다.**

    ⚠️ 전역 규칙("표에 없는 부위는 가림 선언 금지")은 두 번 무시됐다 (2026-08-15
       라이브 2·3차 — 왼쪽 전완만 옷 65% 인데 맨살인 오른쪽 0% 까지 "옷에
       가려짐"으로 따라갔다). 항목을 하나씩 생성하는 모델에게 전역 제약은 잘
       안 붙는다. 모델이 항목을 만들 때 다시 읽는 곳은 **범례의 그 부위 줄**
       이므로, 자격을 거기에 박는다 — _citation_targets 를 코드로 정한 것과
       같은 원리다.
    """
    rows = (metrics or {}).get("parts") or {}
    lines = []
    for p in parts:
        name = p["class_name"]
        row = rows.get(name) or {}
        ratio = (row.get("user") or {}).get("clothing_ratio")

        # ⚠️ **옷 게이트는 '옷 때문에 가렸다'만 다룬다.** 잘림은 별개 사유다.
        #    실측(2026-08-16) — 전완이 프레임에 잘렸는데(is_truncated) 옷 흡수가
        #    50% 라 "가림 선언 금지" 가 그 줄에 박혔고, 모델이 잘린 부위를
        #    MODERATE/HIGH 로 자신 있게 판단해버렸다. 금지의 범위를 옷으로 좁힌다.
        if not ratio:
            gate = " — 옷 흡수 없음: **옷 때문에** 판단 불가라고 하지 마세요"
        elif ratio < _BLOCK_ELIGIBLE_RATIO:
            gate = (
                f" — 옷 흡수 {ratio:.0%}: 옷 위로도 실루엣이 대부분 보입니다."
                " **옷을 이유로** 판단 불가라고 하지 마세요"
            )
        else:
            gate = (
                f" — 옷 흡수 {ratio:.0%}: 형태를 **전혀** 못 읽을 때만 옷을 이유로"
                " 판단 불가 가능 (윤곽이 조금이라도 보이면 판단하세요)"
            )

        # ⚠️ 부위마다 **볼 지점**을 준다 (2026-09-11). 이 경로는 색으로 «어디가 그
        #    부위인지» 는 알려주지만 «그 부위의 어디를 보라» 는 없었다. 위치만 주면
        #    부위가 달라도 같은 문장이 나온다 (part_comparison._LOOK_AT 주석의 실측 —
        #    상완·전완 4개가 한 문장으로 통일). 형태 묘사 규칙도 이 지점이 있어야 선다.
        look = look_at(name)
        if look:
            gate += f"\n  볼 것: {look}"

        # ⚠️ 잘림은 옷과 무관하게 그 부위 줄에 붙인다 — 전역 경고는 부위 줄의
        #    지시에 묻힌다 (_citation_targets·옷 게이트와 같은 이유).
        if row.get("user_truncated"):
            gate += (
                "\n  ⚠️ **이 부위는 프레임에 잘렸습니다.** 안 보이는 부분이 있으므로"
                " 전체 형태를 판단하지 마세요 — 보이는 만큼만 쓰고 confidence 를"
                " 낮추거나, 형태를 못 읽으면 판단 불가로 두세요."
            )
        # ⚠️ 좌우 쌍은 **등급만** 맞춘다 (2026-08-20 개정). 종전에는 여기서
        #    "두 카드에 똑같은 문장을 넣고 주어를 «양쪽»으로" 라고 지시했는데,
        #    그 결과 한쪽에만 해당하는 인바디 수치가 반대쪽 카드로 복사됐다
        #    (실측: 오른쪽 상완 카드가 "왼팔 …%" 를 인용). 좌우를 묶어 부르는
        #    것은 종합 진단의 몫이고, 부위 카드는 자기 부위를 말한다.
        pair = _pair_of(name, {q["class_name"] for q in parts})
        if pair:
            gate += (
                f"\n  ⚠️ **좌우 쌍: {pair} 과(와) 한 쌍입니다.** gap_level·priority 는"
                f" 같게 맞추되, 문장의 주어는 «{p.get('name_ko') or name}» 입니다"
                f" — «양쪽»으로 묶지 말고, 반대쪽 값·수치를 끌어오지 마세요."
            )
        lines.append(f"- {name} ({p.get('name_ko') or name}) = {p.get('color_hex') or '?'}{gate}")
    return "\n".join(lines)


def _pair_of(class_name: str, available: set[str]) -> str | None:
    """좌우 짝 이름. 짝이 이번 진단에 실제로 있을 때만 돌려준다."""
    if class_name.startswith("Left_"):
        other = "Right_" + class_name[5:]
    elif class_name.startswith("Right_"):
        other = "Left_" + class_name[6:]
    else:
        return None
    return other if other in available else None


def _metrics_block(metrics: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    """수치 블록 — **사진 간 크기 비교 수치는 없다** (2026-08-14 폐기).

    ━━ 왜 면적·너비 비교 표를 없앴는가 ━━

    실연동 실측이 근거다. 분모를 교집합으로 고정해도 남는 수치가
    소매 흡수 87% + 자세·각도 차이만으로 ±70% 씩 출렁였다. 두 사진은
    카메라 각도·거리·자세·옷이 전부 다르다 — 그 조건에서 픽셀 크기 비교는
    **정밀해 보이는 노이즈**고, VLM 에게 "이 수치와 모순되지 말라"고
    강제하면 노이즈에 맞춰 진단문을 쓰게 된다.

    남긴 수치는 각도가 소거되는 것들뿐이다:
      · 좌우 대칭       — 같은 사진 안의 비교라 카메라 조건이 동일 (_symmetry_block)
      · 인바디 실측     — 카메라와 무관한 임피던스 측정 (_inbody_block)
      · 옷 흡수 비율    — 크기가 아니라 신뢰도 신호
      · 프레임 잘림     — 크기가 아니라 신뢰도 신호

    크기·형태의 비교 판단은 VLM 이 이미지를 보고 하되, 구체적 수치(%·cm)를
    지어내지 않는다 (§출력 규칙).
    """
    rows = metrics.get("parts") or {}
    if not rows:
        return "(세그멘테이션 부가 정보 없음. 이미지만으로 판단하세요.)"

    out = ["※ 두 사진 사이의 크기 비교 수치는 없습니다 — 각도·거리·자세가 달라 성립하지 않습니다."]

    truncated = [n for n, m in rows.items() if m.get("user_truncated")]
    if truncated:
        out.append(
            f"※ 사용자 사진에서 프레임에 잘린 부위: {', '.join(truncated)} "
            "— 전체 형태를 볼 수 없으니 confidence 를 낮추세요."
        )

    # 옷 픽셀을 흡수해 살린 부위 — 그 비율만큼은 맨살이 아니라 옷 실루엣이다.
    # 임계값을 두지 않고 비율 자체를 보여준다. 판단은 모델이 부위별로 한다.
    clothed = []
    for n, m in rows.items():
        ur = (m.get("user") or {}).get("clothing_ratio")
        rr = (m.get("reference") or {}).get("clothing_ratio")
        if ur or rr:
            bits = []
            if ur:
                bits.append(f"사용자 {ur:.0%}")
            if rr:
                bits.append(f"레퍼런스 {rr:.0%}")
            clothed.append(f"{n} ({' · '.join(bits)})")
    if clothed:
        out.append(
            f"※ 옷에서 흡수된 픽셀이 포함된 부위: {', '.join(clothed)} — 그 비율만큼은 맨살이\n"
            "  아니라 옷 위 실루엣입니다. 비율·외곽선에는 써도 되지만 근육 윤곽의 근거로는\n"
            "  쓰지 말고, 비중이 크면 confidence 를 낮추세요.\n"
            "※ 이 표에 없는 부위를 '옷에 가려 판단 불가'라고 하지 마세요. 좌우 쌍의 한쪽만\n"
            "  가려졌다면 가려진 쪽만 못 본 것이고 반대쪽은 눈으로 판단합니다."
        )
    else:
        out.append("※ 옷 흡수가 감지된 부위가 없습니다 — '옷에 가려 판단 불가'를 쓰지 마세요.")
    return "\n".join(out)


_SIDE_KO = {"LEFT": "왼쪽", "RIGHT": "오른쪽"}


def _inbody_lr(inbody: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """인바디 세그먼트에서 좌우 근육량 차이를 계산한다 — **산수는 코드가 한다.**

    ⚠️ 이 값이 없으면 LLM 이 픽셀 대칭만 보고 "눈에 띄게 더 발달"이라 쓴다.
       실측(2026-08-15): 픽셀은 왼팔이 커 보였지만 인바디 제지방은 좌 2.72kg /
       우 2.74kg — 사실상 대칭이었다. 근육 비대칭이 아니라 자세·지방 분포
       차이였는데, 진단문과 종합 요약 모두 "근육량 차이가 두드러진다"로 나갔다.
       두 신호를 나란히 줘야 조율 규칙(아래)이 작동한다.
    """
    if not inbody:
        return {}
    segments = inbody.get("segments") or {}
    out: dict[str, dict[str, Any]] = {}
    for label, left_key, right_key in (
        ("팔", "LEFT_ARM", "RIGHT_ARM"),
        ("다리", "LEFT_LEG", "RIGHT_LEG"),
    ):
        left = (segments.get(left_key) or {}).get("lean_mass")
        right = (segments.get(right_key) or {}).get("lean_mass")
        if not left or not right:
            continue
        diff = abs(left - right) / max(left, right) * 100
        larger = "LEFT" if left > right else "RIGHT" if right > left else None
        out[label] = {"diff_pct": diff, "larger": larger}
    return out


def _symmetry_block(
    ref_sym: dict[str, dict[str, Any]],
    user_sym: dict[str, dict[str, Any]],
    inbody_lr: dict[str, dict[str, Any]] | None = None,
) -> str:
    if not user_sym:
        return ""
    lines = ["", "## 좌우 쌍 비교 (같은 사람 안에서)"]
    for key, value in user_sym.items():
        side = _SIDE_KO.get(value.get("larger"))
        direction = f"**{side}이 더 큼**" if side else "좌우 동일"
        ref_value = ref_sym.get(key)
        ref_txt = f" · 레퍼런스는 {ref_value['diff_pct']:.1f}%" if ref_value else ""
        lines.append(f"- {key}: 픽셀 좌우 차이 {value['diff_pct']:.1f}%, {direction}{ref_txt}")
    for label, value in (inbody_lr or {}).items():
        side = _SIDE_KO.get(value.get("larger"))
        direction = f"{side}이 더 많음" if side else "대칭"
        lines.append(
            f"- 인바디 실측 근육량 ({label}): 좌우 차이 {value['diff_pct']:.1f}%, {direction}"
        )
    # ⚠️ 종전 문구는 «좌우 쌍은 같은 문장을 씁니다» 였다. ca709fc(2026-08-20)가 폐기한
    #    지시인데 여기만 남아, 정상 사진이면 매 요청 «주어는 그 카드의 부위» 규칙과 함께
    #    나가고 있었다 (segmap.symmetry 는 사람 윤곽만 잡히면 좌우 쌍마다 값을 채운다).
    lines.append(
        "이 수치는 좌우 gap_level 을 다르게 매길지 판단하는 데만 씁니다. 픽셀 차이\n"
        "10~20%는 자세·각도만으로도 흔하니 그것만으로 등급을 가르지 마세요.\n"
        "픽셀과 인바디 실측이 어긋나면 좌우 근육량은 실측을 믿으세요 — 실측 차이가\n"
        "3% 이내면 같게, 같은 방향으로 5% 이상일 때만 다르게 매깁니다."
    )
    return "\n".join(lines)


#: 인바디를 인용하지 않을 부위. **전완(전완)**.
#: ⚠️ 인바디 팔 세그먼트는 상완+전완을 통째로 잰 값이라 전완만의 수치가 아니다.
#:    전완 카드에 그 숫자를 붙이면 "전완 근육량이 89.9%"로 읽혀 오독을 부른다.
#:    상완이 같은 세그먼트를 대표하므로 정보 손실도 없다.
_NO_CITATION_PARTS = ("Left_Lower_Arm", "Right_Lower_Arm")

#: 인바디 수치를 인용할 세그먼트 최대 개수.
#: ⚠️ 세그먼트마다 하나씩 뽑으면 9부위 중 5장에 숫자가 붙는다. 실사용(해커톤은
#:    인바디를 항상 제출한다)에서 카드마다 "평균의 N% 수준입니다"가 반복돼
#:    진단이 표처럼 읽혔다. 숫자는 근거가 될 때만 힘이 있고, 다섯 번 반복되면
#:    배경음이 된다. 가장 할 말이 있는 두 곳에만 남긴다.
_CITATION_MAX = 2


def _citation_targets(
    part_to_segment: dict[str, str],
    metrics: dict[str, Any],
    inbody: dict[str, Any] | None = None,
) -> dict[str, str]:
    """인바디를 인용할 부위를 고른다 — **표준에서 가장 많이 벗어난 세그먼트 순으로 최대 2곳.**

    ⚠️ **이 선택은 코드가 한다.** LLM에게 "필요한 데만 인용하라"고 지시하면
       9개 항목을 스스로 대조해야 하는 전역 제약이 되는데, 항목을 하나씩 생성하는
       모델은 그런 제약을 자주 어긴다. 라우팅을 결정론적으로 만들면 어길 방법이
       없고, 어느 부위가 인용했는지 테스트로 검증할 수 있다.

    ⚠️ 기준은 **표준 대비 %가 100 에서 얼마나 벗어났나**다. 평균과 같은 수치는
       인용해봐야 사용자가 얻는 게 없다("평균의 99% 수준입니다"). 편차가 큰 쪽이
       할 말이 있는 쪽이다. 순위로 자르므로 임계값 경계에서 튀지 않는다
       (89.9% 는 되고 90.1% 는 안 되는 식의 절벽이 없다).

    ⚠️ 옷에 가려 시각 판단이 불가한 부위는 여기 없어도 인용한다 — 그 경우
       인바디가 유일한 근거다. 그 예외는 프롬프트가 처리한다.

    Returns:
        {segment: 대표 class_name}  — 최대 _CITATION_MAX 개
    """
    rows = metrics.get("parts") or {}
    segments = (inbody or {}).get("segments") or {}
    if not segments:
        return {}  # 인바디가 없으면 인용할 수치 자체가 없다

    # 세그먼트별 대표 부위 — 면적 격차가 가장 큰 부위. 전완은 후보에서 뺀다.
    best: dict[str, tuple[str, float]] = {}
    for part, segment in part_to_segment.items():
        if part in _NO_CITATION_PARTS:
            continue
        diff = ((rows.get(part) or {}).get("diff_pct") or {}).get("area_share")
        magnitude = abs(diff) if diff is not None else -1.0
        if segment not in best or magnitude > best[segment][1]:
            best[segment] = (part, magnitude)

    # 표준 대비 % 편차가 큰 세그먼트 순.
    ranked = [
        (abs(pct - 100), segment, part)
        for segment, (part, _) in best.items()
        if (pct := (segments.get(segment) or {}).get("lean_percentage")) is not None
    ]

    # ⚠️ 폴백 — lean_percentage 는 **DB 컬럼이 아니라 raw_ocr 에서 읽는 선택값**이다
    #    (inbody_repo.to_prompt_payload). OCR 이 못 잡았거나 사용자가 PATCH 로 고친
    #    인바디에는 없을 수 있다. 그때 인용을 통째로 없애면 인바디를 제출했는데도
    #    진단문에 수치가 한 번도 안 나온다 — 편차를 못 재는 것이지 근거가 없는 게
    #    아니다. 순위 기준만 면적 격차로 바꾸고 상한(2곳)은 그대로 간다.
    if not ranked:
        ranked = [(magnitude, segment, part) for segment, (part, magnitude) in best.items()]

    ranked.sort(key=lambda r: (-r[0], r[1]))  # 큰 순, 동률이면 이름순(재현성)
    return {segment: part for _, segment, part in ranked[:_CITATION_MAX]}


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
            # ⚠️ 자격을 **그 부위 줄에** 박는다 — _legend_block 의 가림 선언과 같은 이유로,
            #    전역 규칙("인용 표시 있는 데만 쓰세요")은 항목별 생성에서 잘 안 지켜진다.
            if citation_targets.get(segment) == class_name:
                mark = " **[인용]** ← 이 부위에서만 수치를 문장에 씁니다"
            elif class_name in _NO_CITATION_PARTS:
                mark = " — 🔴 인용 금지: 팔 전체를 잰 값이라 전완의 수치가 아닙니다"
            else:
                mark = " — 인용하지 마세요 (참고용). 이 부위는 이미지에서 본 것으로 씁니다"
            lines.append(
                f"- {class_name} ← {_SEGMENT_KO.get(segment, segment)}"
                f"({segment}): {' · '.join(parts_desc)}{mark}"
            )

    lines.append("")
    lines.append(
        "※ 수치는 [인용] 부위에서만 씁니다 (많아야 두 곳). 나머지 부위에 인바디 수치가\n"
        "  한 번도 안 나오는 것이 정상입니다. 인용할 때는 화살표 오른쪽의 한글 이름으로\n"
        "  부릅니다 — 팔 전체를 잰 값이라 전완이나 상완만의 수치가 아닙니다.\n"
        "※ 못 봐서 인바디로 판단한 부위는 [인용]과 무관하게 그 수치를 근거로 씁니다."
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
    citation_targets = _citation_targets(part_to_segment, metrics, inbody)

    return f"""# 부위 범례 (색 → 부위)

{_legend_block(parts, metrics)}

이 {len(parts)}개 부위 전부에 대해 진단 항목을 만드세요.

# 세그멘테이션 수치

{_metrics_block(metrics, parts)}
{_symmetry_block(ref_symmetry, user_symmetry, _inbody_lr(inbody))}
{_inbody_block(inbody, part_to_segment, citation_targets)}

# 요청

위 4장의 이미지와 수치를 종합해 부위별 비교 진단을 JSON 으로 반환하세요."""
