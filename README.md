# KingDreamTree-Backend

레퍼런스 이미지 기반 체형 비교 분석 + 개인화 운동 루틴 생성 백엔드.

**스택**: FastAPI / Sapiens2(세그멘테이션) / OpenAI VLM / Supabase / Docker / EC2

---

## 빠른 시작

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

브라우저에서 http://localhost:8000/docs 확인.

⚠️ **`USE_MOCK=true` 는 DB 를 대신해주지 않는다.** mock 이 우회하는 것은 VLM 호출뿐이고,
DB·스토리지는 그대로 Supabase 로 나간다. `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
없이는 `POST /api/v1/users` 부터 500 이다.

⚠️ **`SUPABASE_URL` 은 `https://<프로젝트>.supabase.co` 까지만.** 끝에 `/rest/v1` 을 붙이면
클라이언트가 한 번 더 붙여서 `PGRST125 Invalid path` 가 난다.

⚠️ **`.env` 를 고치면 서버를 껐다 켤 것.** 설정은 import 시점에 한 번만 읽고,
`--reload` 는 `.py` 만 감시한다. 값이 안 바뀐 것처럼 보이는 원인이 대부분 이것이다.

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

---

## Sapiens2 모델 가중치 (실제 추론 시)

가중치는 git 에 없다. `models/` 로 직접 받는다.

```bash
python scripts/download_sapiens.py --size 1b     # 서비스 표준 백본 (5.9GB)
```

받은 뒤 구조 (safetensors 다. `.pth` 가 아니다):
```
models/
└── sapiens2-seg-1b/
    ├── model.safetensors
    ├── config.json
    └── preprocessor_config.json
```

검증 — 라벨 29 클래스와 좌우 배치까지 확인한다:
```bash
python scripts/verify_labels.py --image tests/fixtures/sample-photo.jpg --size 1b
```

Docker 는 `docker-compose.yml` 이 `./models:/app/models` 로 자동 마운트한다.
가중치는 절대 커밋하지 말 것 (`*.pth`, `*.safetensors`, `models/*` 는 `.gitignore`).

### torch 설치 — 환경별로 다르다

`requirements.txt` 에 torch 는 없다. 환경마다 받아야 할 빌드가 다르기 때문이다.
버전은 `requirements-ml.txt` 한 곳에서 관리한다.

| 환경 | 명령 |
|------|------|
| 로컬 GPU (CUDA 12.8) | `scripts/install_local_gpu.sh` |
| EC2 / CPU 전용 | `scripts/install_cpu.sh` |
| RunPod GPU 워커 | `scripts/install_runpod.sh` |
| **macOS (Apple Silicon)** | 아래 참조 |

**macOS (Apple Silicon)** — `install_cpu.sh` 를 쓰면 안 된다. 리눅스 CPU 인덱스라
arm64 맥 빌드가 나오지 않는다. PyPI 기본 인덱스에서 받으면 MPS 가 포함된다:

```bash
pip install torch==2.11.0 torchvision==0.26.0 \
  transformers==5.15.0 safetensors==0.8.0 huggingface-hub==1.27.0 accelerate==1.14.0
```

`SAPIENS_DEVICE=auto` 면 알아서 mps 를 잡는다 (우선순위 cuda → mps → cpu).
0.4b / 1024px 한 장에 약 4.5 초. 16GB 맥에서 `5b` 는 올리지 말 것 (fp16 가중치만 ~9.5GB).

---

## VLM provider

**openai 만 지원한다.** 다른 값을 넣으면 `vlm.py` 가 기동 시 에러를 낸다.

```env
VLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

키 없이 개발하려면 `USE_MOCK=true` — VLM 호출만 mock 으로 대체된다 (DB 는 위 경고 참조).

---

## API

세션 단위다. 모든 경로에 `/api/v1` 접두사가 붙고, 인증 대신 `X-User-Id` 헤더를 쓴다
(`POST /users`, `GET /body-parts` 제외). 무거운 작업은 202 + `job_id` 를 반환하고
`GET /api/v1/jobs/{job_id}` 폴링으로 확인한다.

```
POST /api/v1/users                                  → user_id 발급
POST /api/v1/sessions                               → 세션 생성
POST /api/v1/sessions/{id}/photos/reference|user    → 사진 업로드 (+ 포즈 판정)
GET  /api/v1/sessions/{id}/segmentation             → 세그멘테이션 결과
POST /api/v1/sessions/{id}/analysis                 → 부위 진단 (VLM)
POST /api/v1/sessions/{id}/routines                 → 운동 루틴 생성
POST /api/v1/sessions/{id}/coach-chat               → 루틴 수정 대화
```

전체 목록과 정확한 스키마는 `/docs` (Swagger), 명세는 [docs/api-spec-v2.md](docs/api-spec-v2.md).
프론트 통합 가이드는 [docs/FRONTEND.md](docs/FRONTEND.md).

---

## 환경 변수

전체 목록과 각 값의 근거는 [.env.example](.env.example) 에 주석으로 있다. 없으면 못 도는 것만:

| 변수 | 설명 | 필수? |
|------|------|-------|
| `SUPABASE_URL` | `https://<프로젝트>.supabase.co` (뒤에 경로 붙이지 말 것) | 필수 |
| `SUPABASE_SERVICE_ROLE_KEY` | `sb_secret_...` (구 service_role). 서버 전용, 프론트 노출 금지 | 필수 |
| `CORS_ORIGINS` | 프론트 오리진. 비우면 브라우저 요청이 전부 막힌다 | 필수 |
| `OPENAI_API_KEY` | VLM 진단·사진 스크리닝 | `USE_MOCK=false` 시 |
| `MODEL_DIR` | Sapiens2 가중치 디렉토리 (기본 `models`) | 세그 워커 |
| `SAPIENS_DEVICE` | `auto` \| `cuda` \| `mps` \| `cpu` (auto: cuda → mps → cpu) | 세그 워커 |
| `USE_MOCK` | `true` 면 VLM 호출을 mock 으로 대체 (DB 는 아니다) | — |

⚠️ 여기 없는 키를 `.env` 에 넣으면 조용히 무시된다. 기동 시 경고만 뜬다.

---

## 수동 설정 (사람이 직접 해야 할 것)

1. **GitHub branch protection** — `main`/`develop` 보호 규칙
2. **Supabase 프로젝트 생성** → 버킷 생성: `photos`, `segmentations`, `body-parts`, `inbody-temp` (전부 private)
3. **DB 스키마 적용** — Supabase SQL 에디터에서 `db/schema.sql` 실행 (이후 변경은 `db/migrations/`)
4. **마스터 데이터 시드** — `python scripts/seed_body_parts.py`
5. **모델 가중치 다운로드** — `python scripts/download_sapiens.py --size 1b`

---

## 개발 팁

- 포매터: `black --check .` + `isort --check-only .` (설치는 `pip install -r requirements-dev.txt` — 런타임 의존성과 분리돼 있다)
- `scripts/verify_*.py` 는 단위별 점검, `scripts/smoke_*.py` 는 API 를 실제로 때리는 흐름 점검이다.
- 포즈 임계값은 `.env` 로만 만진다. 산식과 각 값의 근거는 [docs/pose-scoring.md](docs/pose-scoring.md).
- 제거된 설정·코드가 왜 없어졌는지는 [docs/removed-code.md](docs/removed-code.md).
