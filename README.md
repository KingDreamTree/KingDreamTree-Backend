# KingDreamTree-Backend

레퍼런스 이미지 기반 체형 비교 분석 + 개인화 운동 루틴 생성 백엔드.

**스택**: FastAPI / Sapiens2(세그멘테이션) / Claude Vision / Supabase / Docker / EC2

---

## 빠른 시작

### 로컬 (venv)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# .env 에서 USE_MOCK=true 설정 (키 없이 바로 실행 가능)

USE_MOCK=true uvicorn app.main:app --reload --port 8000
```

브라우저에서 http://localhost:8000/docs 확인.

### Docker Compose

```bash
cp .env.example .env
# .env 에서 USE_MOCK=true 설정

docker compose up --build
```

http://localhost:8000/docs 에서 Swagger UI 확인.

---

## 호출 순서

```
POST /analyze   → 사용자·레퍼런스 이미지 세그멘테이션
POST /compare   → 체형 비교 분석 (Claude Call1)
POST /routine   → 개인화 운동 루틴 생성 (Claude Call2)
```

자세한 통합 가이드는 [docs/FRONTEND.md](docs/FRONTEND.md) 참고.  
정확한 요청/응답 스키마는 `/docs` (Swagger) 참고.

---

## 수동 설정 (사람이 직접 해야 할 것)

아래 작업은 자동화되어 있지 않습니다. 최초 셋업 시 수동으로 진행하세요.

1. **GitHub branch protection** — `main`/`develop` 브랜치 보호 규칙 설정
2. **Supabase 프로젝트 생성** — [supabase.com](https://supabase.com) 에서 새 프로젝트 생성
3. **Supabase 버킷 생성** — Storage에서 `images/`, `overlays/` 버킷 생성
4. **DB 스키마 적용** — Supabase SQL 에디터에서 `db/schema.sql` 실행
5. **.env 실제 값 입력** — `ANTHROPIC_API_KEY`, `SUPABASE_URL` 등

---

## 환경 변수

| 변수 | 설명 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_ANON_KEY` | Supabase anon 키 |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role 키 (서버 전용) |
| `USE_MOCK` | `true` 이면 실제 모델·API 호출 없이 mock 데이터 반환 |

---

## 개발 팁

- `USE_MOCK=true` 상태에서는 API 키·모델 없이 end-to-end 200 응답 확인 가능.
- 실제 모델(`requirements.txt`의 ML 섹션 주석 해제)은 별도 `models/` 볼륨에 두고 docker compose로 마운트.
- 포매터: `black --check .` + `isort --check-only .`
