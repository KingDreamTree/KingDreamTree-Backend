# 담당 B 작업서 — LLM 파이프라인

> **이 문서 하나만 읽으면 됩니다.** 담당 A의 작업서(`docs/work-a.md`)는 안 봐도 됩니다.
> 참고 문서: `docs/db-design-v4.md` (스키마), `docs/api-spec-v2.md` (F07~F12)

| | |
|---|---|
| **최종 수정일** | 2026-08-13 |
| **한 줄 정의** | **부위별 크롭·수치가 들어와서 4주 루틴이 나올 때까지** |

---

## 1. 내가 만드는 것

| 기능 | 내용 |
|---|---|
| **F07** 인바디 결과지 인식 | 업로드, OCR 추출, 검증, 사용자 확인·수정 |
| **F08** 부위별 비교 진단 | 교집합 부위 수만큼 VLM 병렬 호출 |
| **F09** 종합 진단 | 유사도 점수 + 요약 + 우선 개선 부위 |
| **F10** 4주 루틴 생성 | LLM 호출 → Day 1~28 저장. 운동 일수 조정 = 새 버전 |
| **F11** 오늘의 루틴 | Day 계산 + 상세 조회 |
| **F12** 운동 완료 + 피드백 | 기록 저장 → 루틴 패치 → 변경 이력 |

## 2. 내가 안 만드는 것

사용자/세션 관리 · 사진 업로드 · 포즈 판정 · Sapiens2 세그멘테이션 · signed URL 발급 · 잡 폴링 API. 전부 담당 A입니다. 해당 라우터/서비스 파일은 건드리지 마세요.

## 3. 내 파일

```
app/services/ocr.py             인바디 추출 + 검증
app/services/vlm.py             부위별 비교 진단, 종합 진단
app/services/routine.py         루틴 생성, 피드백 패치
app/prompts/                    프롬프트 템플릿 전부
app/routes/inbody.py            F07
app/routes/analysis.py          F08, F09
app/routes/routines.py          F10, F11
app/routes/workout_logs.py      F12
app/worker/handlers/ocr.py      OCR_INBODY
app/worker/handlers/vlm.py      VLM_PART, VLM_OVERALL
app/worker/handlers/routine.py  ROUTINE_GEN, ROUTINE_PATCH
scripts/seed_test_data.py       ⚠️ 내가 만듦 (§5 Phase 1)
```

## 4. 담당 A와의 경계선 — 계약은 DB 하나뿐

A가 채우고, 나는 **읽기만** 합니다.

| 테이블 | 내가 쓰는 컬럼 |
|---|---|
| `segmentation` | `segmentation_id`, `photo_id`, `map_path`, `label_map`, `model_version` |
| `body_part_segment` | `class_name`, `is_valid`, `invalid_reason`, `pixel_count`, `area_ratio`, `bbox_*`, **`crop_path`** |
| `body_part` | `class_name`, `name_ko`, `is_comparable`, `inbody_segment`, `color_hex` |
| `photo` | `photo_id`, `kind`, `storage_path` |

- ⚠️ **`segmenter.py`를 절대 import하지 마세요.** import하는 순간 내 로컬 테스트에 Sapiens2 1.5GB가 필요해지고, A가 함수 시그니처를 바꾸면 내 코드가 깨집니다. **DB만 계약으로 씁니다.**
- ⚠️ **VLM 입력 이미지는 A가 만듭니다.** 나는 `body_part_segment.crop_path`를 signed URL로 바꿔 VLM에 넘기기만 합니다. 입력 형식이 `CROP`↔`HIGHLIGHT`로 바뀌어도 **A가 파일을 다시 만들고 내 코드는 그대로**입니다. 다만 어느 형식으로 진단했는지 `part_diagnosis.vlm_input_type`에 기록하세요.
- ⚠️ **`label_value`로 조인하지 마세요.** 모델 버전이 다르면 같은 숫자가 다른 부위입니다. 조인 키는 항상 `class_name`입니다.

