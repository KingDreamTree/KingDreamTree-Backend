# 실연동 런북 — B가 손으로 하는 것

> 체크리스트(`integration-checklist.md`)가 **무엇을 확인하나**라면,
> 이 문서는 **내가 뭘 치나**다. 위에서 아래로 순서대로 따라가면 된다.
> 2026-08-14 21:25 콜용.

---

## 역할 분담 — 한 줄 요약

```
A 가 한다 : RunPod 세그멘테이션 (GPU 필요) — 사진 → 맵 PNG + DB 행
내가 한다 : 그 뒤 전부 — 진단 → 루틴 → 피드백 (GPU 불필요, 내 노트북)
```

내가 A의 세그를 돌리는 게 아니다. **A가 DB에 넣어준 걸 내가 소비**한다.
그래서 내 순서는 "A 끝났다" 신호를 기다렸다가 시작하는 구조다.

---

## STEP 0 — 콜 전 (21:15, 10분 전에)

터미널 1개 열고 프로젝트로 이동.

```bash
cd ~/Desktop/body-analysis-backend
```

### 0-1. 최신 코드

```bash
git checkout feature/routine-api && git pull
```

### 0-2. `.env` 확인 — **실연동이니 mock 끄기**

`.env` 파일 열어서 두 줄 확인:

```
USE_MOCK=false          ← true 면 가짜 결과가 나온다. 반드시 false
OPENAI_API_KEY=sk-...   ← 값이 있어야 진단·루틴이 돈다
```

> ⚠️ 지금 `.env`는 `USE_MOCK=true`다. **고쳐야 한다.**

### 0-3. 계약 검사 미리 1회

```bash
python scripts/verify_ab_contract.py
```

A가 샘플 JSON을 커밋했으면 **4/4**가 나온다. 아직이면 3/4 + "샘플 없음" 스킵.

### 0-4. 워커 2개 띄우기 — **터미널 2개 더**

내 쪽 워커는 잡 큐를 계속 돌면서 대기하는 프로세스다. 미리 켜둬야
A가 세그를 끝내는 순간 바로 이어받는다.

**터미널 2** (LLM 워커 — 진단·루틴·피드백):
```bash
python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH
```

**터미널 3** (API 서버 — 요청을 받는 곳):
```bash
uvicorn app.main:app --reload --port 8000
```

> 세그 워커(`SEG_*`)는 **안 켠다.** 그건 A가 RunPod에서 돌린다.

두 터미널이 에러 없이 떠 있으면 준비 끝. 화면공유는 **터미널 2(워커 로그)**를
띄워두면 된다 — 잡이 들어오고 처리되는 게 여기 흐른다.

---

## STEP 1 — A 차례 (내가 볼 것만)

A가 긴팔 사진을 올리고 RunPod에서 세그를 돌린다. 나는 **결과만 확인**한다.

A가 "세그 끝났다"고 하면 `session_id`를 받아 적는다. 그리고:

```bash
python scripts/verify_segmap.py <A가 준 샘플 폴더>
```

부위별 픽셀 수가 맞는지 본다. **라벨 순서가 뒤바뀌면 여기서 카운트가 어긋난다.**

그다음 A 화면에서 **오버레이 색칠**을 눈으로 본다:
- 몸통이 몸통 색인가
- **좌우가 안 뒤집혔나** ← 좌우를 비슷한 색 계열로 만든 이유가 이 순간

### 옷 병합 확인 — 오늘의 핵심

A에게 DB를 띄워달라고 해서 `body_part_segment` 행을 같이 본다:

| 볼 것 | 통과 기준 |
|---|---|
| 상완 `is_valid` | `true` (긴팔인데 살아남 = 병합 성공) |
| 상완 `clothing_pixel_count` | **0이 아닌 값** |
| 안 가린 부위 | `0` (NULL 아님 — NULL은 병합 미적용) |
| `SEG_USER` job.result | `retake_recommended: false` |

---

## STEP 2 — 내 차례 (여기부터 내가 명령을 친다)

A가 준 `session_id`로 진단을 시작한다. **터미널 1**에서:

