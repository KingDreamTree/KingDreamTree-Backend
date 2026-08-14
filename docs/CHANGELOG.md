# CHANGELOG

백엔드 2인 협업용 append-only 로그. **최신순 정렬. 과거 항목 절대 수정 금지.**

---

## 2026-08-15 - 조용한 버그 4건 + e2e 테스트 창 + 진단 기준선 분리 (B)

**잡은 버그** (전부 에러 없이 조용히 새는 종류 — 각 PR 에 재현·실측 기록):
- #42 통증이 기록만 되고 루틴 미반영 → `services/contraindication.py` 코드 강제
    (WARN 세트 -1 · BLOCK 제외 · Day 는 안 비움 · 대체 운동은 안 고름).
    적용은 두 경로(F12-a 워커·F12-b 코치챗) **조기 반환보다 앞**에
- #50 삭제된 세션의 잡 1개가 워커 프로세스 전체를 죽임 — complete→fail 연쇄
    IndexError. queue 가 UPDATE 0행을 허용하고 run.py 가 fail 을 try 로 감쌈
- #53 피드백마다 진행도 0 리셋 — 새 버전엔 workout_log 0건이라서.
    진행도를 **세션 단위**로 집계 (스키마 변경 없음, workout_log.session_id 활용)
- #59·#60 진단 기준선 오염 — 인바디 '표준 대비 %'(평균 비교)가 목표 격차
    판정을 눌렀다. 기준선 분리 + few-shot 교체 + 좌우 실측 코드 계산(_inbody_lr)
    + 모순 게이트(_coerce_part: 관찰 있으면 blocked 해제) + 범례 줄에 가림 자격
    명시 + priority_parts 3개 강제. **실사진 세션 라이브 재진단 4회로 검증**

