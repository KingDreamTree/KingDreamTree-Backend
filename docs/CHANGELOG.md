# CHANGELOG

백엔드 2인 협업용 append-only 로그. **최신순 정렬. 과거 항목 절대 수정 금지.**

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