### 2-1. 진단 시작

```bash
curl -X POST http://localhost:8000/api/v1/analysis \
  -H "X-User-Id: <A가 준 user_id>" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<A가 준 session_id>"}'
```

> Swagger가 편하면 브라우저에서 `http://localhost:8000/docs` 열고
> `POST /analysis` 눌러도 된다. 같은 일이다.

### 2-2. 터미널 2(워커 로그)를 본다

여기서 실시간으로 흐른다. 볼 것:

```
비교 대상 N부위: ...        ← 몇 부위가 살았나 (긴팔인데 팔이 있으면 병합 성공)
inbody: NONE                ← 인바디 없이도 진행되는지
종합 진단 잡 ... (신규=True) ← VLM_PART 끝나고 자동 등록되는지
```

### 2-3. 진단 결과 확인

```bash
curl http://localhost:8000/api/v1/analysis \
  -H "X-User-Id: <user_id>" | python -m json.tool
```

볼 것:
- `similarity_score` + `score_source: "RULE"`
- `summary`가 점수와 모순 없는지 (40점인데 "거의 비슷해요" 아닌지)
- 옷 가린 부위의 `confidence`가 낮거나 `blocked_reason`이 있는지

### 2-4. 루틴 생성

```bash
curl -X POST http://localhost:8000/api/v1/sessions/<session_id>/routines \
  -H "X-User-Id: <user_id>" \
  -H "Content-Type: application/json" \
  -d '{"exercise_days_per_week":3}'
```

워커 로그에서 `mode` / `boosts` / `selection` 확인. 그다음 조회:

```bash
curl http://localhost:8000/api/v1/sessions/<session_id>/routines/active \
  -H "X-User-Id: <user_id>" | python -m json.tool
```

볼 것: Day 3개, 운동 전부 `exercise_ref` 있음(지어낸 운동 0),
진단 부위에 `boosted_by` 붙어 있음.

### 2-5. (시간 되면) 코치 대화

```bash
curl -X POST http://localhost:8000/api/v1/sessions/<session_id>/coach-chat \
  -H "X-User-Id: <user_id>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"스쿼트 할 때 무릎이 아팠어"}]}'
```

---

## STEP 3 — 기록 (5분)

체크리스트 문서 하단 표에 오늘 나온 **실측값**을 적는다.

| 적을 것 | 어디서 |
|---|---|
| `clothing_ratio` 실제 값 | 진단 프롬프트 로그 또는 DB |
| `framing_bias` 중앙값·same_direction | 워커 로그 (실사진 첫 기록) |
| `valid_comparable` 수 | `SEG_USER` job.result |

이 숫자들이 **"임계값은 로그 쌓인 뒤에 정한다"고 미뤄온 그 로그의 첫 줄**이다.

---

## 막혔을 때

| 증상 | 1차 확인 |
|---|---|
| 워커가 잡을 안 집음 | 터미널 2가 살아있나 / `--kinds`에 그 잡 종류가 있나 |
| `INSUFFICIENT_PARTS` | 비교 가능 부위 3개 미만 — **병합이 안 먹은 것** (A와 확인) |
| 404 (사진·맵 없음) | DB 행은 있는데 Storage 파일이 없음 — A쪽 업로드 확인 |
| 진단이 mock 결과 | `.env`의 `USE_MOCK`이 아직 `true` |
| 좀비 잡 | 워커 재시작하면 `reclaim_stale`이 회수함 |

---

## 한 장 요약

```
21:15  터미널 3개 준비 (1:명령 / 2:워커 / 3:API서버)
       .env USE_MOCK=false 확인 ← 제일 자주 빠뜨림
       verify_ab_contract.py

21:25  콜 시작 — A가 세그 돌림
       나: verify_segmap + 오버레이 눈으로 + 옷 병합 4개 확인

       A가 session_id 줌
       나: POST /analysis → 워커 로그 → GET /analysis
           POST /routines → GET /routines/active
           (여유되면 coach-chat)

       실측값 3개 기록하고 종료
```
