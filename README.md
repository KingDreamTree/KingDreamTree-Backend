# KingDreamTree-Backend

레퍼런스 이미지 기반 체형 비교 분석 + 개인화 운동 루틴 생성 백엔드.

**스택**: FastAPI / Sapiens2(세그멘테이션) / VLM(미확정) / Supabase / Docker / EC2

---

## 빠른 시작 — mock 모드 (키·모델 없이)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # USE_MOCK=true 설정

USE_MOCK=true uvicorn app.main:app --reload --port 8000
```

브라우저에서 http://localhost:8000/docs 확인.

### Docker Compose (mock)

```bash
cp .env.example .env   # USE_MOCK=true 설정
docker compose up --build
```

---

## Sapiens2 모델 가중치 설정 (실제 추론 시)

가중치는 git에 포함되지 않습니다. 로컬 `models/` 디렉토리에 직접 배치해야 합니다.

```bash
pip install huggingface-hub
python scripts/download_sapiens.py
```

다운로드 후 구조:
```
models/
└── sapiens/
    └── ...pth
```

Docker를 사용할 경우 `docker-compose.yml`이 `./models:/app/models` 로 자동 마운트합니다.
모델 가중치는 절대 커밋하지 마세요 (`*.pth`, `*.safetensors` 등은 `.gitignore`에 포함).

---

## VLM provider 설정 (실제 추론 시)

VLM provider가 아직 확정되지 않았습니다. `.env`에서 선택합니다:

```env
VLM_PROVIDER=claude    # ANTHROPIC_API_KEY 필요
VLM_PROVIDER=openai   # OPENAI_API_KEY 필요 (미구현, TODO)
```

확정 전에는 `USE_MOCK=true`로 우회하세요.

---

## 호출 순서

```
POST /analyze   → 이미지 업로드 + 세그멘테이션
POST /compare   → 체형 비교 분석 (VLM Call1)
POST /routine   → 개인화 운동 루틴 생성 (VLM Call2)
```

자세한 통합 가이드는 [docs/FRONTEND.md](docs/FRONTEND.md) 참고.  
정확한 요청/응답 스키마는 `/docs` (Swagger) 참고.

---

## 환경 변수

| 변수 | 설명 | mock 없이 필요? |
|------|------|----------------|
| `SUPABASE_URL` | Supabase 프로젝트 URL | 필수 |
| `SUPABASE_ANON_KEY` | Supabase anon 키 | 필수 |
| `SUPABASE_SERVICE_ROLE_KEY` | service role 키 (서버 전용) | 필수 |
| `VLM_PROVIDER` | `claude` 또는 `openai` | 필수 |
| `ANTHROPIC_API_KEY` | Claude API 키 | VLM_PROVIDER=claude 시 |
| `OPENAI_API_KEY` | OpenAI API 키 | VLM_PROVIDER=openai 시 |
| `MODEL_DIR` | Sapiens2 가중치 디렉토리 (기본 `models`) | 필수 |
| `USE_MOCK` | `true`이면 모든 외부 호출 없이 mock 반환 | — |

---

## 수동 설정 (사람이 직접 해야 할 것)

1. **GitHub branch protection** — `main`/`develop` 보호 규칙
2. **Supabase 프로젝트 생성** → 버킷(`images/`, `overlays/`) 생성
3. **DB 스키마 적용** — Supabase SQL 에디터에서 `db/schema.sql` 실행
4. **모델 가중치 다운로드** — `python scripts/download_sapiens.py`
5. **VLM provider 확정** → `.env`의 `VLM_PROVIDER` 설정

---

## 개발 팁

- `USE_MOCK=true`로 API 키·모델 없이 즉시 개발 시작 가능.
- ML 패키지(torch 등)는 `requirements.txt` 하단 주석 해제 후 설치.
- 포매터: `black --check .` + `isort --check-only .`
