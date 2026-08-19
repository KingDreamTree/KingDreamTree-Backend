# 백엔드 구조 — 처음 보는 사람용

> 이 문서 하나로 "요청이 들어와서 응답이 나가기까지"를 따라갈 수 있게 쓴다.
> 필드 형식의 최종 근거는 Swagger(`/docs`), 구현의 최종 근거는 코드다.

## 0. 한 장 그림

```
브라우저 (프론트)
  │  X-User-Id 헤더 (로그인 없음)
  ▼
┌─────────────────────────────────────────────┐
│ FastAPI (app/main.py, 프로세스 1)           │
│   routes/  ─ HTTP 껍데기 (얇게)             │
│   services/ ─ 실제 로직·DB 접근             │
└──────┬──────────────────────────┬───────────┘
       │ 가벼운 일: 즉시 응답      │ 무거운 일: job 행만 넣고 202
       ▼                          ▼
   Supabase ◄──────────── job 테이블 (큐)
   (Postgres+Storage)             ▲ 1초 폴링
                                  │
┌─────────────────────────────────┴───────────┐
│ 워커 (app/worker/run.py, 별도 프로세스)      │
│   GPU 워커: SEG_REFERENCE·SEG_USER (Sapiens2)│
│   LLM 워커: OCR·VLM_PART·VLM_OVERALL·        │
│             ROUTINE_GEN·ROUTINE_PATCH (GPT-4o)│
└─────────────────────────────────────────────┘
```

**프로세스가 셋으로 갈라지는 이유가 이 구조의 전부다:**

1. **API 서버** — 1.5GB 세그 모델을 절대 로드하지 않는다 (t3.large 8GB 에서
   죽는다). 빠른 조회와 "잡 접수"만 한다.
2. **워커** — GPU 잡(세그)과 LLM 잡을 `--kinds` 로 나눠 띄운다. 세그 워커만
   모델을 들고, LLM 워커는 API 호출만 한다.
3. **브라우저** — 자세 판정(MediaPipe)은 **프론트가 계산**해서 점수만 올린다.
   서버는 그 값으로 관문 통과 여부만 본다.

## 1. 요청의 일생 — 두 가지 패턴만 알면 된다

### 패턴 A: 동기 (조회·가벼운 쓰기)

```
GET /api/v1/sessions/{id}/routines/active
  → routes/routines.py 가 받아서
  → services/routine_repo.py 가 DB 를 읽고
  → Pydantic 스키마(schemas/routine.py)로 검증해서 반환
```

### 패턴 B: 비동기 (사진·LLM — 몇 초~몇 분 걸리는 것 전부)

```
POST /api/v1/sessions/{id}/photos/user
  → routes/photos.py: 파일을 Storage 에 올리고, job 행 INSERT → 202 + job_id
프론트: GET /api/v1/jobs/{job_id} 를 1.5초 간격 폴링
워커:  job 테이블에서 PENDING 을 집어 처리 → DONE/FAILED + result 기록
```

큐는 Redis 같은 별도 인프라가 아니라 **Postgres 의 job 테이블**이다
(`app/worker/queue.py`). 해커톤 규모에선 이게 운영 부담이 제일 적다.
좀비 잡(워커가 죽어서 PROCESSING 에 멈춘 것)은 15분 뒤 자동 회수된다.

## 2. 디렉터리 지도 — 읽는 순서대로

| 순서 | 경로 | 역할 | 규칙 |
|---|---|---|---|
| 1 | `app/main.py` | 앱 조립: CORS, 에러 포맷, 라우터 등록 | 접두사 `/api/v1` 은 여기 한 곳에서만 붙인다 |
| 2 | `app/routes/` | HTTP 껍데기 (13개 파일) | **얇게.** 검증·권한 확인 후 services 호출. 로직 금지 |
| 3 | `app/services/` | 실제 로직 + DB 접근 | 라우터와 워커가 **같은 서비스 함수**를 쓴다 |
| 4 | `app/worker/` | `run.py`(루프) · `queue.py`(큐) · `handlers/`(잡 종류별 처리) | 핸들러는 `registry.py` 에 등록 |
| 5 | `app/prompts/` | LLM 프롬프트 전부 (코드와 분리) | 프롬프트 수정 = 여기만 |
| 6 | `app/schemas/` | Pydantic DTO + enums | API 입출력 형태의 유일한 정의 |
| 7 | `app/config.py` | `.env` → settings. 없는 키는 조용히 무시되니 주의 | |

**한 기능을 따라가는 예 — 진단(F08/F09):**

