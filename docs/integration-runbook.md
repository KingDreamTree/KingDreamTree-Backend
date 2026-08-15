# 실연동 런북 — Swagger로 하는 법

> 2026-08-14 21:25 콜용. **터미널은 2개만, 나머지는 브라우저 클릭.**
> 확인할 항목은 `integration-checklist.md` 참고.

---

## 역할 — 한 줄

```
A    : RunPod 세그멘테이션 (사진 → 맵 + DB 행)   ← GPU 필요
나   : 그 뒤 전부 (진단 → 루틴 → 대화)          ← 브라우저로
```

세그는 A가 돌린다. 나는 **A가 끝내고 준 `session_id`로 Swagger에서 클릭**한다.

---

## STEP 0 — 21:15, 준비 (5분)

### 0-1. `.env` 딱 한 줄 고치기 ⚠️

`.env` 17번 줄이 지금 `USE_MOCK=true`다. **`false`로 바꾼다.**

```
USE_MOCK=false
```

이거 안 바꾸면 전부 가짜 결과가 나온다. 제일 자주 빠뜨리는 지점.

### 0-2. 터미널 2개

**터미널 A — API 서버** (Swagger가 여기서 뜬다)
```bash
cd ~/Desktop/KingDreamTree-Backend && git pull && uvicorn app.main:app --reload --port 8000
```

**터미널 B — 워커** (실제 일하는 곳. 화면공유는 이걸)
```bash
cd ~/Desktop/KingDreamTree-Backend && python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH
# 맥(Apple Silicon)이면 세그까지 로컬에서 된다: python -m app.worker.run --all
```

> 세그 워커(`SEG_*`)는 안 켠다 — A가 RunPod에서 돌린다.

### 0-3. 브라우저 열기

```
http://localhost:8000/docs
```

---

## STEP 1 — A 기다리는 동안

A가 긴팔 사진으로 세그를 돌린다. 나는 결과만 같이 본다.

A가 끝나면 **두 개를 받아 적는다**:

```
user_id     = ____________________
session_id  = ____________________
```

---

## STEP 2 — Swagger 클릭 (여기부터 내 차례)

### 공통 — 모든 요청에 `X-User-Id` 넣기

로그인이 없어서 이 헤더로 사용자를 식별한다. 각 엔드포인트를 열면
파라미터에 `X-User-Id` 칸이 있다. **거기에 A가 준 `user_id`를 넣는다.**

각 단계 공통 조작:
```
① 엔드포인트 줄 클릭해서 펼치기
② [Try it out] 버튼
③ X-User-Id + session_id 채우기
④ [Execute]
⑤ 아래 Response 확인
```

---

### 2-1. 진단 시작

```
POST /api/v1/sessions/{session_id}/analysis
```

Execute 하면 `job_id`가 나오고 **끝이 아니다** — 실제 작업은 터미널 B(워커)에서
돈다. 수십 초 걸린다. **터미널 B를 본다:**

```
비교 대상 N부위: ...          ← ⭐ 긴팔인데 팔이 있으면 병합 성공
inbody: NONE                  ← 인바디 없이도 진행되는지
종합 진단 잡 ... (신규=True)   ← 자동 등록되는지
```

### 2-2. 진단 결과 보기

```
GET /api/v1/sessions/{session_id}/analysis
```

볼 것:
- `similarity_score` + `score_source: "RULE"`
- `summary`가 점수와 안 어긋나는지 (40점인데 "거의 비슷해요" 아닌지)
- 옷 가린 부위에 `confidence: LOW` 또는 `blocked_reason`이 있는지

> 아직 안 끝났으면 `GET .../analysis/progress`로 진행률을 볼 수 있다.

### 2-3. 루틴 생성

```
POST /api/v1/sessions/{session_id}/routines
```

Request body에 이것만:
```json
{ "exercise_days_per_week": 3 }
```

터미널 B에서 `mode` / `boosts` / `selection` 확인.

### 2-4. 루틴 보기

```
GET /api/v1/sessions/{session_id}/routines/active
```

볼 것: Day 3개 / 운동마다 `exercise_ref` 있음(지어낸 운동 0) /
진단 부위에 `boosted_by` 붙어 있음.

### 2-5. (여유되면) 코치 대화

```
POST /api/v1/sessions/{session_id}/coach-chat
```

Request body:
```json
{ "messages": [{ "role": "user", "content": "스쿼트 할 때 무릎이 아팠어" }] }
```

응답의 `reply`를 읽고, 이어서 대화하려면 **응답의 `messages`를 그대로 복사해
뒤에 새 발화를 붙여서** 다시 Execute.

---

## STEP 3 — 실측값 3개 적기 (5분)

체크리스트 문서 하단 표에 기록한다. 오늘의 수확이다.

| 적을 것 | 어디서 |
|---|---|
| `clothing_ratio` 실제 값 | 워커 로그 / DB `clothing_pixel_count` |
| `framing_bias` 중앙값·same_direction | 워커 로그 (**실사진 첫 기록**) |
| `valid_comparable` 수 | A의 `SEG_USER` job.result |

이게 "임계값은 로그 쌓인 뒤에 정한다"고 미뤄온 그 로그의 첫 줄이다.

---

## 막혔을 때

| 증상 | 1차 확인 |
|---|---|
| Response에 mock 같은 값 | `.env` `USE_MOCK`이 아직 `true` |
| 아무 반응 없음 | 터미널 B(워커)가 살아있나 |
| `INSUFFICIENT_PARTS` | 비교 부위 3개 미만 = **병합이 안 먹음** → A와 확인 |
| 404 (파일 없음) | DB 행은 있는데 Storage에 파일이 없음 → A쪽 업로드 |
| 401 / 403 | `X-User-Id` 안 넣었거나 다른 사용자 id |

---

## 한 장 요약

```
21:15  .env USE_MOCK=false        ← 제일 중요
       터미널 A: uvicorn
       터미널 B: worker
       브라우저: localhost:8000/docs

21:25  A가 세그 → session_id 받기

       Swagger에서 순서대로 Execute:
         POST .../analysis        → 터미널 B 보면서 대기 ⭐
         GET  .../analysis
         POST .../routines        { "exercise_days_per_week": 3 }
         GET  .../routines/active
         (여유되면 coach-chat)

       실측값 3개 기록 → 끝
```
