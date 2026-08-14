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

크기·형태의 비교는 **이미지를 보고** 판단합니다. 두 사진은 촬영 각도·거리·
자세가 다르므로:

⚠️ 각도나 자세 차이로 설명될 수 있는 정도의 차이는 격차로 잡지 마세요.
   확실히 각도 이상의 차이일 때만 gap 을 매기고, 애매하면 confidence 를 낮추세요.
⚠️ 구체적 수치를 만들어내지 마세요. "약 15% 가늘다", "5cm 차이" 같은 표현 금지 —
   이미지에서 그런 정밀도는 나오지 않습니다. "눈에 띄게 가늘다" 정도로 쓰세요.

함께 주는 수치는 각도와 무관한 것들뿐입니다: 같은 사진 안의 좌우 대칭,
인바디 실측(있으면), 옷 흡수 비율·잘림 같은 신뢰도 신호.

당신이 판단할 것: 근육의 윤곽과 선명도, 실루엣의 형태, 부위 간 비례,
자세와 정렬, 좌우 균형의 시각적 인상.

# 기준선이 두 개입니다 — 섞으면 진단이 무너집니다

이 진단의 질문은 하나입니다: **목표 체형(레퍼런스 사진)과 얼마나 다른가.**
그런데 인바디의 "표준 대비 %"가 답하는 질문은 다릅니다: **일반인 평균과
얼마나 다른가.** 잣대가 다른 두 수치입니다.

⚠️ 평균 수준(90~100%)이어도 목표가 근육질이면 격차는 큽니다. 표준 대비 %로
   gap_level 을 매기면 모든 부위가 '약간 부족'으로 눌립니다 —
   **보이는 부위의 gap_level 은 언제나 이미지가 정합니다.**

부위마다 쓸 수 있는 근거는 세 종류이고, 각각 용도가 정해져 있습니다.

1. **이미지 관찰** — 두 사진의 실루엣·비례 비교. **gap_level 의 유일한 근거**
   (옷에 가린 부위만 예외 — 아래 표)
2. **같은 사진 안의 좌우 대칭** — 각도·거리가 소거된 픽셀 비교. 좌우 문장 구분용
3. **인바디 실측** — 제출됐을 때만. 용도 세 가지뿐:
   ① 현재 상태를 설명하는 보조 수치 (인용할 때 '평균 대비'임을 밝히세요)
   ② 좌우 근육량 비교 — 같은 사람의 실측이라 픽셀보다 강한 유일한 용도
   ③ 옷에 가린 부위의 유일한 근거

⚠️ **두 사진 사이의 크기 수치는 주어지지 않습니다.** 촬영 각도·거리·자세가 달라
   픽셀 크기 비교가 성립하지 않기 때문입니다. 크기 차이는 1번(이미지 관찰)으로만
   판단하고, **없는 수치를 지어내지 마세요.**

부위마다 가장 강한 신호 하나로 문장을 시작하되, 서두를 무엇으로 열든
**목표와의 격차 판단은 이미지에서 가져옵니다**:

- 인바디 표에 `[인용]` 표시가 붙은 부위 → 인바디 수치로 시작해도 좋습니다.
  단 그 수치는 현재 상태의 설명이지 목표와의 격차 근거가 아닙니다
- 그 외 부위 → 이미지에서 본 것으로 시작 (좌우 차이가 뚜렷하면 그것으로)
- 시각과 인바디가 어긋나면: 크기·형태는 시각을, **좌우 근육량은 인바디를** 믿으세요

⚠️ 인바디는 좌우 팔·다리와 몸통 5단위라 **상완과 전완을 구분하지 못합니다.**
   같은 세그먼트를 공유하는 부위들은 값이 똑같으므로, `[인용]` 부위에서 한 번만
   수치를 쓰고 **나머지 부위에서 같은 숫자를 반복하지 마세요.** 그 안에서 어느
   쪽이 더 부족한지는 이미지에서 본 형태로 나눕니다.

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

### differences — 관찰만, 발명 금지

**이미지에서 실제로 본 것**만 쓰세요:
근육의 갈라짐과 경계선, 실루엣의 처짐, 피부 아래 윤곽의 선명도, 자세 정렬, 좌우 비틀림.
크기 차이를 적을 때는 정도 표현("눈에 띄게", "약간")으로 — 숫자(%·cm)는 금지입니다.

⚠️ 이미지에서 확인 못 했으면 **빈 배열 `[]`** 로 두세요.
   수치가 작으니 흐릿하겠거니 하고 **추론해서 쓰지 마세요.** 그건 관찰이 아닙니다.
⚠️ assessment 의 격차 문장을 되풀이하지 마세요 ("~가늘어 보입니다" 재진술 금지).
   여기 들어갈 것은 윤곽·경계·라인·자세 같은 **구체 디테일**입니다 —
   그런 디테일이 없으면 빈 배열이 낫습니다.

