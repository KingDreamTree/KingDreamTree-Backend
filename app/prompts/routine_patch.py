"""F12-a 한 방 피드백 — 도구 정의 + 시스템 프롬프트.

━━ 코치 대화(F12-b)와 같은 계약을 쓴다 (2026-08-14 통일) ━━

이전에는 이 파일이 독자 스키마를 갖고 있었다:

    day_number: 1~28          ← 주기 모델(Day 1..N × 4주기) 이전의 잔재
    new_exercise: {name, ...} ← LLM 이 운동을 **지어낼 수 있었다**

둘 다 실제 적용을 막던 원인이다. 카탈로그에 없는 운동은 exercise_ref 가 없어
routine_day_exercise 에 넣어도 이미지·출처가 없고, day_number 는 존재하지 않는
Day 를 가리킨다. 그래서 "해석만 하고 적용은 안 함" 상태로 남아 있었다.

지금은 `app/prompts/coach_chat.py` 의 도구를 그대로 재사용한다:

    day_order         1..N (주기 내 순서)
    new_exercise_ref  후보 목록 안의 ref — 환각이 구조적으로 불가능

한 방 피드백과 대화 피드백이 **같은 검증·같은 적용 경로**를 타므로,
한쪽만 고쳐져 어긋나는 사고가 사라진다.
"""

from app.prompts.coach_chat import TOOLS as _COACH_TOOLS

#: F12-a 는 대화가 없으므로 finalize_revision 이 필요 없다.
#: 나머지 3개는 coach_chat 과 **완전히 동일한 객체**를 쓴다 — 복사하면 어긋난다.
TOOLS: list[dict] = [t for t in _COACH_TOOLS if t["function"]["name"] != "finalize_revision"]

SYSTEM_PROMPT = """너는 개인 트레이너다. 사용자가 운동을 마치고 남긴 피드백 한 건을
읽고, 다음 운동부터 적용할 루틴 변경을 결정한다.

# 규칙

- 변경이 필요한 부분만 건드린다. 전체 루틴 재생성 금지.
- 통증·부상 언급이 있으면 flag_contraindication 을 **반드시** 호출한다.
  (가벼운 불편이면 WARN, 운동을 멈출 정도면 BLOCK)
- 통증 부위에 부담을 주는 운동은 replace_exercise 로 부담이 적은 것으로 바꾼다.
- replace_exercise 의 new_exercise_ref 는 **반드시 주어진 후보 목록에서** 고른다.
  목록 밖 값은 시스템이 거부하고 변경이 무시된다.
- 바꿀 것이 없으면 아무 도구도 호출하지 않는다. 억지로 바꾸지 마라.
- 모든 reason 은 한국어로, 사용자에게 그대로 보여줄 문장으로 쓴다.

# 하지 말 것

- 중량(kg) 추정 금지. 강도는 세트·횟수·휴식으로만 조정한다.
- 오늘 하지 않은 Day 의 운동을 바꾸지 마라.
- 통증을 참고 계속하라는 취지의 말은 절대 하지 마라."""
