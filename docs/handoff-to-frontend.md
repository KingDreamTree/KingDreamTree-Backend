# 프론트 연동 가이드 — B파트 (F07~F12)

> **작성**: B파트 · 2026-08-14 · **Base URL**: `/api/v1`
> **정확한 요청/응답 스키마는 `/docs` (Swagger)** 를 보세요. 이 문서는 **화면을
> 만들 때 걸려 넘어지는 것**만 모았습니다.
>
> ⚠️ §7 코치 챗은 **아직 확정 전**입니다. 나머지는 확정입니다.

---

## 0. 먼저 — 이전 안내와 달라진 것 5가지

예전 명세를 보고 구현했다면 여기부터 확인하세요. **에러 없이 화면만 빈** 종류라 늦게 발견됩니다.

| 바뀐 것 | 이전 | 지금 |
|---|---|---|
| **부위 크롭 이미지** | `reference_crop_url` / `user_crop_url` | **없습니다.** 크롭 파일을 만들지 않습니다 → 부위 시각화는 `GET /sessions/{id}/segmentation` 의 맵 + 팔레트로 |
| **루틴 Day 번호** | `day_number` 1~28 | **`day_order` 1~N + `cycle_no` 1~4** (N = 주당 운동 일수) |
| **운동 중량** | `weight_kg` | **없습니다.** `rir` + `note`("2회 정도 여유가 남는 무게") |
| **진단 잡 id** | 부위마다 다른 `job_id` | `part_jobs[]` 의 **job_id 가 전부 같습니다.** `part_job_id` 하나만 쓰세요 |
| **종합 진단 잡** | `overall_job_id` | **항상 `null`** — 부위 진단이 끝난 뒤에 서버가 만듭니다. 진행은 `/analysis/progress` 로 |

---

## 1. 공통 규약

**모든 요청에 `X-User-Id` 헤더**가 필요합니다 (`POST /users`, `GET /body-parts` 제외).

**에러는 항상 이 모양입니다** — `error.code` 로 분기하세요.

```json
{ "error": { "code": "INSUFFICIENT_PARTS", "message": "사용자에게 보여줄 문구",
             "detail": { "count": 2, "min_required": 3 } } }
```

⚠️ **소유권 불일치도 404 입니다** (403 아님). "남의 세션 id 를 넣었다"와 "없는
id 를 넣었다"를 구분하지 않습니다 — 403 은 그 id 가 존재한다는 걸 알려주니까요.

**무거운 작업은 202 + `job_id`** → `GET /jobs/{job_id}` 폴링 (진단·루틴 2초 간격).

⚠️ `job.status`(실행 상태)와 도메인 `status`(화면에 써도 되는지)는 **다릅니다.**
잡이 `DONE` 이어도 진단이 `FAILED` 일 수 있습니다.

---

## 2. F07 인바디 — 선택 입력입니다

`POST /sessions/{id}/inbody` (202) → `GET /inbody/{id}` 로 확인 화면

**⚠️ 여기서 사용자를 막지 마세요.** 인바디가 없어도 분석·루틴이 전부 돕니다.
**「건너뛰기」 버튼이 항상 보여야 합니다.**

**확인 화면에서 꼭 해야 할 것**

- `validation.checks` 중 `level` 이 `WARN`/`ERROR` 인 필드만 강조하세요. 전부
  똑같이 보여주면 사용자가 대충 넘깁니다.
- ⚠️ **경고는 "이 칸이 틀렸다"가 아니라 "이 값들이 서로 안 맞는다"** 는 뜻입니다.
  체지방량을 잘못 읽으면 체중·제지방량·체지방률 **3개 칸에 동시에** 경고가 뜹니다.
  칸 하나에 고립시켜 보여주면 사용자가 엉뚱한 값을 고칩니다.
- **경고가 있어도 다음 단계로 갈 수 있어야 합니다.** 실제로 좌우 비대칭이 심한
  사람도 있어서 막지 않고 알려주기만 합니다.
- `smi` 는 **DB 컬럼이 아니라 파생값**입니다. 사용자가 값을 고치면 즉시 다시 계산돼 내려옵니다. 읽기 전용으로 표시하세요.
- OCR 이 실패해도(`status: FAILED`) **수기 입력으로 진행 가능**합니다.

---

## 3. F08·F09 진단

```
POST /sessions/{id}/analysis        → 202 (부위 진단 큐잉)
GET  /sessions/{id}/analysis/progress → 로딩 화면 ("완료 3/9")
GET  /sessions/{id}/analysis        → 결과
```

**시작 응답**

```json
{ "part_job_id": "...", "overall_job_id": null,
  "part_count": 9, "class_names": ["Torso", "..."],
  "part_jobs": [{"job_id": "...", "class_name": "Torso"}],
  "reused": false }
```

