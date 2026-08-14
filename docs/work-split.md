# 백엔드 2인 작업 분담

> 🚫 **이 문서는 담당별 작업서로 분리되었습니다.**
> - 담당 A → **`docs/work-a.md`** (이미지 파이프라인)
> - 담당 B → **`docs/work-b.md`** (LLM 파이프라인)
>
> 각 문서는 자기 것만 읽으면 되도록 협업 규칙까지 포함해 자족적으로 작성돼 있습니다.

| | |
|---|---|
| **최종 수정일** | 2026-08-13 (superseded) |
| **기준 문서** | `docs/db-design-v4.md`, `docs/api-spec-v2.md` |
| **인원** | 담당 A / 담당 B |

---

## 0. 분담 원칙

`CLAUDE.md`의 기존 소유권(`segmenter.py`+`routes/analyze.py` = A, `vlm.py`+`routes/compare.py` = B)을 그대로 확장했습니다.

> **A = 이미지가 들어와서 부위별 크롭이 나올 때까지**
> **B = 크롭·수치가 들어와서 루틴이 나올 때까지**

⚠️ **모듈이 늘었으므로 `CLAUDE.md`의 "모듈 소유권" 줄을 아래 표로 갱신해야 합니다.** 이건 공유 파일 변경이니 둘이 합의한 뒤 한 PR로 처리하세요.

### 경계선 — 두 사람이 코드로 만나는 지점은 딱 하나

```
A가 씀 → body_part_segment 테이블 (+ Storage 크롭 파일)
                    ↓
B가 읽음 → DB SELECT + signed URL 만
```

⚠️ **B는 `segmenter.py`를 절대 import하지 않습니다.** import하는 순간 B가 로컬에서 테스트하려면 Sapiens2 1.5GB를 받아야 하고, A가 함수 시그니처를 바꾸면 B가 깨집니다. **DB만 계약으로 씁니다.**

---

## 1. 파일 소유권

### 🟡 공유 (변경 시 상대 리뷰 필수 · 셀프 머지 금지)

| 파일 | 내용 |
|---|---|
| `app/main.py` | 앱 진입점, 라우터 등록, 에러 핸들러 |
| `app/config.py` | 환경변수, 튜닝 상수 |
| `app/schemas/` | Pydantic DTO — **두 사람 작업의 계약** |
| `app/services/db.py` | Supabase 클라이언트, 공통 쿼리 |
| `app/services/storage.py` | 업로드/삭제/signed URL |
| `app/deps.py` | `X-User-Id` 파싱, 소유권 검증 의존성 |
| `app/worker/queue.py` | 잡 선점/완료/실패 처리 공통 로직 |
| `db/schema.sql` | 스키마 |
| `requirements.txt`, `.env.example` | 의존성·환경변수 |

### 🔵 담당 A — 이미지 파이프라인

| 파일 | 내용 |
|---|---|
| `app/services/segmenter.py` | Sapiens2 로드, 부위 마스크, 크롭, 유효 부위 판정 |
| `app/services/pose.py` | MediaPipe Pose, 스케일 정규화, P/F 점수 계산 |
| `app/routes/users.py` | `POST /users`, `GET/DELETE /users/me` |
| `app/routes/sessions.py` | 세션 CRUD, `GET /sessions/active` (단계 집계) |
| `app/routes/photos.py` | 레퍼런스/사용자 사진 업로드, 세그먼트 조회, `comparable` |
| `app/routes/storage.py` | `POST /storage/signed-urls` |
| `app/routes/jobs.py` | `GET /jobs/{id}`, `GET /sessions/{id}/jobs` |
| `app/routes/body_parts.py` | `GET /body-parts` |
| `app/worker/handlers/seg.py` | `SEG_REFERENCE`, `SEG_USER` |
| `scripts/seed_body_parts.py` | `body_part` seed |

### 🟢 담당 B — LLM 파이프라인

| 파일 | 내용 |
|---|---|
| `app/services/ocr.py` | 인바디 결과지 추출 + 검증 |
| `app/services/vlm.py` | 부위별 비교 진단, 종합 진단 |
| `app/services/routine.py` | 루틴 생성, 피드백 패치 |
| `app/prompts/` | 프롬프트 템플릿 전부 |
| `app/routes/inbody.py` | 인바디 업로드/조회/수정/삭제 |
| `app/routes/analysis.py` | `POST /analysis`, progress, 결과 조회 |
| `app/routes/routines.py` | 루틴 생성/조회/오늘의 루틴 |
| `app/routes/workout_logs.py` | 운동 기록, 피드백, revisions |
| `app/worker/handlers/ocr.py` | `OCR_INBODY` |
| `app/worker/handlers/vlm.py` | `VLM_PART`, `VLM_OVERALL` |
| `app/worker/handlers/routine.py` | `ROUTINE_GEN`, `ROUTINE_PATCH` |