**비교 대상 부위 쿼리** (이게 VLM 호출 횟수를 결정합니다)
```sql
SELECT bps.class_name
FROM body_part_segment bps
JOIN segmentation s ON s.segmentation_id = bps.segmentation_id
JOIN photo p        ON p.photo_id = s.photo_id
JOIN body_part bp   ON bp.class_name = bps.class_name
WHERE p.session_id = $1 AND bps.is_valid AND bp.is_comparable
GROUP BY bps.class_name
HAVING COUNT(DISTINCT p.kind) = 2;   -- REFERENCE, USER 둘 다 유효
```

---

## 5. 작업 순서

### Phase 0 — 담당 A와 같이 (페어, 반나절) ⚠️ 이걸 건너뛰면 뒤가 다 꼬입니다

| # | 작업 |
|---|---|
| 0-1 | `db/schema.sql` 확정 → Supabase 콘솔 반영 (16개 테이블 + 인덱스 + RLS 켜기, 정책 없음) |
| 0-2 | `app/schemas/` DTO 골격 — API 명세의 요청/응답 전부 |
| 0-3 | `app/services/db.py`, `storage.py`, `app/deps.py`, `app/config.py` |
| 0-4 | `app/worker/queue.py` + 잡 등록/폴링 — `GET /jobs/{id}`가 도는 상태까지 |
| 0-5 | `requirements.txt`, `.env.example` |
| 0-6 | Storage 버킷 4개 생성 (A가 주도) |

전부 공유 파일입니다. **한 브랜치에서 같이 작업하고 한 PR로 머지하세요.**

### Phase 1 — A를 기다리지 않기 위한 준비 ⚠️ 제일 먼저

`scripts/seed_test_data.py`를 만들어 A의 세그멘테이션 없이 개발할 수 있게 하세요.

| # | 작업 |
|---|---|
| 1-1 | `test_` 접두사 세션 1개 + 레퍼런스/사용자 `photo` 행 |
| 1-2 | 손으로 자른 부위 크롭 PNG 3~9장을 `body-parts` 버킷에 업로드 |
| 1-3 | 대응하는 `segmentation` + `body_part_segment` 행 삽입 (`is_valid=true`, `crop_path` 채움) |
| 1-4 | 더미 `inbody` + `inbody_segment` 행 |

- ⚠️ **A에게 실제 맵 PNG 샘플 1장 + `label_map` JSON을 받아서 형식을 맞추세요.** 여기서 어긋나면 통합할 때 터집니다.
- ✅ `body_part` seed는 이미 적용돼 있습니다 (29개, 비교 대상 9개). **프롬프트에 부위명을 하드코딩하지 말고 DB에서 읽으세요** — 부위 이름이 바뀔 수 있고(실제로 `Eyeglasses` → `Eyeglass` 로 고쳤습니다), 그때 코드를 고칠 일이 없어야 합니다.

### Phase 2 — 인바디

| # | 작업 |
|---|---|
| 2-1 | **OCR 기술 선택** (Document AI / Vision API / OpenAI) — 내가 결정 |
| 2-2 | `ocr.py` — JSON 구조화 추출 |
| 2-3 | 검증 로직 — ① 범위 ② 항등식 ③ 좌우 대칭성 → `validation` JSONB |
| 2-4 | `POST /inbody`, `GET /inbody/{id}`, `PATCH`, `DELETE` |
| 2-5 | `OCR_INBODY` 워커 + 임시 이미지 삭제 |

### Phase 3 — VLM 진단

| # | 작업 |
|---|---|
| 3-1 | 부위별 비교 프롬프트 v1 |
| 3-2 | `VLM_PART` 워커 (병렬 3~4) |
| 3-3 | 종합 진단 프롬프트 + `VLM_OVERALL` 워커 |
| 3-4 | `POST /analysis` (선행 조건·중복 호출 가드·`INSUFFICIENT_PARTS`) |
| 3-5 | `GET /analysis`, `GET /analysis/progress` |

### Phase 4 — 루틴

| # | 작업 |
|---|---|
| 4-1 | 루틴 생성 프롬프트 + `ROUTINE_GEN` 워커 |
| 4-2 | 응답 파싱 → `day_routine` 28행 + `day_routine_exercise` |
| 4-3 | `POST /routines` (운동 일수 조정 = 새 버전) |
| 4-4 | `GET /routines/active`, `days/{n}`, `today` |

### Phase 5 — 피드백 반영

