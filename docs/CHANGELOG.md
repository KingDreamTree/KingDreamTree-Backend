# CHANGELOG

백엔드 2인 협업용 append-only 로그. **최신순 정렬. 과거 항목 절대 수정 금지.**

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
