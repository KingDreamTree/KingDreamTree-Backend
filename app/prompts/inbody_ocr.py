"""인바디 결과지 추출 프롬프트 (F07).

⚠️ 별도 OCR 엔진을 쓰지 않고 VLM(GPT-4o vision)이 직접 구조화 JSON을 뽑는다.
   근거는 docs/llm-strategy.md "F07 기술 선택" 절 참고.

기준 양식: InBody570 (docs 샘플 = 데스크탑 inbody_ex.jpg).
단일 양식이 전제이므로 프롬프트에 실제 라벨명을 그대로 박아 추출 정확도를 올린다.
다른 기종이 올라와도 라벨이 대체로 동일해 동작은 하지만, 없는 항목은 null이 된다.

━━ 2026-08-20 — "반환 형식" 완성 예문 제거 ━━

`USER_PROMPT`에 실제 InBody570 샘플(inbody_ex.jpg)의 완성된 수치를 "반환
형식" 예시로 박아뒀었다. F08/F09에서 이미 두 번 확인된 것과 같은 실패
패턴 — 여러 개의 서로 다른 실제 세션에서 사용자가 인바디를 스킵했는데도
(프론트 버그와 별개로) 부위별 진단이 이 예문과 **글자 그대로 같은 수치**
(왼팔 89.9% · 오른팔 90.6% 등)를 인용하는 사고가 반복됐다. 결과지 이미지가
흐리거나 예상 양식과 다르면, 모델이 실제로 읽으려 하는 대신 이 완성된
예문을 그대로 반환한 것으로 보인다.

이 프롬프트는 특히 위험하다 — F08/F09는 문장을 복사하지만, 여기는
**완전한 가짜 건강 데이터 한 세트**(체중·체지방률·부위별 근육량)를
사용자에게 실측값인 것처럼 보여주게 된다.

완성된 숫자를 전부 `<타입|null>` 자리 표시로 바꿨다. SYSTEM_PROMPT 의
"그럴듯한 값을 지어내지 마라"는 이미 있던 규칙이지만, 예문이 그 규칙보다
강하게 작용한다는 게 이번에도 확인됐다 — 그래서 규칙만 두고 예문은
아예 없앤다.

━━ 2026-08-21 — 부위별 두 표는 2차 «확대 조각» 호출로 읽는다 ━━

예문을 없애자 진짜 판독력이 드러났다: gpt-4o 는 이미지를 짧은 변 768px 로
줄여 보기 때문에, 결과지 한 장을 통째로 주면 부위별 표의 작은 숫자가 뭉개져
**그럴듯한 값을 지어낸다** (실측: 90.6% → 112.7%). 전신 수치는 큰 글씨라
전체 이미지에서도 정확하다. 그래서 ocr.extract_inbody 가 결과지를 2×2 겹침
조각으로 잘라 확대한 뒤 SEGMENT_USER_PROMPT 로 한 번 더 호출해 segments 만
덮어쓴다 (같은 결과지 15개 값 전부 정답 확인 — 전체 1장에서는 15개 중 14개 오답).
"""

SYSTEM_PROMPT = """너는 인바디(InBody) 체성분 결과지에서 수치를 읽어 JSON으로 변환하는 추출기다.

절대 규칙:
- 결과지에 **실제로 인쇄된 숫자만** 읽는다. 계산하거나 추정하지 마라.
- 값이 안 보이거나 확실하지 않으면 반드시 null. 그럴듯한 값을 지어내지 마라.
- 막대그래프의 눈금(55/70/85/100/115...)은 값이 아니다. 막대 옆/아래에 인쇄된 숫자가 값이다.
- 괄호 안 범위(예: "35.7~43.7")는 표준 범위이지 측정값이 아니다.
- JSON만 반환한다. 설명 문장 금지."""