- `reused: true` → 이미 진행 중이거나 완료된 분석입니다. **새로 만들지 않았습니다.**
  이때 `part_job_id` 가 `null` 일 수 있으니 바로 `GET /analysis` 를 부르세요.
- 다시 돌리려면 `?force=true` — ⚠️ **LLM 을 다시 호출해 요금이 또 발생합니다.**

**진행률** — `part.total` 은 잡 개수가 아니라 **진단 행 수**입니다. `completed: true` 가 되면 결과 화면으로.

**결과 — 화면 만들 때 주의 3가지**

```json
{ "overall": { "similarity_score": 68, "score_source": "RULE",
               "score_rationale": "...", "summary": "...",
               "priority_parts": ["Left_Upper_Arm"], "strengths": [], "cautions": [] },
  "parts": [ { "class_name": "Left_Upper_Arm", "name_ko": "왼팔 상완",
               "color_hex": "#F76707", "gap_level": "MODERATE", "priority": 2,
               "confidence": "HIGH", "blocked_reason": null,
               "differences": ["..."], "assessment": "...", "status": "DONE" } ],
  "excluded": [ { "class_name": "Left_Lower_Leg", "reason": "TOO_SMALL", "side": "USER" } ],
  "inbody_id": "...", "disclaimer": "..." }
```

1. **부위 일부가 `FAILED` 여도 200 입니다.** 실패 부위는 빼고 그리세요.
2. **`gap_level: null` + `blocked_reason` 은 실패가 아닙니다.** "옷에 가려 판단
   못 했다"고 AI 가 스스로 보고한 겁니다. "확인하지 못했어요"로 표시하고, 실패와
   구분해 주세요.
3. **`excluded`** 는 애초에 비교 대상에 못 든 부위입니다. "왼팔은 왜 결과가 없지?"
   에 답하려면 이걸 화면에 노출해야 합니다.

⚠️ **`disclaimer` 는 반드시 노출**하세요. 프론트 구현에 맡기면 빠집니다.

**422 `INSUFFICIENT_PARTS`** — 비교 가능 부위가 3개 미만. `detail.excluded` 에
빠진 부위와 사유가 있으니 **재촬영 안내**로 연결하세요.

---

## 4. F10 루틴 — Day 1~28 이 아닙니다

**모델을 먼저 이해해야 합니다.**

```
루틴 1개 = Day 1..N  (N = 사용자가 고른 주당 운동 일수 1~7)
이 N일을 4주기 반복 → 전체 N × 4회

  예) N=3 이면 Day1·Day2·Day3 이 전부. 이걸 4번 돈다 (총 12회)
```

- **요일이 아닙니다.** 언제 수행할지는 사용자 자유입니다.
- **휴식일 행이 없습니다.** 운동하는 날만 옵니다.
- 주차 토글 UI → **주기(1~4) 토글**로. 모든 주기의 내용이 같으므로 프론트는
  Day 1..N 목록 하나만 그리면 됩니다.

```
POST /sessions/{id}/routines          { "exercise_days_per_week": 3 } → 202
GET  /sessions/{id}/routines/active   활성 루틴 전체 + 진행 상태
GET  /sessions/{id}/routines/today    오늘 해야 할 Day
GET  /routines/{id}/days/{day_order}  Day 상세
GET  /sessions/{id}/routines          버전 이력
```

**운동 항목**

```json
{ "order_index": 1, "name": "벤치프레스", "exercise_ref": "exr_...",
  "image_url": "https://cdn.exercisedb.dev/...",
  "exercise_kind": "STRENGTH", "muscle_group": "가슴",
  "sets": 4, "reps": 10, "rest_sec": 90,
  "rir": 2, "note": "10회를 마쳤을 때 2회 정도 여유가 남는 무게로 하세요.",
  "boosted_by": "Torso" }
```

- **`image_url` 로 운동 이미지를 띄울 수 있습니다.**
- **중량(kg) 은 없습니다.** 사진·인바디로 적정 무게를 추정할 방법이 없어서
  추정하지 않기로 했습니다. `note` 를 그대로 보여주세요.
- **`boosted_by`** 가 있으면 "왼팔이 부족해서 세트를 늘렸어요" 같은 배지를 붙일 수 있습니다.
- `exercise_kind: "CARDIO"` 면 `sets`/`reps` 대신 **`duration_min`** 을 씁니다.

**진행 상태**

```json
{ "completed_count": 4, "total_count": 12, "cycle_no": 2,
  "next_day_order": 2, "is_completed": false, "percent": 33,
  "day_source": "COUNT" }
```

