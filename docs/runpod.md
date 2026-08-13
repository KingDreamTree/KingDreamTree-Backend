# RunPod GPU 워커 운영

| | |
|---|---|
| **최종 수정일** | 2026-08-13 |
| **목적** | Sapiens2 5b 세그멘테이션을 GPU에서 돌리기 |
| **담당** | A |

---

## 왜 RunPod인가

Sapiens2 **5b는 fp16 가중치만 ~9.5GB**입니다. EC2 t3.large(메모리 8GB, GPU 없음)에서는 돌아가지 않고, CPU 추론이라 메모리를 늘려도 이미지 한 장에 분 단위입니다.

Phase 0에서 만든 잡 큐 덕에 **워커를 다른 machine에 둬도 코드 변경이 없습니다.** 워커는 Supabase(job 테이블 + Storage)하고만 대화합니다.

```
EC2 (API + LLM 워커, CPU)  ─┐
                            ├─→ Supabase ←─ 유일한 접점
RunPod GPU 팟 (세그 워커)   ─┘
```

---

## 팟 사양

| 모델 | fp16 가중치 | 활성값 여유 포함 | 권장 GPU |
|---|---|---|---|
| 0.4b | ~0.8 GB | 4 GB | 아무거나 |
| 1b | ~2.7 GB | 8 GB | RTX 3090 등 |
| **5b** | **~9.5 GB** | **16 GB 이상** | **RTX 4090 (24GB) 권장** |

> ⚠️ 16GB(T4/A4000)면 5b가 **빠듯합니다.** 1024×768 입력에 56레이어라 활성값이 큽니다. 24GB를 권합니다.

**디스크** — 5b 가중치가 19GB입니다. Network Volume 최소 40GB.

---

## ⚠️ 반드시 지킬 것 3가지

### 1. 가중치는 반드시 볼륨에

컨테이너 디스크는 **휘발성**입니다. 볼륨 없이 띄우면 팟을 다시 만들 때마다 **19GB를 재다운로드**합니다.

볼륨은 두 종류이고, **GPU 기종에 따라 선택지가 다릅니다.**

| | Network Volume | Volume Disk (팟 볼륨) |
|---|---|---|
| 팟 **중지(Stop)** 후 | 유지 | **유지** |
| 팟 **종료(Terminate)** 후 | **유지** | ❌ **삭제** |
| 다른 팟에 재사용 | 가능 | 불가 |
| 지원 | 특정 데이터센터만 | 대부분 |

- **Network Volume이 되면** 그쪽이 낫습니다. 팟을 종료해도 가중치가 남아 GPU 요금이 완전히 끊깁니다.
  ⚠️ 볼륨은 리전에 묶입니다. **볼륨을 먼저 만들고 그 리전의 GPU를 고르세요.** 순서가 반대면 붙일 수가 없습니다.
- **Volume Disk만 되면** (예: A40) 작업 후 **Stop**만 하세요. **Terminate를 누르면 19GB가 사라집니다.**
  두 버튼이 UI에서 나란히 있어 실수하기 쉽습니다.

```
Volume Mount Path = /workspace
MODEL_DIR=/workspace/models
HF_HOME=/workspace/.cache/huggingface
```

### 2. secret 키는 환경변수로만

워커가 Supabase에 붙어야 하므로 `SUPABASE_SERVICE_ROLE_KEY`가 팟에 들어갑니다. **RLS를 전부 우회하는 키**입니다.

- 이미지에 굽지 마세요. 팟 환경변수로 주입
- 팟을 공개(public) 설정으로 두지 마세요
- 팟을 삭제해도 키는 그대로 유효합니다. 유출이 의심되면 **Supabase에서 키를 재발급**하세요

### 3. 세그 워커는 팟당 하나만

모델을 프로세스마다 하나씩 들고 있으므로 두 개 띄우면 VRAM이 두 배입니다. 5b면 바로 OOM입니다.

---

## 설정

### 환경변수

```bash
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=sb_secret_...

MODEL_DIR=/workspace/models                 # ⚠️ 볼륨 경로
HF_HOME=/workspace/.cache/huggingface       # ⚠️ 이걸 놓치면 컨테이너 디스크가 터진다
SAPIENS_SIZE=5b
SAPIENS_DEVICE=cuda
SAPIENS_DTYPE=float16             # 5b는 fp32면 19GB — 반드시 fp16

SEG_WORKER_CONCURRENCY=1
```

### 디스크 배분

| | 권장 | 들어가는 것 |
|---|---|---|
| **Volume Disk** (`/workspace`) | **60 GB** | 가중치 19GB + 비교용 다른 크기 + 캐시 |
| **Container Disk** | 30 GB (기본값) | 베이스 이미지 + pip 설치분 (~15–18GB) |