---

## 2. 단계별 순서

### Phase 0 — 둘이 같이 (페어, 반나절) ⚠️ 이걸 안 하면 뒤가 다 꼬입니다

| # | 작업 | 산출물 |
|---|---|---|
| 0-1 | `schema.sql` 확정 → Supabase 콘솔 반영 | 15개 테이블 + 인덱스 + RLS 켜기(정책 없음) |
| 0-2 | `app/schemas/` DTO 골격 | API 명세의 요청/응답 전부 |
| 0-3 | `db.py`, `storage.py`, `deps.py`, `config.py` | 소유권 검증 의존성 포함 |
| 0-4 | `worker/queue.py` + 잡 등록/폴링 | `GET /jobs/{id}`가 도는 상태 |
| 0-5 | `requirements.txt`, `.env.example` | torch **CPU 빌드**, 버전 전부 `==` 고정 |

> 이 5개는 전부 🟡 공유 파일입니다. **나중에 각자 만들면 100% 충돌합니다.** 한 브랜치에서 같이 작업하고 한 PR로 머지하세요.

### Phase 1 — 병렬 (여기부터 갈라짐)

| A | B |
|---|---|
| Sapiens2 실제 클래스명 확인 → seed 확정 ⚠️ **최우선** | OCR 기술 선택 후 인바디 추출 구현 |
| `POST /photos/reference` + MediaPipe 동기 추출 | 검증 로직 (범위 / 항등식 / 좌우 대칭) |
| `SEG_REFERENCE` 워커 | `POST /inbody`, `PATCH /inbody` |
| `POST /photos/user` + 포즈 재검증 | 프롬프트 v1 작성 |
| `SEG_USER` 워커 | |
| signed URL, jobs, sessions, users 라우터 | |

