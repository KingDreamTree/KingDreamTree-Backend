# 혼자 전 구간 테스트하기 (2026-08-14 이후)

> 로컬 GPU 로 세그까지 돌 수 있게 되면서 **A 없이 처음부터 끝까지** 가능해졌다.
> 셋업은 `docs/local-gpu-setup.md`.

---

## 방법 A — 명령 한 줄 (가장 빠름, 사진 업로드 없음)

```bash
python scripts/smoke_full_flow.py --live-llm
```

사용자 생성 → 사진·세그 → 인바디 OCR → 진단 → 루틴 → 피드백 → 새 버전까지
관통하고, 끝나면 DB·Storage 를 **전부 자동 정리**한다.

| 옵션 | 용도 |
|---|---|
| (없음) | mock LLM — 무료·빠름. 배선 확인 |
| `--live-llm` | 실제 GPT-4o — 진단문 품질 확인 |
| `--no-inbody` | 인바디 없는 경로 |

⚠️ 세그멘테이션은 **A 데이터 모양을 흉내낸 것**이다. 실제 추론 정합은 방법 B 로.

---

## 방법 B — 실제 사진으로 전 구간 (로컬 GPU)

### 1. 터미널 3개

```bash
# 터미널 1 — 세그 워커 (로컬 GPU, 모델 1.6GB 로드)
python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
```
```bash
# 터미널 2 — LLM 워커
python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH
```
```bash
# 터미널 3 — API 서버
uvicorn app.main:app --reload --port 8000
```

> 세그와 LLM 워커를 **분리**하는 이유: 모델 1.6GB 를 LLM 워커가 들고 있을
> 이유가 없다 (`app/worker/run.py` 주석).

### 2. 포즈 점수 만들기 — 사진 업로드에 필수

사진 업로드는 `pose_landmarks`(MediaPipe 33개) 를 **폼 필드로 요구**한다.
서버는 MediaPipe 를 돌리지 않는다 — 측정은 프론트, 정책은 서버
(`app/services/pose.py` 모듈 주석).

브라우저에서 열어 값을 얻는다:

```
web/pose-live.html      웹캠으로 실시간 판정 + 셔터
web/score-photos.html   찍어둔 사진에 점수 매기기
```

여기서 나오는 `pose_landmarks` JSON · `pose_similarity` · `framing_score` 를
복사해 Swagger 폼에 넣는다.

### 3. Swagger 에서 순서대로

```
http://localhost:8000/docs
```

모든 요청에 **`X-User-Id` 헤더** 필요 (로그인 없음).

```
POST /api/v1/users                                   → user_id 발급
POST /api/v1/sessions                                → session_id 발급
POST /api/v1/sessions/{id}/photos/reference          레퍼런스 + 포즈값
POST /api/v1/sessions/{id}/photos/user               사용자 + 포즈값
     → 터미널 1(세그 워커)에서 추론 진행 (~5초)
POST /api/v1/sessions/{id}/inbody                    (선택) 인바디 결과지
POST /api/v1/sessions/{id}/analysis                  진단 시작
     → 터미널 2(LLM 워커)를 본다
GET  /api/v1/sessions/{id}/analysis                  진단 결과
POST /api/v1/sessions/{id}/routines                  { "exercise_days_per_week": 3 }
GET  /api/v1/sessions/{id}/routines/active           루틴 확인
POST /api/v1/sessions/{id}/workout-logs              완료 + 피드백
POST /api/v1/sessions/{id}/coach-chat                코치 대화
```

---

## 무엇을 볼 것인가

### 세그 워커 터미널

```
비교 대상 N부위: ...        ← 9/9 나오면 좋은 사진
clothing=...                ← 옷 병합이 실제로 흡수했는지
```

### LLM 워커 터미널

```
inbody: NONE | USED         ← 인바디 없이도 진행되는지
종합 진단 잡 ... (신규=True) ← 자동 등록
```

### 진단 결과 (`GET /analysis`)

2026-08-14 수정분이 반영됐는지 확인할 지점:

| 볼 것 | 기대 |
|---|---|
| `score_source` | `"RULE"` — LLM 이 점수를 만들지 않음 |
| `summary` | 크기 수치(%·cm) 없음. 각도로 설명될 차이를 격차로 안 잡음 |
| 억지 칭찬 | 전 부위 격차가 크면 **칭찬 없이** 상태 서술로 |
| `strengths` | 근거 없으면 **빈 배열** |
| `blocked_reason` + 인바디 없음 | `gap_level` 이 `null` 로 강등됐는지 |
| 분석 안 된 부위 | `summary`·`strengths` 에 **안 나와야** 함 |

### 루틴 (`GET /routines/active`)

| 볼 것 | 기대 |
|---|---|
| Day 수 | 설정한 일수와 같음 |
| `exercise_ref` | 전부 있음 (지어낸 운동 0) |
| `boosted_by` | 진단 약점 부위에 붙음 |
| 하체 | 진단이 없어도 기본 볼륨 있음 (D10) |

### 피드백 (`POST /workout-logs` 에 통증 문구)

| 볼 것 | 기대 |
|---|---|
| 새 버전 | `version` 이 올라가고 활성 전환 |
| 이전 버전 | Day 가 그대로 보존 |
| 금기 | `analysis_session.contraindications` 에 누적 |

---

## 검증 스크립트 (코드 수정 후 매번)

```bash
python scripts/verify_analysis.py        # F08·F09 파이프라인
python scripts/verify_coach_chat.py      # 코치 대화 도구 검증
python scripts/verify_routine_rules.py   # 루틴 불변식
python scripts/verify_ab_contract.py     # A↔B 계약 (스키마 드리프트)
python scripts/verify_segmap.py <샘플>   # 맵 → 하이라이트
```

---

## 막혔을 때

| 증상 | 확인 |
|---|---|
| mock 같은 결과 | `.env` 의 `USE_MOCK` 이 `false` 인지 |
| 세그가 안 돎 | 터미널 1 살아있나 / `SAPIENS_SIZE=0.4b` 인지 |
| `no kernel image` | torch 가 cu128 빌드인지 (`sm_120` 필요) |
| `INSUFFICIENT_PARTS` | 비교 부위 3개 미만 — 사진을 바꿔야 함 |
| 404 (파일 없음) | DB 행은 있는데 Storage 업로드 실패 |
| 사진 업로드 400 | `pose_landmarks` 형식 — `web/` 데모에서 값 다시 뽑기 |

---

## 좋은 테스트 사진 스펙

```
정면 기립, 머리~발 전체
팔을 몸통에서 15~30도 벌리기   ← 팔/몸통 분리의 핵심
긴팔이면 손등·손목은 노출      ← 옷 병합 BFS 씨앗
하의는 반바지나 붙는 레깅스
배경 단순
```

레퍼런스와 사용자 **둘 다** 이 조건이어야 교집합이 9개 나온다.
