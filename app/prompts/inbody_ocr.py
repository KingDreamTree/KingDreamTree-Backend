"""인바디 결과지 추출 프롬프트 (F07).

⚠️ 별도 OCR 엔진을 쓰지 않고 VLM(GPT-4o vision)이 직접 구조화 JSON을 뽑는다.
   근거는 docs/llm-strategy.md "F07 기술 선택" 절 참고.

기준 양식: InBody570 (docs 샘플 = 데스크탑 inbody_ex.jpg).
단일 양식이 전제이므로 프롬프트에 실제 라벨명을 그대로 박아 추출 정확도를 올린다.
다른 기종이 올라와도 라벨이 대체로 동일해 동작은 하지만, 없는 항목은 null이 된다.
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
- device_type: 우측 상단 대괄호 안 기종명 (예: "InBody570")
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

[부위별근육분석] — (kg)와 (%) **둘 다** 읽는다
- segments.<부위>.lean_mass       : (kg) 값
- segments.<부위>.lean_percentage : (%) 값 — 표준 대비 비율 (예: 90.6)
  부위 키: RIGHT_ARM(오른팔) LEFT_ARM(왼팔) TRUNK(몸통) RIGHT_LEG(오른다리) LEFT_LEG(왼다리)

[부위별체지방분석] — 괄호 안 (kg)와 우측 (%) 둘 다
- segments.<부위>.fat_mass       : 괄호 안 (kg) 값
- segments.<부위>.fat_percentage : 막대 우측 (%) 값 (예: 108.0)

[체중조절]
- weight_control.fat_control_kg    : 지방조절 (부호 포함, 예: -2.5)
- weight_control.muscle_control_kg : 근육조절 (부호 포함, 예: +2.6)

[세포외수분비분석]
- ecw_ratio: 세포외수분비 (예: 0.375)

[연구항목]
- bmr_kcal: 기초대사량 (kcal), 정수
- visceral_fat_level: 내장지방레벨, 정수
- abdominal_fat_ratio: 복부지방률

[인바디점수]
- inbody_score: 100점 만점 점수, 정수

반환 형식:
{
  "device_type": "InBody570",
  "measured_at": "2025-01-21",
  "age": 22,
  "gender": "MALE",
  "height": 170,
  "weight": 63.5,
  "bmi": 22.0,
  "body_fat_mass": 12.0,
  "body_fat_percentage": 18.9,
  "skeletal_muscle_mass": 28.8,
  "fat_free_mass": 51.5,
  "bmr_kcal": 1482,
  "total_body_water": 37.7,
  "protein": 10.3,
  "minerals": 3.52,
  "visceral_fat_level": 5,
  "abdominal_fat_ratio": 0.86,
  "ecw_ratio": 0.375,
  "inbody_score": 75,
  "weight_control": {"fat_control_kg": -2.5, "muscle_control_kg": 2.6},
  "segments": {
    "RIGHT_ARM": {"lean_mass": 2.74, "lean_percentage": 90.6, "fat_mass": 0.6, "fat_percentage": 108.0},
    "LEFT_ARM":  {"lean_mass": 2.72, "lean_percentage": 89.9, "fat_mass": 0.6, "fat_percentage": 112.7},
    "TRUNK":     {"lean_mass": 22.8, "lean_percentage": 94.5, "fat_mass": 6.0, "fat_percentage": 149.4},
    "RIGHT_LEG": {"lean_mass": 7.78, "lean_percentage": 92.6, "fat_mass": 1.9, "fat_percentage": 113.1},
    "LEFT_LEG":  {"lean_mass": 7.78, "lean_percentage": 92.7, "fat_mass": 1.8, "fat_percentage": 111.6}
  }
}"""