⚠️ **A의 "Sapiens2 클래스명 확인"은 다른 모든 일보다 먼저입니다.** `body_part` seed가 확정돼야 B가 프롬프트에 넣을 부위 목록이 정해집니다. 실제 추론 결과 라벨을 찍어서 공유하세요. (미확정 #2)

⚠️ **B는 A를 기다리지 않습니다.** Phase 0 직후 `scripts/seed_test_data.py`로 더미 데이터를 만드세요:
- `test_` 접두사 세션 1개
- 손으로 자른 부위별 크롭 PNG 3~9장을 `body-parts` 버킷에 업로드
- 대응하는 `body_part_segment` 행 삽입

이러면 B는 A의 세그멘테이션 없이 VLM/루틴을 끝까지 개발·테스트할 수 있습니다. **이 스크립트는 B가 만들되 A와 함께 형식을 확인하세요** — 여기서 어긋나면 통합 때 터집니다.

### Phase 2 — 병렬

| A | B |
|---|---|
| `comparable` 교집합 계산 | `VLM_PART` / `VLM_OVERALL` 워커 |
| `GET /sessions/active` 단계 집계 | `POST /analysis` + progress |
| 사진 교체(upsert) + Storage 고아 파일 정리 | `ROUTINE_GEN` + 28일 저장 |
| `DELETE /users/me` prefix 삭제 | 루틴 조회 API 전부 |

### Phase 3 — 병렬

| A | B |
|---|---|
| EC2 배포 스크립트, tmux 세팅 | `workout_log` + `ROUTINE_PATCH` |
| Sapiens2 첫 실행 다운로드 확인 | `routine_revision` + 금기 누적 |
| 워커 동시성/메모리 튜닝 | 안전 처리(통증 피드백) |

### Phase 4 — 둘이 같이

전체 플로우 통합 테스트 (`POST /users` → 피드백 반영까지 한 번에), 프론트 연동, 임계값 튜닝.

---

## 3. 담당 A 주의사항

### 🔴 치명적

- **Sapiens2 가중치를 절대 커밋하지 마세요.** `*.pt` `*.pth` `*.safetensors` `models/`. 첫 실행 시 자동 다운로드입니다. 커밋 전 `git diff --cached` 확인.
- **좌우 반전.** Storage에 저장되는 사진과 `pose_landmarks`는 **반전되지 않은 카메라 원본** 기준. 미러링은 프론트 CSS만. 어기면 왼팔↔오른팔이 뒤바뀐 채 **에러 없이 조용히** 진행되고, VLM 진단이 전부 좌우 반대로 나옵니다. **구현 직후 크롭 PNG를 눈으로 확인하세요.**
- **레퍼런스와 사용자는 같은 `pose_scale_basis`를 써야 합니다.** 사용자 사진을 잴 때 레퍼런스 값을 강제하고, 그 기준을 못 재면 422로 떨어뜨리세요. 각자 다른 기준으로 재면 점수가 무의미해집니다.

### ⚠️ 중요

- **t3.large는 GPU가 없습니다.** Sapiens2 CPU 추론 수십 초 + 메모리 8GB. **세그 워커 동시성 1.** 2개 돌리면 OOM.
- **MediaPipe는 동기, Sapiens2는 비동기.** 레퍼런스 업로드 응답에 landmarks가 즉시 들어가야 사용자가 촬영 화면으로 바로 넘어갑니다. 둘을 같은 잡에 묶지 마세요.
- **`torch`/`mediapipe` 버전.** `==`로 고정하고 torch는 CPU 빌드. 버전이 어긋나면 fp16 로드/추론이 깨집니다. 추가 즉시 `requirements.txt` 반영 + 상대에게 공지(무거운 의존성).
- **`MIN_PIXELS`(1,500) / `MIN_RATIO`(0.5%) / `THRESHOLD`(0.90) / `F_MIN`(0.80) / `TOL`(40°)는 전부 잠정값.** 코드에 박지 말고 `config.py` + `.env`로. 새 환경변수는 같은 PR에서 `.env.example` 갱신.
- **`pixel_count` / `area_ratio` 원값을 항상 저장하세요.** 임계값을 나중에 올렸을 때 기존 데이터를 재판정할 수 있어야 합니다.
- **사진 교체 순서:** Storage 크롭 파일 삭제 → `photo` 행 삭제(세그 CASCADE) → 새로 생성. 순서를 어기면 고아 파일이 남습니다.
- **`DELETE /users/me`는 Storage 먼저, DB 나중.** DB를 먼저 지우면 어느 경로를 지워야 하는지 알 수 없게 됩니다.
- **signed URL 발급은 prefix 검증만으로 부족합니다.** DB에 그 행이 실제로 있는지 확인하세요.
- **잡 선점은 원자적으로.** `UPDATE ... WHERE status='PENDING' RETURNING`. `SELECT` 후 `UPDATE`하면 워커 2개가 같은 잡을 집습니다.
- **소유권 불일치는 403이 아니라 404.** 403은 리소스 존재를 알려줍니다.

---

## 4. 담당 B 주의사항

### 🔴 치명적

- **API 키를 절대 커밋하지 마세요.** `.env`만. `.env.example`에는 키 이름만.
- **VLM 중복 호출 = 요금 2배.** `POST /analysis`를 새로고침으로 두 번 부르면 안 됩니다. 이미 `PENDING`/`PROCESSING` 잡이 있으면 **새로 만들지 말고 기존 `job_id`를 반환**하세요.
- **`month_routine` 행을 절대 삭제하지 마세요.** `workout_log`가 CASCADE로 딸려 사라져 사용자의 수행 기록이 통째로 날아갑니다. `is_active = false`로만 내립니다.
- **새 루틴 버전이 `DONE`이 된 뒤에만 `is_active`를 넘기세요.** 생성 중에 넘기면 `FAILED`일 때 사용자가 볼 활성 루틴이 사라집니다.
- **통증·부상 피드백 안전 처리.** 해당 부위 부하 운동 즉시 제외 + `contraindications` 누적 + "통증 지속 시 중단·전문가 상담" 안내. "본 루틴은 의학적 조언이 아닙니다" 고지도 필요.

### ⚠️ 중요

- **부분 실패를 전체 실패로 만들지 마세요.** `VLM_PART` 하나가 죽어도 나머지 8개는 살아야 합니다. `status='FAILED'` 행을 남기고 결과에서 제외.
- **인바디는 선행 조건이 아닙니다.** 없어도 분석은 진행. 인바디 잡이 아직 `PENDING`이면 **기다리지 말고 인바디 없이 진행**하고 그 사실을 `job.result`에 남기세요. 사용자를 로딩 화면에 무한정 세우면 안 됩니다.
- **DB CHECK로는 항등식 검증이 안 됩니다.** 체중 ≈ 체수분+단백질+무기질+체지방량 / BMI ≈ 체중÷신장² / 좌우 30% 대칭성 → 애플리케이션에서 하고 결과를 `inbody.validation`에 기록. **INSERT를 실패시키지 마세요** — "OCR이 이상한 값을 뽑았다"는 사실 자체가 기록되지 않습니다.
- **좌우 대칭성 30% 차이는 경고만. 자동 수정 금지.** 실제로 심한 비대칭일 수 있습니다.
- **`raw_ocr`은 사용자 수정 시 덮어쓰지 마세요.** 원본과 수정본을 구분해야 OCR 정확도를 평가할 수 있습니다.
- **`raw_response`(VLM/LLM 원본)를 항상 저장하세요.** 프롬프트 튜닝 때 이게 없으면 왜 그런 답이 나왔는지 재현이 안 됩니다.
- **`gap_level`/`confidence`/`status`는 전부 대문자.** DB CHECK가 대문자로 걸려 있어 소문자를 넣으면 INSERT가 터집니다.
- **`feedback_text`를 `routine_revision`에 중복 저장하지 마세요.** 원본은 `workout_log.feedback_text`. 조회 시 조인.
- **루틴 패치는 변경분만.** 전체 재생성 금지. `changes` JSONB에 남겨야 "왜 바뀌었는지" 설명하고 되돌릴 수 있습니다.
- **`VLM_PART`는 병렬 가능** (동시성 3~4). 단 LLM API rate limit 확인.
- **인바디 임시 이미지는 `OCR_INBODY`가 `DONE`이 된 직후 삭제.** `FAILED`면 재처리를 위해 남깁니다.
- **`error` 필드에 스택 트레이스·모델 경로·API 키를 넣지 마세요.** 프론트에 그대로 노출됩니다.

---

## 5. 둘 다 지킬 것

| 항목 | 규칙 |
|---|---|
| **브랜치** | `main`에서 분기, `feature/`·`fix/`·`refactor/`·`chore/`. 하나의 브랜치 = 하나의 작업 |
| **커밋** | `<타입>: <한글 설명>` (예: `feat: 레퍼런스 세그멘테이션 워커 추가`) |
| **PR** | `main` 직접 push 금지. 최소 1명 승인. 🟡 공유 파일 변경 시 셀프 머지 절대 금지 |
| **포맷** | `black` + `isort`, line-length 100. 저장 시 자동 포맷 켜기 |
| **남의 파일** | 함부로 수정·재포맷 금지. 꼭 필요하면 최소한만 + PR 설명에 이유 |
| **의존성** | 새 패키지는 `==` 고정으로 즉시 `requirements.txt`. 무거운 건 공지 |
| **환경변수** | 새로 추가하면 **같은 PR에서 `.env.example` 갱신** |
| **DB 스키마** | 콘솔 변경 시 **같은 날 `db/schema.sql` 갱신 + 공지.** 파괴적 변경은 합의 후에만 |
| **테스트 데이터** | 무료 티어 공유 DB → `test_` 접두사 |
| **EC2** | 코드 직접 수정 금지, `git pull`만. 8000 포트 동시 실행 불가, tmux 세션 `api`, 재시작 전 공지 |
| **커밋 전** | `git diff --cached`로 `.env` / `*.pem` / 모델 가중치 / `.venv` / `__pycache__` / `.idea` 확인 |

### 실시간 공지 대상
`main` 배포 · EC2 재시작 · DB 스키마 변경 · 🟡 공유 파일 변경 · 무거운 의존성 추가 · 키 사고

---

## 6. 만나서 확정해야 할 것 (내일)

| # | 항목 | 결정권 | 막히는 작업 |
|---|---|---|---|
| 1 | **Sapiens2 실제 클래스명·개수** | A가 찍어보고 공유 | A seed / B 프롬프트 부위 목록 |
| 2 | **OCR 기술** (Document AI / Vision API / OpenAI) | B | B 인바디 전체 |
| 3 | **인바디 추출 컬럼 확정** (샘플 5~10장 필요) | 둘 다 | `inbody` 스키마 |
| 4 | 유사도 점수 산출 (VLM 직접 / 규칙 합산) | B | `score_source` |
| 5 | 루틴 진행 기준 (수행 횟수 / 날짜) | 둘 다 | `start_date`, `today` 계산 |
| 6 | 루틴 생성 분할 (28일 일괄 / 7일×4) | B | `status` 전환 시점 |
| 7 | VLM 입력 형식 (크롭 / 원본+하이라이트) | B | `bbox_*` 사용 여부 |
| 8 | 3방향 촬영 필요 여부 | 둘 다 | `photo.kind` 값 집합 |
| 9 | 레퍼런스 프리셋 도입 여부 | 둘 다 | `reference_source` |
| 10 | **시연 후 데이터 삭제 정책** | 둘 다 | 운영 (사람 사진이라 정해두는 게 좋음) |
| 11 | **`users`에 넣을 컬럼** | 둘 다 | `users` 스키마 (화면 요구 필요) |