### priority — 서로 다른 값을 쓰세요

부위 간 **상대 순위**입니다. 같은 값이 세 개 이상 몰리면 순위 정보가 사라집니다.
격차가 큰 순서대로 1, 2, 3… 을 배분하고, 개선이 불필요한 부위에만 5를 겹쳐 쓰세요.

### gap_level 이 NONE 이면 개선 제안을 하지 마세요

이미 목표에 도달한 부위입니다. "조금 더 늘리면 좋습니다" 같은 말을 붙이면
사용자는 모든 부위가 부족하다고 읽습니다. **유지하라고만** 하세요.

### 어떤 두 부위도 같은 문장이면 안 됩니다

아홉 항목이 화면에 나란히 놓입니다. 복사한 듯한 문장 두 개는 성의 없는
진단으로 읽힙니다. 부위마다 그 부위만의 근거로 구분하세요.

- **좌우 쌍** (왼/오른 같은 부위): 아래 "좌우 쌍 비교"의 방향을 문장에
  넣으세요. 왼쪽 항목과 오른쪽 항목은 서로를 언급하는 순간 달라집니다.
  (예: "오른쪽 허벅지는 왼쪽보다 살짝 더 발달해 있습니다")
  ⚠️ 좌우 비교는 **문장을 구분하는 용도일 뿐**입니다. gap_level 은 언제나
  **레퍼런스와의 격차**로만 매기세요 — 레퍼런스보다 큰 부위는 좌우 어느 쪽이
  뒤처졌든 NONE 입니다.
  ⚠️ 처방도 방향을 따릅니다. "집중해서 키우세요"는 **뒤처진 쪽에만** 쓰고,
  앞선 쪽에는 유지나 균형 잡기를 권하세요. 양쪽 모두에 "집중"을 붙이면
  좌우를 비교한 의미가 사라집니다.
  ⚠️ 실측 근육량이 좌우 대칭이면 방향 서술 대신 "양쪽이 고르게 목표보다
  가늡니다"처럼 쓰되, 두 항목의 **문장 구조는 달리하세요** — 한쪽은 상태
  중심으로, 다른 쪽은 처방 중심으로. 같은 문장을 두 번 쓰면 안 됩니다.
- **같은 팔다리의 이웃 부위** (허벅지↔종아리, 위팔↔팔뚝): 처방을 부위의
  역할에 붙이세요 — 허벅지는 하체 힘의 중심, 종아리는 걷고 뛰는 힘,
  팔뚝은 쥐는 힘. 일반 상식 수준의 역할만 쓰고 지어내지 마세요.
- 그래도 내용이 겹치면 문장 구조라도 바꾸세요.

## 문체 — 운동을 처음 하는 사람에게 말하듯

읽는 사람은 **운동 초보자**입니다. 헬스장 용어도 해부학 용어도 모릅니다.
중학생이 읽어도 바로 이해되는 문장으로, 부드러운 존댓말로 쓰세요.

`[지금 상태] → [그래서 뭘 하면 되는지]` 순서로 한 문장씩.
부위는 «왼쪽/오른쪽»으로 부르고, 할 수 있으면 **누구나 아는 운동 이름**을
하나 들어주세요 (스쿼트·런지·플랭크·팔굽혀펴기·덤벨 컬 정도만 — 그보다
낯선 운동명은 쓰지 마세요. 어차피 상세 운동은 루틴에서 정해줍니다).

### 용어 대체표 — 왼쪽 단어가 나오면 틀린 겁니다

| 쓰지 마세요 | 이렇게 쓰세요 |
|---|---|
| 제지방량 | 근육량 |
| 표준 대비 82% | 평균의 82% 수준 |
| 레퍼런스 | 목표 체형 |
| 대퇴사두근 | 허벅지 앞쪽 근육 |
| 비복근 | 종아리 근육 |
| 상완이두근 / 삼두근 | 팔 앞쪽 / 뒤쪽 근육 |
| 전완 굴곡근 | 팔뚝 근육 |
| 광배근 / 기립근 | 등 근육 / 허리 근육 |
| 편측 운동 | 한쪽씩 하는 운동 |
| 코어 안정성 | 몸의 중심을 잡는 힘 |
| 볼륨 / 발달도 | 크기 / 근육량 |
| 선명도 / 데피니션 | 근육 라인 |

**인바디 수치**는 빼지 말고 쉬운 틀에 넣으세요 — 숫자가 신뢰를 만듭니다.
("인바디를 보면 왼팔 근육량이 평균의 82% 수준입니다" 처럼)
⚠️ 숫자를 써도 되는 것은 **주어진 인바디 수치와 좌우 차이뿐**입니다.
   크기 비교 수치는 주어지지 않으므로 만들어 쓸 수 없습니다.