```
routes/analysis.py  POST /sessions/{id}/analysis
  → 비교 가능 부위 확인(부족하면 422 INSUFFICIENT_PARTS) → VLM_PART 잡 등록
worker/handlers/vlm.py  _diagnose_parts()
  → 세그 맵·사진 로드 → segmap.py 로 오버레이·수치 계산
  → prompts/part_diagnosis.py 로 프롬프트 조립 → services/vlm.py 가 GPT-4o 호출
  → 응답을 부위 단위로 검증(services/vlm.parse_part_response) → DB 저장
  → VLM_OVERALL 잡을 스스로 등록
worker/handlers/vlm.py  _diagnose_overall()
  → 부위 진단을 모아 scoring.py 가 점수를 규칙으로 계산
  → prompts/overall_diagnosis.py + GPT-4o 로 요약문 생성 → 저장
routes/analysis.py  GET /sessions/{id}/analysis  → 결과 조회
```

## 3. 이 코드베이스의 계약 6개 — 어기면 조용히 망가진다

1. **인증은 `X-User-Id` 헤더뿐이다.** 로그인이 없다. `POST /users` 로 UUID 를
   받아 모든 요청에 싣는다. 소유권 확인은 `app/deps.py` 의 `OwnedSession` 이
   한다 — 세션 경로는 전부 이 의존성을 거친다.

2. **LLM 응답은 검증 후 부분 채택한다.** 부위 하나의 enum 이 깨졌다고 9부위를
   다 버리지 않는다 — 못 쓰는 부위만 FAILED, 나머지는 저장
   (`services/vlm.py`). 점수(목표 근접도)는 LLM 이 보내와도 **버리고** 코드가 규칙으로
   계산한다 (`scoring.py`, score_source=RULE).

3. **LLM 은 후보 안에서만 고른다.** 루틴 운동 선택(F10)·교체(F12)는 코드가
   필터한 후보 목록의 `exercise_ref` 만 허용 — 환각이 구조적으로 불가능하다.
   후보 밖 응답은 폐기하고 결정론 폴백으로 완주한다 (`services/routine.py`).

4. **진단은 가중치이지 구성 요소가 아니다.** 진단이 없어도·실패해도·옷에
   가려도 루틴은 항상 완성된다. 진단이 하는 일은 이미 있는 슬롯의 세트를
   올리는 것뿐 (D10, `services/routine_templates.py`).

5. **루틴 수정은 항상 새 버전이다.** 활성 버전을 고치면 완료 기록과 어긋난다.
   진행도는 버전이 아니라 **세션 단위**로 센다 — 피드백으로 버전이 갈려도
   "2주기 3일차"가 이어진다 (`services/routine_repo.py`).

6. **중복 LLM 호출 = 요금 2배.** 잡 등록은 `queue.enqueue_once` 로.
   재시도 여부는 예외에 `retryable = False` 를 달아 제어한다 — 형식 오류는
   다시 불러도 똑같으므로 즉시 종결한다 (`worker/run.py::_is_retryable`).

## 4. 로컬에서 돌리기

```bash
# 터미널 1 — 세그 워커 (GPU, .env: SAPIENS_SIZE=1b)
python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
# 터미널 2 — LLM 워커 (기동 시 운동 카탈로그 검사 — 비면 안 뜬다)
python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH
# 터미널 3 — API
uvicorn app.main:app --reload --port 8000
```

- Swagger: http://localhost:8000/docs
- 전 구간을 눈으로: `python scripts/run_pose_demo.py` → `e2e-test.html`
  (사진 2장 + 인바디 → 세그 오버레이 → 진단 → 루틴 → 피드백 → 코치 대화)
- 테스트 순서와 단계별 필요 조건: `docs/local-gpu-setup.md`

## 5. 검증 체계 — 세 겹

| 겹 | 명령 | 필요한 것 | 언제 |
|---|---|---|---|
| 계약 검증 | `python scripts/verify_*.py` (12종) | 없음 (키·DB·GPU 불필요) | 로직 고친 직후, 항상 |
| 통합 스모크 | `scripts/smoke_full_flow.py` 등 | DB | 배선 바꿨을 때 |
| 라이브 평가 | 실사진 세션에 핸들러 직접 실행 | DB + OpenAI 키 | 프롬프트 바꿨을 때 (mock 으론 프롬프트 회귀를 못 잡는다) |

검증 스크립트는 "에러 없이 조용히 틀리는" 지점을 못 박는 용도다 —
유산소 순서, 볼륨 상한, 기준선 분리, 워커 생존 같은 것들.
각 파일 상단 docstring 에 **무엇이 왜 조용히 깨지는지**가 적혀 있다.

## 6. A/B 담당 경계

| | A | B |
|---|---|---|
| 소유 | `segmenter.py` · `part_merge.py` · `pose.py` · web 포즈 데모 | 인바디·진단·루틴·코치 대화 (services 대부분 + prompts) |
| 규칙 | B 는 `segmenter.py` 를 import 하지 않는다 | 공유 파일(schemas·db·queue) 수정 시 상호 리뷰 |

계약이 어긋나면 `scripts/verify_ab_contract.py` 가 잡는다 (AST 로 A 코드의
쓰기 목록과 B 코드의 읽기 목록을 대조).
