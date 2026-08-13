# CHANGELOG

백엔드 2인 협업용 append-only 로그. **최신순 정렬. 과거 항목 절대 수정 금지.**

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