서두와 종결을 다양하게 쓰세요 — 인바디 인용 / 눈으로 본 형태 / 좌우 차이 /
유지 안내는 각각 다른 문장 틀입니다.
⚠️ 단, 다양성을 위해 **없는 사실을 만들지 마세요.** 근거가 정말 같은 부위들
(예: 다리 네 부위가 전부 충분)은 표현만 달리하고 내용은 짧게 유지합니다.

### 예시 — 근거 유형별로 서두가 다릅니다

⚠️ **아래는 전부 가상의 다른 사용자입니다.** 지금 진단하는 사진·수치와 무관합니다.
   문장의 틀(상태→처방)과 말투만 따르고 **내용을 복사하지 마세요.**
   특히 "옷에 가렸다", "라인이 흐릿하다" 같은 관찰은 예시 사용자의 것입니다 —
   지금 이미지에서 직접 확인한 경우에만 쓰세요.
   **처방 문장도 마찬가지입니다.** 예시의 문장을 통째로 옮겨 쓰지 마세요 —
   특히 좌우 쌍 두 항목에 예시의 같은 처방을 복사하면 진단 전체가
   복사·붙여넣기로 읽힙니다.

인바디 `[인용]` 부위 — 격차는 이미지가 정하고, 수치는 상태 설명으로 뒷받침:
{
  "class_name": "(범례의 부위명)",
  "differences": ["(이 이미지에서 직접 본 것. 없으면 빈 배열)"],
  "assessment": "목표 체형에 비해 왼팔이 눈에 띄게 가늡니다. 인바디로도 왼팔 전체 근육량이 평균의 82% 수준이라 실측이 같은 방향을 가리킵니다. 덤벨 컬처럼 팔을 굽히는 운동부터 시작해보세요.",
  "gap_level": "SIGNIFICANT", "priority": 1, "confidence": "HIGH", "blocked_reason": null
}

인바디는 평균 수준인데 목표와는 먼 부위 — **표준 대비 %로 격차를 낮추지 마세요**:
{
  "assessment": "인바디 근육량은 평균의 95% 수준으로 또래와 비슷하지만, 목표 체형과 견주면 몸통 두께 차이가 뚜렷합니다. 플랭크처럼 몸의 중심을 잡는 운동을 꾸준히 해주세요.",
  "gap_level": "MODERATE", "priority": 2, "confidence": "HIGH", "blocked_reason": null
}

같은 세그먼트의 나머지 부위 — 수치 반복 금지, 이미지에서 본 형태로 서술:
{
  "assessment": "왼쪽 팔뚝은 목표 체형보다 살짝 가는 정도입니다. 팔 운동을 마친 뒤 팔뚝 운동을 가볍게 더해주면 충분합니다.",
  "gap_level": "SLIGHT", "priority": 4, ...
}

목표 도달 부위 — 좌우 방향으로 쌍과 구분, 유지만:
{
  "assessment": "오른쪽 허벅지는 이미 목표 수준을 넘었고, 왼쪽보다도 살짝 앞서 있습니다. 지금 하던 운동량을 그대로 유지하면 됩니다.",
  "gap_level": "NONE", "priority": 5, ...
}
{
  "assessment": "왼쪽 허벅지도 목표에 도달했습니다. 오른쪽과의 작은 차이는 런지처럼 한쪽씩 하는 운동으로 좁힐 수 있습니다.",
  "gap_level": "NONE", "priority": 5, ...
}

옷에 가린 부위 — **지금 이미지에서 실제로 가려진 경우에만** (인바디가 유일한 근거이므로 반드시 인용):
{
  "differences": [],
  "assessment": "통이 넓은 바지에 가려 오른쪽 허벅지는 눈으로 확인하지 못했습니다. 대신 인바디를 보면 오른쪽 다리 전체 근육량이 평균의 87% 수준이라, 스쿼트 같은 하체 운동이 도움이 됩니다.",
  "gap_level": "MODERATE", "priority": 3, "confidence": "MEDIUM",
  "blocked_reason": "시각 확인 불가, 인바디 기준 판단"
}

⚠️ blocked 를 선언한 부위의 differences 는 **반드시 빈 배열**입니다 — 눈으로 본
   관찰이 있다면 가려진 게 아닙니다. 둘 다 보내면 시스템이 blocked 를 무시합니다.
   오버레이에서 그 부위의 형태를 정말 읽을 수 없을 때만 blocked 를 쓰세요.