⚠️ **`MODEL_DIR`과 `HF_HOME`을 반드시 `/workspace` 아래로 두세요.**
컨테이너 디스크는 팟을 종료하면 사라집니다. 여기에 19GB가 들어가면 매번 재다운로드해야 하고,
HuggingFace 캐시까지 겹치면 컨테이너 디스크 용량을 넘겨 설치가 실패합니다.

### 최초 1회

```bash
git clone https://github.com/KingDreamTree/KingDreamTree-Backend.git
cd KingDreamTree-Backend
bash scripts/install_runpod.sh     # 의존성 + 가중치 다운로드 (5b는 19GB, 오래 걸림)
```

### 워커 기동

```bash
python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
```

`tmux`로 띄워두면 SSH가 끊겨도 유지됩니다. 세션 이름은 EC2와 겹치지 않게 `seg`로 하세요.

```bash
tmux new -s seg
python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
# Ctrl+B, D 로 빠져나오기
```

---

## 운영 메모

**콜드 스타트** — 팟 부팅 + 19GB를 VRAM에 올리는 데 수 분 걸립니다. 시연 중에 팟이 꺼져 있으면 첫 요청이 몇 분 걸립니다. **시연 시간대에는 미리 켜두세요.**

**비용** — 시간당 과금입니다. 개발·시연 때만 켜고 끄면 실제 비용은 크지 않습니다.

### 좀비 잡 회수 (구현됨)

`PROCESSING`인 채로 팟을 끄면 그 잡은 그대로 두면 **영영 안 끝납니다.** `attempts`는 이미 올라갔고 `status`는 `PROCESSING`이라 `claim()`이 다시 집지 않기 때문입니다. 사용자는 로딩 화면에서 무한정 기다립니다.

워커가 이걸 스스로 정리합니다.

- **기동 직후 한 번** + 이후 `JOB_RECLAIM_INTERVAL_SEC`(기본 60초)마다
- `started_at`이 `JOB_STALE_AFTER_SEC`(기본 900초 = 15분) 이상 지난 `PROCESSING` 잡이 대상
- 재시도 여력이 남았으면 `PENDING`으로 되살리고, **`attempts`가 한도에 닿았으면 `FAILED`로 종결**합니다
- **자기가 처리하는 `kind`만** 건드립니다

> ⚠️ **`attempts`가 소진된 잡을 `PENDING`으로 되돌리면 안 됩니다.** `claim()`이 `attempts < JOB_MAX_ATTEMPTS`로 거르므로 아무도 집지 않는 채 `PENDING`으로 남습니다. 좀비의 상태만 바뀔 뿐입니다.

> ⚠️ **`JOB_STALE_AFTER_SEC`를 가장 오래 걸리는 잡보다 짧게 잡지 마세요.** 멀쩡히 돌고 있는 잡을 회수해 같은 일을 두 번 하게 됩니다. VLM이면 요금도 두 배입니다.

**그래서 팟은 그냥 꺼도 됩니다.** 다음 기동 때 자기가 남긴 좀비를 회수합니다. 다만 15분을 기다려야 하므로, 시연 직전이라면 끄기 전에 진행 중인 잡이 없는지 보는 편이 빠릅니다.

**모델 크기 변경** — `SAPIENS_SIZE`만 바꾸고 워커를 재시작하면 됩니다. 스키마·코드 변경 없습니다. 기존 데이터는 `model_version`이 행마다 남아 있어 어느 모델로 뽑았는지 구분됩니다. 다만 **섞어 쓰면 품질이 들쭉날쭉해지므로** 바꾼 뒤에는 재추론을 권합니다.

---

## 로컬 개발과의 관계

로컬(RTX 5060, VRAM 8GB)에서는 **1b까지** 돌아갑니다. 개발·디버깅은 로컬 0.4b로 빠르게 하고, RunPod은 5b 검증과 시연에만 쓰는 게 효율적입니다.

```bash
# 로컬
SAPIENS_SIZE=0.4b SAPIENS_DEVICE=cuda python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
```

`segmenter.py` 코드는 크기·장비와 무관합니다.

---

## 라벨 매핑 (⚠️ 최초 1회 필수)

`config.json`의 `id2label`이 `LABEL_0`…`LABEL_28` 플레이스홀더라, **어느 픽셀 값이 어느 부위인지 모델 파일만으로는 알 수 없습니다.** 검증 전에는 워커가 실행을 거부합니다.

```bash
python scripts/verify_labels.py --image <정면 전신 사진>
```

결과를 보고 `app/services/sapiens_labels.py`의 `VERIFIED_ORDER`에 반영하세요. 자세한 배경은 그 파일의 docstring에 있습니다.