**만든 것**:
- `web/e2e-test.html` (#46·#49·#52·#55·#57·#58) — 사진 2장 + 인바디(선택) →
    세그 오버레이(OVERLAY_ALPHA 동일·라벨 보간 금지) → 진단 → 루틴 →
    F12 피드백 루프 → 코치 대화까지 브라우저 한 페이지. 관문 점수는
    pose-score.js 를 프론트와 같은 코드로 계산. localStorage 이어쓰기
- `scripts/verify_contraindication.py` · `verify_worker_resilience.py` 신규,
    `verify_analysis.py` §8 기준선 분리 24항목 추가

**환경 실측** (#44·#51): 로컬 세그 백본 **1b 확정** — 0.4b 0.6s/2.1GB ·
1b 1.0s/4.8GB · 5b 376s/12.5GB(12GB 카드에서 OOM 대신 640배 느려짐 —
WDDM 이 초과분을 공유 메모리로 흘려서 안 죽고 느려진다). Windows HF 다운로드는
`HF_HUB_DISABLE_XET=1` 필수 (hf_xet 이 조용히 멈춤).

**정리**: 병합 브랜치 로컬·원격 전부 삭제(main 하나), 열린 PR 0, 5b 가중치 삭제.

---

## 2026-08-14 - F10 루틴 생성 로직 완성 + 운동명 한글화 (B)

**무엇이**:
- `app/services/routine.py`: `build_routine()` — PM 확정 로직 구현
    L0 모드(체지방률 1차) → L1 골격(주기당 N일, CUT은 유산소 항목 포함)
    → L2 약점 가중(+2~4세트/주) → ExerciseDB 후보 필터 → LLM 선택(후보 제약)
    → 검증·조립(RIR 처방, 주간 중복 제한). LLM 실패 시 결정론 폴백으로 완주
- `app/services/routine_templates.py`: 전면 재작업 — Day 1~28 폐기,
    주기당 N일(휴식일 행 없음), CUT/BALANCE 골격, 수치 출처(ACSM 등) 주석 명기
- `app/prompts/routine_gen.py`: 후보 제약형 재작성 — LLM은 exercise_ref 선택만
- `scripts/localize_exercises.py`: 운동명 한글화 배치 (200/200 완료, 캐시 저장)
- `scripts/verify_routine_build.py`: 확정 로직 계약 검증 30항목 통과
- 실호출 검증: CUT 시나리오에서 LLM 선택 교정 0건, 비대칭 부위에 단측 운동
    (원암 로우·싱글 레그 카프) 자동 배치, 비용 $0.019/호출

**확정 반영 (D1·D3·D10 결정됨)**:
- D1: 체지방률 남 ≥25 / 여 ≥36 → CUT, 미만·인바디 없음 → BALANCE
- D3: CUT 유산소는 문구가 아니라 Day 목록의 운동 항목 (완료 체크 대상)
- D10: 진단 실패 부위도 기본 볼륨 유지(가이드라인 근거), 개인화는 진단 부위만

**남은 것 (A 협의 후)**: 루틴 스키마 반영(routine-schema-draft.md) → 라우터·워커 배선

---

## 2026-08-14 - F08·F09 진단 파이프라인 구현 + 부위별 병렬 폐기 (B)

**무엇이**:
- `app/routes/analysis.py`: `POST /analysis`, `GET /analysis`, `GET /analysis/progress`
- `app/worker/handlers/vlm.py`: `VLM_PART`(전 부위 일괄) + `VLM_OVERALL` 핸들러
- `app/services/vlm.py`: 전면 재작성 — 구 스캐폴드(`compare_body`/`generate_routine`) 제거,
  응답 **부위 단위 부분 채택** 검증 추가
- `app/services/diagnosis_repo.py`: 비교 대상 교집합 산출 + 진단 저장/조회 (B 전용)
- `app/services/segmap.py`: `build_overlay()`(전 부위 컬러 오버레이),
  `compare_parts()`/`symmetry()`(스케일 불변 수치), `fit_for_vlm()`
- `app/prompts/{part_diagnosis,overall_diagnosis}.py`: 신규
- `app/services/inbody_repo.py`: `latest_done()` / `to_prompt_payload()` — 세션 최신 DONE 1건
- `app/schemas/analysis.py`: DTO
- `scripts/verify_analysis.py`: DB·키 없이 도는 오프라인 검증 26항목
- 삭제: `app/routes/analyze.py`, `app/schemas/{analyze,compare}.py` (구 스캐폴드)

**왜**:
- 입력이 크롭 → **원본+오버레이**로 확정된 뒤에도 호출 구조는 크롭 시절의
  "부위별 병렬 9회"가 남아 있었다. 그 조합은 같은 원본을 부위 수만큼 재업로드하고,
  격리된 호출들이 서로의 판단을 몰라 **부위 간 우선순위를 매길 수 없다.**
  → 전 부위 1회 Vision + 종합 1회 Text 로 변경. 호출 10회 → 2회, 입력 토큰 약 1/3.
- 면적·너비는 세그 맵에 이미 정확히 있는데 VLM에게 눈대중으로 재게 하고 있었다.
  → 코드가 계산해 프롬프트에 주고, VLM은 수치로 표현 안 되는 것(근육 라인·실루엣)만.
- 수치를 인물 크기로 정규화. 안 하면 "가까이서 찍었다"가 "근육량이 늘었다"로 읽힌다.

**영향 (프론트·상대 개발자)**:
- **신규 엔드포인트 3개.** 명세는 `docs/api-spec-v2.md` F08·F09 갱신본 참고.
- ⚠️ `POST /analysis` 응답 변경 — `part_jobs[].job_id` 가 전부 같은 값이고,
  `overall_job_id` 는 항상 `null` 입니다. 새 화면은 `part_job_id` 하나만 쓰세요.
- ⚠️ `GET /analysis` 에서 `reference_crop_url`/`user_crop_url` **제거.** 크롭 파일을
  만들지 않습니다. 부위 시각화는 `GET /sessions/{id}/segmentation` 의 맵+팔레트로.
- `gap_level: null` + `blocked_reason` 은 실패가 아니라 "VLM이 모르겠다고 보고함"입니다.
- 응답의 `disclaimer` 를 화면에 반드시 노출해주세요.
- **A 영향 없음** — 읽는 테이블(`segmentation`/`body_part_segment`/`body_part`)과
  조인 키(`class_name`)는 그대로입니다. DB 스키마 변경 없음.

---

## 2026-08-13 - F07 인바디 API 완성 + 하이라이트 파이프라인 검증 (B)

**무엇이**:
- `app/routes/inbody.py`: 업로드(202+job)·목록·상세·수정·삭제 5개 엔드포인트
- `app/worker/handlers/ocr.py`: `OCR_INBODY` 핸들러. DONE 직후 임시 이미지 삭제
- `app/services/inbody_repo.py`: B 전용 쿼리 (공유 파일 `db.py` 비대화 방지)
- `app/services/ocr.py`: 다중 페이지 추출 + **DB CHECK 가드** + 확인 화면용 변환기
- `app/schemas/inbody.py`: DTO 4종
- `app/services/segmap.py`: **라벨 맵 → 하이라이트 생성** (F08 VLM 입력)
- `scripts/verify_segmap.py`: 실제 맵 샘플로 검증 (A 샘플 20/20 통과)
- `app/schemas/routine.py`: `InbodySnapshot.from_inbody()` — DB→DTO 매핑 일원화
- `app/main.py`: inbody 라우터 등록

**왜**:
- **SMI는 VLM이 계산하지 않는다.** 프롬프트 절대 규칙이 "인쇄된 숫자만, 계산 금지"인데
  계산을 시키면 할루시네이션 통로가 열린다. `calc_smi()`가 컬럼값으로 계산하고
  DB에 저장하지 않아 사용자 수정이 즉시 반영된다.
- **DB CHECK 가드**: OCR이 범위 밖 값을 뽑으면 UPDATE 전체가 터져
  "검증 실패가 INSERT를 막지 않는다" 원칙이 깨진다. 위반 컬럼만 비우고 WARN 기록.
- **하이라이트 함정 3개** (실측 검증): 맵-원본 종횡비 불일치(단일 배율 시 31px 오차),
  리사이즈 보간이 라벨을 섞음(BILINEAR 시 23→28종), 라벨 인덱스 하드코딩 금지.
- `openai` 지연 import: 최상단 import면 미설치 배포 서버에서 **API 전체가 기동 실패**.

**영향 (프론트·상대 개발자)**:
- 신규 엔드포인트 `/api/v1/sessions/{id}/inbody`, `/api/v1/inbody/{id}` (전부 `/api/v1` 아래).
- `GET /inbody/{id}` 응답의 `smi`는 **DB 컬럼이 아니라 파생값**이다.
- `validation`은 WARN 필드만 내려간다 (전부 주면 사용자가 대충 넘긴다).
- **A 요청 사항 있음** → `docs/handoff-to-a.md` §1 (의류 병합 위치) 참고.

---

## 2026-08-13 - VLM provider 추상화 + Sapiens2 모델 관리 정책

**무엇이**:
- `services/vlm.py`: Claude 하드코딩 제거 → `VLM_PROVIDER` env로 분기하는 추상화 레이어로 교체
- `app/config.py`: `VLM_PROVIDER`, `OPENAI_API_KEY`, `MODEL_DIR` 설정 추가
- `.env.example`: 신규 env 변수 반영 (`VLM_PROVIDER`, `MODEL_DIR`, `OPENAI_API_KEY`)
- `models/.gitkeep`: 가중치 없이 디렉토리 구조만 git 추적
- `scripts/download_sapiens.py`: Sapiens2 가중치 HuggingFace 다운로드 안내 스크립트
- `app/services/segmenter.py`: `MODEL_DIR` 설정으로 모델 경로 참조하도록 수정
- `README.md`: 모델 다운로드 + VLM provider 설정 절차 추가

**왜**:
- VLM provider 미확정 상태에서 Claude에 lock-in되지 않도록 인터페이스 분리.
- Sapiens2 가중치(수 GB)를 git에 넣을 수 없으므로 로컬 배치 + docker 볼륨 마운트 정책 명문화.

**영향 (프론트·상대 개발자)**:
- API 계약 변경 없음. 엔드포인트·요청/응답 스키마 동일.
- **신규 env 변수**: `VLM_PROVIDER` (claude|openai), `MODEL_DIR` (기본 `models`).
  `.env.example` 참고해서 본인 `.env` 갱신 필요.
- 모델 실사용 전 `python scripts/download_sapiens.py` 실행 필요.

---

## 2026-08-13 - 프로젝트 초기 스캐폴딩

**무엇이**: FastAPI 백엔드 스켈레톤 전체 구조 생성.

**왜**: 레퍼런스 이미지 기반 체형 비교 분석 + 개인화 운동 루틴 백엔드 개발 착수.

**포함 내용**:
- 설정 파일: `.gitignore`, `.dockerignore`, `pyproject.toml`, `.env.example`
- DB 스키마 초안: `db/schema.sql` (`analysis` 테이블)
- 앱 스켈레톤: `app/main.py`, `app/config.py`, 라우터 3개(`/analyze`, `/compare`, `/routine`)
- 서비스 레이어: `segmenter`, `vlm`, `storage`, `db` (전부 mock 분기 포함)
- Pydantic DTO 스키마: `schemas/analyze.py`, `schemas/compare.py`, `schemas/routine.py`
- Docker: `Dockerfile`, `docker-compose.yml` (models/ 볼륨 마운트)
- 문서: `docs/FRONTEND.md`, `docs/CHANGELOG.md`, `README.md`

**영향 (프론트·상대 개발자)**:
- `USE_MOCK=true` 환경변수 하나로 API 키·모델 없이 세 엔드포인트 200 응답 확인 가능.
- 호출 순서: `POST /analyze → POST /compare → POST /routine`
- 요청은 `/analyze`만 `multipart/form-data`, 나머지는 `application/json`.
- Swagger UI: `http://localhost:8000/docs`
