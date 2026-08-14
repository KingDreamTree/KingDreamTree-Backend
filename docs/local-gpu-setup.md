# 로컬 GPU 세그멘테이션 — B 혼자 전 구간 돌리기

> 2026-08-14 실측. **A 없이도 세그 → 진단 → 루틴 → 피드백 전 구간이 로컬에서 돈다.**

## 실측 결과 (RTX 5070 12GB)

```
추론          4.5s   (sapiens2-seg-0.4b, fp16)
맵            768x1024
검출 클래스   18 / 29
유효 비교대상 9 / 9        ← 전 부위 검출
인물 비율     17.3%
옷 병합       허벅지 27% · 몸통 10% 흡수 (정상 동작)
```

## MediaPipe 는 파이썬에 설치하지 않는다

⚠️ **자세 판정은 브라우저에서 돈다.** `web/*.html` 이 CDN 에서
`@mediapipe/tasks-vision` 을 직접 읽어 랜드마크를 뽑고, 서버는 그 좌표만 받는다
(`docs/FRONTEND.md` §4 "자세 판정은 프론트가 계산합니다").

`pip install mediapipe` 는 **필요 없다.** 파이썬 쪽 `app/services/pose.py` 는
넘어온 좌표로 점수만 계산하고, `scripts/verify_pose_mirror.py` 는 MediaPipe 가
좌우를 어떻게 보는지 **흉내 낸 함수**로 검증한다 — 둘 다 라이브러리를 안 쓴다.

자세 판정을 눈으로 보려면 브라우저를 열면 된다:

```bash
python scripts/run_pose_demo.py
```

## 설치 (1회)

가중치는 `models/sapiens2-seg-0.4b/` 에 이미 있다 (1.6GB).

```bash
# ⚠️ RTX 50 시리즈(Blackwell, sm_120)는 CUDA 12.8 이상 빌드가 **필수**.
#    cu121 이하는 torch.cuda.is_available() 이 True 여도 추론이 실패한다.
.venv/Scripts/python.exe -m pip install "torch==2.11.0+cu128" torchvision   --index-url https://download.pytorch.org/whl/cu128

.venv/Scripts/python.exe -m pip install   "transformers==5.15.0" "safetensors==0.8.0"   "huggingface-hub==1.27.0" "accelerate==1.14.0"
```

`.env` 에 한 줄:

```
SAPIENS_SIZE=0.4b
```

> ⚠️ 기본값이 `5b` 다. 이 줄이 없으면 "모델 폴더 없음" 으로 죽는다.

### 백본별 실측 (RTX 5070 12GB, 2026-08-15)

| 크기 | 다운로드 | fp16 가중치 | VRAM 피크 | 추론 | 12GB 에서 |
|---|---|---|---|---|---|
| 0.4b | 1.6GB | 1.6GB | **2.1GB** | 0.6s | ✅ 여유 |
| 5b | **20.4GB** | ~10.2GB | 측정 예정 | — | ⚠️ 빠듯 |

> ⚠️ **이전 문서의 "5b 가중치 ~9.5GB" 는 틀린 값이었다.** 실제 배포 파일은
> fp32 **20.4GB** 이고, fp16 으로 로드해도 가중치만 ~10.2GB 다. 가용 VRAM 이
> 11.9GB 라 활성화 메모리 여유가 1.7GB 밖에 없다 —
> `hidden_size 2432 × 56층`, 입력 1024×768(3072 토큰) 기준으로 OOM 위험이 있다.
>
> 5b 로 OOM 이 나면 `SAPIENS_SIZE=1b` 로 내리는 것이 정답이다. 배치를 줄이는
> 튜닝은 의미가 없다 — 이미 1장씩 추론한다.

## 확인

```bash
.venv/Scripts/python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_capability(0))"
# True (12, 0)  ← sm_120 이 나와야 Blackwell 커널이 맞다
```

## 로컬에서 전 구간 돌리기 — 터미널 3개

워커를 **두 종류로 나눠 띄운다.** 세그 워커만 GPU 모델(1.6GB)을 들고 있고,
LLM 워커는 안 들고 있어야 한다 (`app/worker/run.py` 주석).

### 터미널 1 — 세그 워커 (GPU)

```bash
python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
```

### 터미널 2 — LLM 워커 (진단·루틴)

```bash
python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH
```

> ⚠️ 이 워커는 기동할 때 `exercise_catalog` 를 확인하고, 비어 있으면 **뜨지 않는다.**
> 카탈로그가 빈 채로 돌면 LLM 이 고를 후보가 없어 빈 루틴이 조용히 생성되기 때문이다.
> `python scripts/seed_exercise_catalog.py` 를 먼저 돌린다.

### 터미널 3 — API 서버

```bash
uvicorn app.main:app --reload --port 8000
```

Swagger: http://localhost:8000/docs

---

## 무엇을 어떤 순서로 테스트하나

### 1. 서버 없이 — 로직 검증 (가장 빠름, 키 불필요)

