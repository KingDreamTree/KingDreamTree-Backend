# 로컬 GPU 세그멘테이션 — B 혼자 전 구간 돌리기

> 2026-08-14 실측. **A 없이도 세그 → 진단 → 루틴 → 피드백 전 구간이 로컬에서 돈다.**

## 실측 결과 (RTX 5070 12GB · sapiens2-seg-0.4b, 2026-08-14 실사진)

```
맵            768x1024
검출 클래스   18 / 29
유효 비교대상 9 / 9        ← 전 부위 검출
인물 비율     17.3%
옷 병합       허벅지 27% · 몸통 10% 흡수 (정상 동작)
```

> 백본별 속도·VRAM 은 아래 [백본별 실측](#백본별-실측-rtx-5070-12gb-2026-08-15--같은-사진-1장) 표를 볼 것.
> **지금 기본값은 `1b` 다.**

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

가중치 받기 (`.gitignore` 라 clone 에 안 딸려온다):

```bash
python scripts/download_sapiens.py --size 1b
```

⚠️ **Windows 에서는 `HF_HUB_DISABLE_XET=1` 을 붙여라.** `hf_xet` 백엔드가
중간에 조용히 멈춘다 — 프로세스는 살아 있고 CPU 도 쓰는데 파일이 안 커진다
(2026-08-15 실측: 256MB 에서 정지). 일반 HTTP 로 내려받으면 정상이다.

```bash
HF_HUB_DISABLE_XET=1 HF_HUB_DOWNLOAD_TIMEOUT=120 python scripts/download_sapiens.py --size 1b
```

```bash
# ⚠️ RTX 50 시리즈(Blackwell, sm_120)는 CUDA 12.8 이상 빌드가 **필수**.
#    cu121 이하는 torch.cuda.is_available() 이 True 여도 추론이 실패한다.
.venv/Scripts/python.exe -m pip install "torch==2.11.0+cu128" torchvision   --index-url https://download.pytorch.org/whl/cu128

.venv/Scripts/python.exe -m pip install   "transformers==5.15.0" "safetensors==0.8.0"   "huggingface-hub==1.27.0" "accelerate==1.14.0"
```

`.env` 에 한 줄:

```
SAPIENS_SIZE=1b
```

> ⚠️ 기본값이 `5b` 다. 이 줄이 없으면 "모델 폴더 없음" 으로 죽는다.

### 백본별 실측 (RTX 5070 12GB, 2026-08-15 · 같은 사진 1장)

| 크기 | 다운로드 | VRAM 피크 | 추론 | 로컬에서 |
|---|---|---|---|---|
| 0.4b | 1.6GB | 2.1GB | **0.6초** | ✅ 가볍다 |
| 1b | 5.9GB | **4.8GB** | **1.0초** | ✅ **기본값으로 쓴다** |
| 5b | **20.4GB** | **12.5GB** | **376초** | ❌ 못 쓴다 |

**1b 가 12GB 에서 가장 좋은 선택이다.** VRAM 4.8GB 로 절반도 안 쓰고 1초 안에
끝난다. 0.4b 대비 0.4초 더 쓰는 대신 백본이 2.5배 크다.

**5b 는 12GB 카드에서 OOM 이 나지 않는다. 대신 640배 느려진다.**

이게 더 나쁘다 — 죽으면 바로 알아채고 내리는데, 안 죽으니 "되는 줄 알고" 6분을
기다리게 된다. 워커 타임아웃·잡 좀비 판정(`JOB_STALE_AFTER_SEC=900`)에도 걸린다.

원인은 VRAM 피크가 **12.5GB 로 가용치(11.9GB)를 넘겼다**는 데 있다. Windows
WDDM 은 이 초과분을 공유 시스템 메모리로 흘려보내므로 CUDA 가 죽지 않는다.
대신 가중치가 매 레이어마다 PCIe 를 오간다 (그때 프로세스 RSS 가 14.3GB 까지
올라간 것이 증거다). GPU 사용률은 100% 로 보이지만 대부분 전송 대기다.

> ⚠️ **이전 문서의 "5b 가중치 ~9.5GB" 는 틀린 값이었다.** 실제 배포 파일은
> fp32 **20.4GB**, fp16 로드해도 가중치만 ~10.2GB 다.
>
> ⚠️ 배치를 줄이는 튜닝은 소용없다 — 이미 1장씩 추론한다. 해상도를 낮추면
> 활성화는 줄지만 **가중치 10.2GB 자체가 안 줄어든다.**

**결론 — 로컬은 `SAPIENS_SIZE=1b`.** 5b 는 A 의 RunPod(24GB)용이다.

> ⚠️ 다운로드가 `httpx.ReadTimeout` 으로 끊길 수 있다 (1b 5.9GB 에서 실제로 겪음).
> HF 기본 read 타임아웃이 10초라 짧다. **끊기면 받던 조각도 지워져 처음부터**
> 받으므로, 큰 파일은 처음부터 늘려서 시작하는 편이 낫다:
>
> ```bash
> HF_HUB_DISABLE_XET=1 HF_HUB_DOWNLOAD_TIMEOUT=120 python scripts/download_sapiens.py --size 1b
> ```

> 위 22클래스 검출 수치는 **합성 픽스처**(`make_fixture_photo.py` — 라벨 맵에서
> 만든 그림) 기준이라 백본 간 **품질 비교의 근거로 쓸 수 없다.** 근육 윤곽·질감이
> 없어서다. 품질을 견주려면 실사진으로 다시 재야 한다.

## 확인

```bash
.venv/Scripts/python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_capability(0))"
# True (12, 0)  ← sm_120 이 나와야 Blackwell 커널이 맞다
```

## 로컬에서 전 구간 돌리기 — 터미널 3개

워커를 **두 종류로 나눠 띄운다.** 세그 워커만 GPU 모델(1b = 5.9GB)을 들고 있고,
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

⚠️ 다만 **A 의 RunPod 은 5b, 로컬은 1b** 다. 백본이 다르면 검출 품질과
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