| # | 작업 |
|---|---|
| 5-1 | `POST /workout-logs` |
| 5-2 | 패치 프롬프트 + `ROUTINE_PATCH` 워커 (**변경분만**) |
| 5-3 | `routine_revision` + `contraindications` 누적 |
| 5-4 | 안전 처리 (통증 피드백) |
| 5-5 | `GET /workout-logs`, `GET /revisions` |

### Phase 6 — 담당 A와 같이

전체 플로우 통합 테스트 (`POST /users` → 피드백 반영까지), 프론트 연동, 프롬프트 튜닝.

---

## 6. ⚠️ 주의사항

### 🔴 치명적

- **API 키를 절대 커밋하지 마세요.** `.env`만. `.env.example`에는 키 이름만. 커밋 전 `git diff --cached` 확인.
- **VLM 중복 호출 = 요금 2배.** `POST /analysis`를 새로고침으로 두 번 부르면 안 됩니다. 이미 `PENDING`/`PROCESSING` 잡이 있으면 **새로 만들지 말고 기존 `job_id`를 반환**하세요.
- **`month_routine` 행을 절대 삭제하지 마세요.** `workout_log`가 CASCADE로 딸려 사라져 사용자의 수행 기록이 통째로 날아갑니다. `is_active = false`로만 내립니다.
- **새 루틴 버전이 `DONE`이 된 뒤에만 `is_active`를 넘기세요.** 생성 중에 넘기면 `FAILED`일 때 사용자가 볼 활성 루틴이 사라집니다.
- **통증·부상 피드백 안전 처리.** 해당 부위 부하 운동 즉시 제외 + `analysis_session.contraindications`에 누적 + "통증이 지속되면 운동을 중단하고 전문가 상담을 권합니다" 안내. 서비스 전반에 "본 루틴은 의학적 조언이 아닙니다" 고지도 필요합니다.

### ⚠️ 중요 — 인바디

- **DB CHECK로는 항등식 검증이 안 됩니다.** 체중 ≈ 체수분+단백질+무기질+체지방량 / BMI ≈ 체중÷신장² / 좌우 30% 대칭성 → 애플리케이션에서 하고 `inbody.validation`에 기록. **INSERT를 실패시키지 마세요** — "OCR이 이상한 값을 뽑았다"는 사실 자체가 기록되지 않습니다.
- **부위별 범위(팔 0.5~8kg / 다리 2~20kg)도 CHECK가 아니라 애플리케이션 검증.** `segment`에 따라 범위가 달라 CHECK 식이 복잡해집니다.
- **좌우 대칭성 30% 차이는 경고만. 자동 수정 금지.** 실제로 심한 비대칭일 수 있습니다.
- **`raw_ocr`은 사용자 수정 시 덮어쓰지 마세요.** 원본과 수정본을 구분해야 OCR 정확도를 평가할 수 있습니다.
- **사용자 수정 후 `validation`을 재계산.** 고친 값이 또 항등식을 깨면 다시 `warn`이 떠야 합니다.
- **임시 이미지는 `OCR_INBODY`가 `DONE`이 된 직후 삭제.** `FAILED`면 재처리를 위해 남깁니다. 경로는 `job.payload`에 있습니다.
- **항등식 전용 항목(체수분·단백질·무기질·복부지방률·내장지방레벨 등)은 컬럼으로 만들지 마세요.** `raw_ocr`에서 읽어 검증만 하고 버립니다.
- ⚠️ **추출 컬럼 구성은 확정이 아닙니다.** 실제 결과지 샘플 5~10장 확보 후 확정하세요. WIM 3D 결과지 구조는 아직 확인 못 했습니다.

### ⚠️ 중요 — VLM