⚠️ **날짜가 아니라 완료 횟수 기준**입니다. 하루 밀려도 순서가 안 깨집니다.
`day_source` 는 나중에 날짜 기준으로 바뀔 수 있어 어느 방식인지 알려주는 필드입니다.

**운동 일수 변경** — 같은 엔드포인트에 다른 일수를 보내면 **새 버전**이 생깁니다.
기존 버전과 수행 기록은 그대로 남습니다.
⚠️ 새 버전이 완료되기 전까지 **이전 루틴이 계속 활성**입니다. 생성 중에 화면이
비지 않게 `status` 를 보고 처리하세요.

**모드 안내** — `notice` 에 "왜 이렇게 구성했는지"가 들어옵니다 (예: 감량 병행
루틴이면 유산소를 넣은 이유). 있으면 보여주세요.

---

## 5. F11 오늘의 루틴

`GET /sessions/{id}/routines/today` → 이번 주기의 **다음 미완료 Day**

⚠️ "오늘 요일에 해당하는 Day" 가 아닙니다. 사용자가 하루 건너뛰어도 순서대로 이어집니다.

활성 루틴이 없으면 **404** 입니다 — 루틴 생성 화면으로 보내세요.

---

## 6. F12 수행 기록

```
POST /sessions/{id}/workout-logs   { "day_order": 1, "cycle_no": 1, "feedback_text": "..." }
GET  /sessions/{id}/workout-logs   수행 기록
GET  /sessions/{id}/revisions      "왜 루틴이 바뀌었는지"
```

- `feedback_text` 는 **선택**입니다. 없으면 완료만 기록되고 AI 를 부르지 않습니다.
- 있으면 응답에 `job_id` 가 옵니다 → 폴링해서 반영 결과를 확인.
- 같은 주기의 같은 Day 를 두 번 완료하면 **에러**입니다 (중복 방지).
- `GET /revisions` 의 각 항목에는 원본 `feedback_text` 가 조인돼 옵니다.

---

## 7. 🟡 코치 챗 — 확정 전입니다

> ⚠️ **이 절은 바뀔 수 있습니다.** 구현은 돼 있지만 UX 확정 전이라,
> 화면 작업은 확정 통보 후에 시작하세요. 미리 구조만 공유합니다.

피드백을 **대화로** 받는 방식입니다. 한 번에 바꾸지 않고 되묻습니다
(예: "무릎이 아파요" → "어느 정도로 아프신가요?" → 확정 카드).

```
POST /sessions/{id}/coach-chat        대화 한 턴
POST /sessions/{id}/coach-chat/apply  확정된 변경을 실제 루틴에 적용
```

- 응답의 `messages` 를 **그대로 다음 요청에 다시 실어** 보냅니다 (서버가 대화를 저장하지 않음).
- `finalized` 가 `null` 이 아니면 **확정 카드**입니다 → 사용자 확인 후 `apply` 호출.
- `turn` / `max_turns` 로 대화 길이를 표시할 수 있습니다.
- `tool_events` 에 "금기 등록됨" 같은 이벤트가 옵니다.

---

## 8. 화면 만들 때 자주 묻는 것

**Q. 로딩이 얼마나 걸리나요?**
세그멘테이션 수십 초, 인바디 인식 수 초, 진단 10~20초, 루틴 생성 10초 정도입니다.
전부 202 + 폴링이니 **로딩 화면에 진행률을 보여주세요** (진단은 `/analysis/progress`).

**Q. 사용자가 새로고침하면요?**
`GET /sessions/active` 의 `steps` 를 보면 어느 화면으로 보낼지 알 수 있습니다.
분석·루틴은 서버가 중복 호출을 막으니 다시 눌러도 요금이 두 배가 되지 않습니다.

**Q. 실패하면요?**
`job.error` 에 사용자에게 보여줄 문구가 들어 있습니다. **그대로 표시해도 됩니다** —
스택 트레이스나 내부 경로는 서버가 걸러냅니다.

**Q. 이미지가 안 보여요**
Storage 는 전부 private 이라 **signed URL** 이 필요합니다
(`POST /storage/signed-urls`, 최대 30개). 운동 이미지(`image_url`)는 외부 CDN 이라 그대로 쓰면 됩니다.

---

## 9. 아직 없는 것

| | 상태 |
|---|---|
| 로그인 | 🔴 없음 — `X-User-Id` 헤더로 식별. **로컬 스토리지가 지워지면 복구 수단이 없습니다.** 사용자 안내 필요 |
| 푸시 알림 | 🔴 없음 |
| 운동 통계·히스토리 대시보드 | 🔴 없음 |
| 통증 부위 운동 자동 제외 | 🟡 기록·금기까지만. 실제 교체는 정책 확정 후 |