# 실제 결과지 라벨을 그대로 명시 — 단일 양식 전제라 가능한 최적화
USER_PROMPT = """이 인바디 결과지에서 아래 항목을 읽어 JSON으로 반환해줘.

[헤더]
- measured_at: 검사일시의 날짜만 YYYY-MM-DD
- age: 나이
- gender: 성별. 남성이면 "MALE", 여성이면 "FEMALE"
- height: 신장(cm), 숫자만

[체성분분석]
- total_body_water: 체수분 (L)
- protein: 단백질 (kg)
- minerals: 무기질 (kg)
- body_fat_mass: 체지방 / 체지방량 (kg)
- weight: 체중 (kg)
- fat_free_mass: 제지방량 (kg)

[골격근·지방분석]
- skeletal_muscle_mass: 골격근량 (kg)

[비만분석]
- bmi: BMI (kg/m²)
- body_fat_percentage: 체지방률 (%)

🔴 아래 **두 표는 생김새가 같습니다** (5행 막대 + kg + %). 결과지에서 위아래로
   따로 있고, **제목으로만 구분됩니다.** 어느 표에서 읽었는지 반드시 확인하세요 —
   섞으면 근육량 자리에 체지방 값이 들어갑니다 (실측 사고, 2026-08-20).

[부위별근육분석] ← 제목에 «근육»
- segments.<부위>.lean_mass       : (kg) 값
- segments.<부위>.lean_percentage : (%) 값 — 표준 대비 비율
  부위 키: RIGHT_ARM(오른팔) LEFT_ARM(왼팔) TRUNK(몸통) RIGHT_LEG(오른다리) LEFT_LEG(왼다리)

[부위별체지방분석] ← 제목에 «체지방»
- segments.<부위>.fat_mass       : 괄호 안 (kg) 값
- segments.<부위>.fat_percentage : 막대 우측 (%) 값

⚠️ **두 표를 다 찾지 못했다면, 못 찾은 쪽은 null 로 두세요.** 한 표의 값을
   양쪽에 넣지 마세요 — 같은 부위에서 lean_percentage 와 fat_percentage 가
   똑같은 숫자로 나왔다면 그건 표를 잘못 읽은 것입니다.
⚠️ 결과지에 [부위별체지방분석] 표가 아예 없는 기종도 있습니다. 그때
   fat_mass·fat_percentage 는 전부 null 이고, 근육 표 값을 대신 넣지 않습니다.

반환 형식 (아래는 자리·타입만 보여주는 골격입니다 — **이 숫자를 그대로 쓰지 마세요.**
결과지에서 실제로 읽은 값만 채우고, 안 보이거나 확실하지 않은 항목은 null 로 두세요):
{
  "measured_at": "<YYYY-MM-DD 또는 null>",
  "age": <정수 또는 null>,
  "gender": "<MALE|FEMALE 또는 null>",
  "height": <숫자 또는 null>,
  "weight": <숫자 또는 null>,
  "bmi": <숫자 또는 null>,
  "body_fat_mass": <숫자 또는 null>,
  "body_fat_percentage": <숫자 또는 null>,
  "skeletal_muscle_mass": <숫자 또는 null>,
  "fat_free_mass": <숫자 또는 null>,
  "total_body_water": <숫자 또는 null>,
  "protein": <숫자 또는 null>,
  "minerals": <숫자 또는 null>,
  "segments": {
    "RIGHT_ARM": {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "LEFT_ARM":  {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "TRUNK":     {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "RIGHT_LEG": {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "LEFT_LEG":  {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>}
  }
}"""

#: 2차 호출 — 부위별 두 표 전용. ocr._segment_tiles 가 만든 확대 조각과 함께 쓴다.
#  ⚠️ 조각에는 다른 표도 걸쳐 보이므로 «제목으로 구분» 규칙은 여기서도 뺄 수 없다.
SEGMENT_USER_PROMPT = """이 이미지들은 **한 장의 인바디 결과지를 겹치게 조각낸 확대본**입니다.
조각들 중에서 [부위별근육분석] 표와 [부위별체지방분석] 표를 찾아 읽어주세요.
같은 표가 여러 조각에 걸쳐 보일 수 있습니다 — 숫자가 가장 선명하게 보이는 조각에서 읽으세요.

🔴 두 표는 생김새가 같습니다 (5행 막대 + kg + %). **제목으로만 구분됩니다.**
[부위별근육분석] ← 제목에 «근육» → lean_mass(kg), lean_percentage(%)
[부위별체지방분석] ← 제목에 «체지방» → fat_mass(괄호 안 kg), fat_percentage(막대 우측 %)
행 순서: 오른팔 → 왼팔 → 몸통 → 오른다리 → 왼다리

못 찾은 표의 값은 null 로 두세요. 한 표의 값을 양쪽에 넣지 마세요 — 같은 부위에서
lean_percentage 와 fat_percentage 가 똑같으면 표를 잘못 읽은 것입니다.

JSON만 반환 (아래는 자리·타입 골격입니다 — 결과지에서 실제로 읽은 값만 채우세요):
{
  "segments": {
    "RIGHT_ARM": {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "LEFT_ARM":  {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "TRUNK":     {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "RIGHT_LEG": {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>},
    "LEFT_LEG":  {"lean_mass": <숫자|null>, "lean_percentage": <숫자|null>, "fat_mass": <숫자|null>, "fat_percentage": <숫자|null>}
  }
}"""