- **부분 실패를 전체 실패로 만들지 마세요.** `VLM_PART` 하나가 죽어도 나머지 8개는 살아야 합니다. `status='FAILED'` 행을 남기고 결과에서 제외. `GET /analysis`는 **200**을 반환합니다.
- **인바디는 선행 조건이 아닙니다.** 없어도 분석은 진행됩니다(선택 업로드). 인바디 잡이 아직 `PENDING`이면 **기다리지 말고 인바디 없이 진행**하고 그 사실을 `job.result`에 남기세요. 사용자를 로딩 화면에 무한정 세우면 안 됩니다.
- **`raw_response`를 항상 저장하세요.** 프롬프트 튜닝 때 이게 없으면 왜 그런 답이 나왔는지 재현이 안 됩니다.
- **`gap_level`/`confidence`/`status`는 전부 대문자.** DB CHECK가 대문자로 걸려 있어 소문자를 넣으면 INSERT가 터집니다.
- **`differences`는 JSONB 배열.** 이어붙여 TEXT로 저장하면 화면에서 항목별로 나열할 수 없습니다.
- **`VLM_PART`는 병렬 가능** (동시성 3~4). LLM API rate limit 확인하세요.
- **부위 프롬프트에 인바디 수치를 함께 넣으세요.** `body_part.inbody_segment`로 `inbody_segment.lean_mass`와 조인됩니다. 시각 정보 + 실측 수치가 함께 들어가는 게 이 서비스의 차별점입니다.
- **`confidence='LOW'` 진단을 루틴 생성 입력에서 뺄지 결정하세요.** 이미지 품질이 낮으면 VLM이 LOW를 냅니다.

### ⚠️ 중요 — 루틴

- **루틴 패치는 변경분만.** 전체 재생성 금지. `changes` JSONB에 남겨야 "왜 바뀌었는지" 설명하고 되돌릴 수 있습니다.
- **`feedback_text`를 `routine_revision`에 중복 저장하지 마세요.** 원본은 `workout_log.feedback_text`. 조회 시 조인합니다.
- **`contraindications`는 세션 단위 누적** (`analysis_session`). `routine_revision.contraindications_added`에는 이번 회차 증분만.
- **`month_routine_id`는 서버가 현재 활성 버전으로 채웁니다.** 클라이언트가 보내게 하지 마세요.
- **`weight_kg`는 LLM 추정치.** `GET /routines/.../days/{n}` 응답에 `disclaimer`를 담아 프론트가 반드시 노출하게 하세요. 프론트 구현에 맡기면 빠집니다.
- **28행을 항상 만드세요.** 휴식일도 `is_rest=true`로 행이 있어야 주차 토글이 안 깨집니다.
- **`week_number`는 생성 컬럼입니다.** INSERT에 넣지 마세요.
- ⚠️ **7일×4회 분할 생성을 택하면** 1주차 저장 후에도 `status`는 `PENDING`을 유지하고 4주차까지 끝난 시점에만 `DONE`으로 바꾸세요. 안 그러면 화면에 7일짜리 루틴이 노출됩니다.
- ⚠️ **오늘의 Day 계산**은 `min(COUNT(workout_log)+1, 28)`. 응답에 `day_source: "COUNT"`를 넣어두세요 — 나중에 날짜 기준으로 바꿔도 프론트가 어느 방식인지 알 수 있습니다.

### ⚠️ 공통

- **잡 선점은 원자적으로.** `UPDATE ... WHERE status='PENDING' RETURNING`. `SELECT` 후 `UPDATE`하면 워커 2개가 같은 잡을 집습니다.
- **소유권 불일치는 403이 아니라 404.** 403은 리소스 존재를 알려줍니다.
- **`job.error`에 스택 트레이스·프롬프트 전문·API 키를 넣지 마세요.** 프론트에 그대로 노출됩니다.
- **`attempts >= 3`이면 `FAILED`.** 재시도는 지수 백오프.

---

## 7. 내 완료 체크리스트

- [ ] `seed_test_data.py`로 A 없이 전체 개발 가능한지
- [ ] 부위 목록을 프롬프트에 하드코딩하지 않고 `body_part`에서 읽는지
- [ ] 인바디 항등식 검증이 INSERT를 실패시키지 않고 `validation`에 기록되는지
- [ ] `VLM_PART` 하나를 일부러 실패시켰을 때 나머지가 살아남는지
- [ ] `POST /analysis`를 두 번 불렀을 때 잡이 두 배로 안 생기는지
- [ ] 인바디 없이도 분석 → 루틴 생성이 끝까지 도는지
- [ ] 루틴 생성이 `FAILED`일 때 이전 버전이 활성으로 남아 있는지
- [ ] 피드백 → 새 버전 생성 후에도 `workout_log`가 전부 살아 있는지
- [ ] `raw_response` / `raw_ocr`가 전부 저장되는지
- [ ] 통증 피드백 시 해당 부위 운동이 실제로 빠지고 `contraindications`에 쌓이는지
- [ ] 임시 인바디 이미지가 `DONE` 후 삭제되는지
- [ ] API 키가 `.env`에만 있고 `.env.example`에 이름만 있는지

