"""부위별 비교 진단 — **라이브(웹캠·미디어파이프) 경로.** 세그멘테이션 없이 원본 2장으로 (F08-direct).

━━ 이 파일이 하는 일 ━━

**사용자 메시지만 조립한다.** 시스템 프롬프트 본문은 DB(prompt_version)에 있다 — 입력 설명
'part.intro.live' + 사진 경로와 같이 쓰는 'part.rules' · 'part.output'
(app/services/prompt_store.py, vlm.PART_LIVE_PROMPT). 이 경로의 입력은:

    · 이미지 2장 (레퍼런스 → 사용자). 부위를 칠한 그림은 없다
    · 부위는 색 대신 **해부학적 위치를 글로** 지목한다 (_LOCATION)
    · 옷 흡수 비율 같은 세그 수치가 없으니, 옷이 어디를 덮는지는 모델이 직접 본다

세그 없이 성립하는 이유: 사진 경로에서도 세그 수치는 판정 근거에서 빠졌고
(part_diagnosis._metrics_block docstring), 세그가 주던 것은 부위 지목·옷 비율·잘림뿐이다.
앞의 것은 글로, 뒤의 둘은 모델의 직접 관찰로 갈아끼운다.

⚠️ 이미지 순서는 vlm.compare_parts_direct 가 넣는 순서와 같아야 한다 (레퍼런스 → 사용자).
   어긋나면 비교가 정반대로 나온다.
⚠️ 출력 형식은 사진 경로와 같다 (DB 'part.output') — vlm.parse_part_response 를 같이 쓴다.
"""

from typing import Any

from app.prompts.overall_diagnosis import _inbody_block
from app.prompts.part_rules import look_at

#: 색 오버레이를 대신해 **글로** 부위를 지목한다.
#  ⚠️ 좌우는 «인물 자신 기준» 이다 — 세그멘테이션(Sapiens)이 붙이는 라벨과 같은 규약이어야
#     두 경로의 결과가 같은 부위를 가리킨다 (services/part_pairing.py 모듈 주석).
_LOCATION: dict[str, str] = {
    "Torso": "어깨선 아래부터 골반까지 (가슴·복부·허리)",
    "Left_Upper_Arm": "인물 자신의 왼쪽 어깨~팔꿈치",
    "Right_Upper_Arm": "인물 자신의 오른쪽 어깨~팔꿈치",
    "Left_Lower_Arm": "인물 자신의 왼쪽 팔꿈치~손목",
    "Right_Lower_Arm": "인물 자신의 오른쪽 팔꿈치~손목",
    "Left_Upper_Leg": "인물 자신의 왼쪽 골반~무릎",
    "Right_Upper_Leg": "인물 자신의 오른쪽 골반~무릎",
    "Left_Lower_Leg": "인물 자신의 왼쪽 무릎~발목",
    "Right_Lower_Leg": "인물 자신의 오른쪽 무릎~발목",
}


def build_part_comparison_prompt(
    parts: list[dict[str, Any]],
    inbody: dict[str, Any] | None,
) -> str:
    """라이브 경로 사용자 메시지. 이미지 2장은 호출부가 앞에 붙인다.

    Args:
        parts: body_part 마스터 행 (class_name / name_ko). ⚠️ 부위 목록은 DB 가
               유일한 출처다 — 여기서 만들지 않는다 (사진 경로와 같은 규약).
    """
    lines = ["# 부위 목록", ""]
    for p in parts:
        name = p["class_name"]
        ko = p.get("name_ko") or name
        lines.append(f"- `{name}` ({ko}) — {_LOCATION.get(name, ko)}")
        look = look_at(name)
        if look:
            lines.append(f"    볼 것: {look}")
    lines += [
        "",
        "# 인바디",
        "",
        _inbody_block(inbody),
        "",
        "# 요청",
        "",
        f"위 {len(parts)}개 부위를 전부 담아 JSON 으로 반환하세요.",
    ]
    return "\n".join(lines)