```bash
python scripts/verify_routine_rules.py
python scripts/verify_contraindication.py
python scripts/verify_coach_chat.py
python scripts/verify_routine_build.py
python scripts/verify_routine_mode.py
python scripts/verify_analysis.py
python scripts/verify_exercise_catalog.py
python scripts/verify_ab_contract.py
```

DB·GPU·API 키가 전혀 없어도 돈다. 로직을 고친 뒤 **가장 먼저** 돌릴 것.

### 2. 워커 없이 — 전 구간 스모크 (DB 필요, GPU 불필요)

```bash
python scripts/smoke_full_flow.py              # mock LLM
python scripts/smoke_full_flow.py --no-inbody  # 인바디 없는 경로
python scripts/smoke_routine_api.py
```

핸들러를 직접 호출하므로 워커를 안 켜도 된다. 저장 계약(행 수·소유권·진행 계산)까지 본다.

### 3. 실제 GPU 추론 — 진짜 세그멘테이션

터미널 1(세그 워커)을 켜 둔 상태에서:

```bash
python scripts/smoke_e2e_segmentation.py --image tests/fixtures/sample-photo.jpg
python scripts/smoke_e2e_segmentation.py --image 내사진.jpg --out out/e2e --keep
```

> `--keep` 을 주면 결과를 지우지 않고 남긴다 — Supabase 콘솔에서 눈으로 볼 때.
> 워커를 안 켰으면 잡이 PENDING 에서 안 넘어가고 타임아웃 난다. **실패가 아니라
> "워커를 안 켰다"는 뜻이다.**

### 4. 진짜 LLM 으로 진단문 품질 보기

`.env` 에 `USE_MOCK=false` 와 `OPENAI_API_KEY` 가 있어야 한다 (지금 둘 다 설정됨).

```bash
python scripts/smoke_routine_api.py --live-llm
```

⚠️ 실제 요금이 나간다. 로직 회귀는 위 1·2번으로 보고, 이건 **문장 품질을 볼 때만**.

### 5. 자세 판정 (브라우저)

```bash
python scripts/run_pose_demo.py
```

## 이제 가능해진 것

| 단계 | 전 | 후 |
|---|---|---|
| 사진 업로드 | ✅ | ✅ |
| **세그멘테이션** | ❌ A의 RunPod 필요 | ✅ **로컬 GPU** |
| 진단 → 루틴 → 피드백 | ✅ | ✅ |

전 구간을 혼자 돌릴 수 있으므로, A 와의 합동 세션은 **A 파이프라인 자체의 검증**
(라벨 순서 눈 확인 등)에만 쓰면 된다.

⚠️ 다만 **A 의 RunPod 은 5b, 로컬은 0.4b** 다. 백본이 다르면 검출 품질과
label_map 이 다를 수 있으니, 로컬 결과를 A 환경의 근거로 쓰지 말 것.


---

## 테스트 픽스처 (2026-08-15 리포 안으로 이동)

```
tests/fixtures/sample-photo.jpg    스모크가 Storage 에 올리는 사진
tests/fixtures/sample-map.png      라벨 맵 (라벨 값이 _PARTS 와 일치해야 함)
data/exercise_catalog.json         루틴 후보 (없으면 루틴 생성이 죽는다)
```

⚠️ 이전에는 픽스처가 `../map/map.png` 처럼 **리포 바깥**을 가리켰다. 만든 사람
컴퓨터에서만 돌고, 다른 기계에서는 진단 단계를 통째로 건너뛰면서 그 사실이
경고 한 줄로만 표시됐다 (A 발견). 테스트 픽스처는 코드와 같이 버전 관리한다.

### `data/exercise_catalog.json` 은 gitignore 예외다

```gitignore
/data/*                          # 수집 캐시는 무시 (재수집 가능)
!/data/exercise_catalog.json     # 카탈로그만 예외
```

`/data/` 로 디렉터리째 무시하면 하위 `!` 예외가 **먹지 않는다** — git 이 디렉터리
단계에서 가지치기하기 때문이다. 그래서 `/data/*` 로 파일 단위 무시를 쓴다.

**예외로 둔 이유**: 재수집에 RapidAPI 키가 필요한데, 없으면 루틴 생성이 통째로
죽는다. clone 만으로 돌아가야 EC2 배포와 신규 합류자가 막히지 않는다.

## EC2 배포 체크리스트

```bash
git clone … && cd body-analysis-backend
bash scripts/install_cpu.sh          # torch CPU 빌드 (GPU 없음)
cp .env.example .env && vi .env      # 키 채우기

# ⚠️ 확인 — 이게 없으면 루틴 기능만 조용히 죽는다
test -f data/exercise_catalog.json && echo OK || echo "카탈로그 없음!"

python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 이제 카탈로그가 git 에 포함되므로 **별도 복사가 필요 없다.** 위 `test -f` 는
> 혹시 모를 누락을 잡는 안전망이다.