⚠️ gap_level / confidence 는 **반드시 대문자**입니다. 소문자는 저장에 실패합니다.
⚠️ 사용자를 평가하거나 비하하지 마세요. 개선 관점으로 서술하세요.
⚠️ 의학적 진단·질병 언급 금지. 체형과 운동 관점으로만 서술하세요."""


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


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%"


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
        ratio = ((rows.get(name) or {}).get("user") or {}).get("clothing_ratio")
        if ratio:
            gate = f" — 옷 흡수 {ratio:.0%}: 형태를 읽을 수 없으면 가림 선언 가능"
        else:
            gate = " — 옷 흡수 없음: 이 부위에 '옷에 가려 판단 불가' 선언 금지"
        lines.append(f"- {name} ({p.get('name_ko') or name}) = {p.get('color_hex') or '?'}{gate}")
    return "\n".join(lines)


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

    out = [
        "※ 크기 비교 수치는 제공하지 않습니다 — 두 사진은 각도·거리·자세가 달라\n"
        "  픽셀 크기 비교가 성립하지 않습니다. 크기·형태 차이는 이미지의 실루엣과\n"
        "  비율로 판단하되, 각도·자세 차이로 설명될 수 있는 정도면 격차로 잡지 말고,\n"
        "  구체적 수치(%·cm)를 만들어내지 마세요."
    ]

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
            f"※ 옷에서 흡수된 픽셀이 포함된 부위: {', '.join(clothed)} "
            "— 표기된 비율만큼은 맨살이 아니라 옷 위 실루엣입니다. 실루엣·비율 판단에는 "
            "써도 되지만 근육 윤곽·질감의 근거로 쓰지 마세요. 비중이 크면 confidence 를 "
            "낮추고, 형태를 읽을 수 없으면 판단 불가를 선언하세요.\n"
            "🔴 **이 표에 없는 부위를 '옷에 가려 판단 불가'라고 하지 마세요.** 옷 흡수가\n"
            "   감지되지 않은 부위입니다. 좌우 쌍의 한쪽만 가려졌다면(예: 왼쪽 65% ·\n"
            "   오른쪽 0%) 가려진 쪽만 판단 불가이고 **반대쪽은 눈으로 판단합니다** —\n"
            "   한쪽을 따라 양쪽을 포기하지 마세요."
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
    lines.append(
        "🔴 좌우 쌍 부위의 문장은 **이 방향으로 서로 구분**하세요.\n"
        '   (예: 왼쪽이 작으면 왼쪽 항목에 "오른쪽보다 뒤처져 있다", '
        '오른쪽 항목에 "왼쪽보다 앞서 있다")\n'
        "※ 픽셀 차이 10~20%는 자세·각도만으로도 흔합니다.\n"
        "🔴 픽셀과 실측이 어긋나면 **좌우 근육량은 실측이 정답**입니다.\n"
        "   실측 좌우 차이가 3% 이내면 '더 발달했다'고 쓰지 마세요 — 자세나 지방\n"
        "   분포로 보이는 차이일 수 있으니 '약간 더 커 보인다' 까지만 씁니다.\n"
        "   실측이 같은 방향으로 5% 이상일 때만 발달 차이로 단정합니다."
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
        "   나머지 부위는 **같은 숫자를 반복하지 말고** 이미지에서 본 형태로 서술하세요.\n"
        "   인용할 때는 화살표 오른쪽의 한글 이름 그대로 부릅니다.\n"
        '   O "왼팔 전체 근육량이 평균의 82% 수준"\n'
        '   X "왼쪽 팔뚝 근육량이 평균의 82% 수준"  ← 팔뚝만 잰 값이 아닙니다\n'
        "※ 옷에 가려 시각 판단이 불가한 부위는 예외 — `[인용]` 여부와 무관하게\n"
        "   인바디가 유일한 근거이므로 수치를 인용합니다.\n"
        "🔴 표준 대비 %는 **일반인 평균과의 비교**입니다. 목표 체형과의 격차\n"
        "   (gap_level) 근거로 쓰지 마세요 — 옷에 가린 부위만 예외입니다.\n"
        "   평균 수준(90~100%)이어도 목표가 근육질이면 격차는 클 수 있습니다.\n"
        "※ 좌우 근육량 비교에는 인바디가 픽셀보다 강합니다 — '좌우 쌍 비교'의\n"
        "   실측 항목을 보세요."
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

{_legend_block(parts, metrics)}

이 {len(parts)}개 부위 전부에 대해 진단 항목을 만드세요.

# 세그멘테이션 수치

{_metrics_block(metrics, parts)}
{_symmetry_block(ref_symmetry, user_symmetry, _inbody_lr(inbody))}
{_inbody_block(inbody, part_to_segment, citation_targets)}

# 요청

위 4장의 이미지와 수치를 종합해 부위별 비교 진단을 JSON 으로 반환하세요."""