---

## 8. 협업 규칙 (둘 다 지킴)

| 항목 | 규칙 |
|---|---|
| **브랜치** | `main`에서 분기, `feature/`·`fix/`·`refactor/`·`chore/`. 하나의 브랜치 = 하나의 작업 |
| **커밋** | `<타입>: <한글 설명>` (예: `feat: 부위별 VLM 진단 워커 추가`) |
| **PR** | `main` 직접 push 금지. 최소 1명 승인. 머지 후 브랜치 삭제 |
| **공유 파일** | `app/main.py` `app/config.py` `app/schemas/` `app/services/db.py` `app/services/storage.py` `app/deps.py` `app/worker/queue.py` `db/schema.sql` `requirements.txt` `.env.example` → **변경 시 A 리뷰 필수, 셀프 머지 금지** |
| **남의 파일** | 함부로 수정·재포맷 금지. 꼭 필요하면 최소한만 + PR 설명에 이유 |
| **포맷** | `black` + `isort`, line-length 100. 저장 시 자동 포맷 켜기 |
| **의존성** | 새 패키지는 `==` 고정으로 즉시 `requirements.txt`. 무거운 건 공지 |
| **환경변수** | 새로 추가하면 **같은 PR에서 `.env.example` 갱신** |
| **DB 스키마** | 콘솔 변경 시 **같은 날 `db/schema.sql` 갱신 + 공지.** 파괴적 변경은 합의 후에만 |
| **테스트 데이터** | 무료 티어 공유 DB → `test_` 접두사 |
| **EC2** | 코드 직접 수정 금지, `git pull`만. 8000 포트 동시 실행 불가, tmux 세션 `api`, 재시작 전 공지 |
| **커밋 전** | `git diff --cached`로 `.env` / `*.pem` / `.venv` / `__pycache__` / `.idea` 확인 |

**실시간 공지 대상** — `main` 배포 · EC2 재시작 · DB 스키마 변경 · 공유 파일 변경 · 무거운 의존성 추가 · 키 사고

---

## 9. 내가 결정하거나 확인해야 할 미확정 항목

| # | 항목 | 상태 |
|---|---|---|
| 1 | **OCR 기술 선택** (Document AI / Vision API / OpenAI) | **내가 결정. 최우선** — 인바디 전체가 막혀 있음 |
| 2 | **인바디 추출 컬럼 확정** | 실제 결과지 샘플 5~10장 확보 후. WIM 3D 결과지 구조 미확인 |
| 3 | 유사도 점수 산출 방식 (VLM 직접 / 규칙 합산) | 내가 결정 — 확정되면 `score_source` 컬럼 제거 가능 |
| 4 | VLM 입력 형식 (크롭 / 원본+하이라이트) | 내가 결정 → **A에게 알려주면 A가 파일을 만듦** |
| 5 | 루틴 생성 분할 (28일 일괄 / 7일×4) | 내가 결정 |
| 6 | `strengths`/`cautions` 화면 사용 여부 | 화면 요구 확인 — 안 쓰면 컬럼·응답에서 제거 |
| 7 | 루틴 진행 기준 (수행 횟수/날짜) | 둘이 합의 |
| 8 | 시연 후 데이터 삭제 정책 | 둘이 합의 |

**A에게서 받아야 할 것**

- ✅ **Sapiens2 클래스 목록은 확정됐습니다** — 29개(28 + Eyeglass), 그중 **비교 대상 9개**. `body_part` 테이블에 seed 적용 완료이므로 프롬프트 부위 목록은 DB에서 읽으면 됩니다. (`GET /body-parts` 또는 `db.comparable_class_names()`)
- ⏳ 맵 PNG 샘플 1장 + `label_map` JSON — 아직 대기. **이게 오기 전까지는 §5 Phase 1의 더미 데이터로 진행합니다.**
