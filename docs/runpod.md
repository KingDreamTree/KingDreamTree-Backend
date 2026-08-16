# RunPod GPU 워커 운영

| | |
|---|---|
| **최종 수정일** | 2026-08-17 |
| **목적** | Sapiens2 **1b** 세그멘테이션을 GPU에서 돌리기 |
| **담당** | A |

> ⚠️ **서비스 모델은 1b로 고정 (2026-08-17).** 5b는 특정 사진에서 결과가 완파되는
> 품질 붕괴(fp16 수치 문제 의심)가 재현돼 폐기했다. 같은 사진이 1b에서는 전 부위
> 정상 검출. 볼륨의 5b 가중치(19GB)도 삭제했다. 5b로 되돌리려면 품질 검증부터.

---

## 왜 RunPod인가

1b도 **GPU가 필요합니다.** EC2 t3.large(2 vCPU, RAM 7.6GB, GPU 없음)에서는 CPU
추론이 장당 분 단위인 데다(노트북급 CPU 실측 25초/장) fp32 로드에 RAM ~6GB가
필요해 API와 같이 돌리면 OOM 위험이 있습니다.

Phase 0에서 만든 잡 큐 덕에 **워커를 다른 machine에 둬도 코드 변경이 없습니다.** 워커는 Supabase(job 테이블 + Storage)하고만 대화합니다.

```
EC2 (API + LLM 워커, CPU)  ─┐
                            ├─→ Supabase ←─ 유일한 접점
RunPod GPU 팟 (세그 워커)   ─┘
```

---

## 팟 사양

**1b 기준 VRAM 8GB면 충분합니다** (2000 Ada 16GB 실측 피크 ~5GB, 추론 0.5~1.1초/장).

| 우선순위 | GPU | 시세(EU-RO-1) | 비고 |
|---|---|---|---|
| 1 | **RTX 2000 Ada 16GB** | **$0.24/hr** | 표준. 실측 검증 완료 (2026-08-17) |
| 2 | L4 24GB | $0.49/hr | 1이 품절일 때 |
| 3 | RTX PRO 4000 | $0.57/hr | |
| 4 | RTX PRO 4500 | $0.72/hr | 재고가 거의 항상 있는 안전판 |

> 4090(24GB, $0.74/hr)은 5b 시절 사양이다. 1b에는 과사양이라 더 쓰지 않는다.
> ⚠️ 볼륨이 **EU-RO-1**에 있으므로 팟도 EU-RO-1에서 골라야 한다. 아시아 DC는
> 시세는 비슷하나 만성 품절(2026-08-17 확인)이라 볼륨 이전 금지.

**디스크** — 1b 가중치 5.5GB + 리포. 기존 Network Volume 60GB를 그대로 쓴다
(사용량 ~7GB, 5b는 삭제됨).

---

## ⚠️ 반드시 지킬 것 3가지

### 1. 가중치는 반드시 볼륨에

컨테이너 디스크는 **휘발성**입니다. 볼륨 없이 띄우면 팟을 다시 만들 때마다 **가중치(1b 5.9GB)를 재다운로드**합니다.

볼륨은 두 종류이고, **GPU 기종에 따라 선택지가 다릅니다.**

| | Network Volume | Volume Disk (팟 볼륨) |
|---|---|---|
| 팟 **중지(Stop)** 후 | 유지 | **유지** |
| 팟 **종료(Terminate)** 후 | **유지** | ❌ **삭제** |
| 다른 팟에 재사용 | 가능 | 불가 |
| 지원 | 특정 데이터센터만 | 대부분 |

- **Network Volume이 되면** 그쪽이 낫습니다. 팟을 종료해도 가중치가 남아 GPU 요금이 완전히 끊깁니다.
  ⚠️ 볼륨은 리전에 묶입니다. **볼륨을 먼저 만들고 그 리전의 GPU를 고르세요.** 순서가 반대면 붙일 수가 없습니다.
- **Volume Disk만 되면** (예: A40) 작업 후 **Stop**만 하세요. **Terminate를 누르면 가중치가 사라집니다.**
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

모델을 프로세스마다 하나씩 들고 있으므로 두 개 띄우면 VRAM이 두 배입니다.

---

## 설정

### 환경변수

```bash
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=sb_secret_...

MODEL_DIR=/workspace/models                 # ⚠️ 볼륨 경로
HF_HOME=/workspace/.cache/huggingface       # ⚠️ 이걸 놓치면 컨테이너 디스크가 터진다
SAPIENS_SIZE=1b                   # 기본값도 1b지만 명시해 두는 편이 사고를 막는다
SAPIENS_DEVICE=cuda
SAPIENS_DTYPE=float16

SEG_WORKER_CONCURRENCY=1
```

### 디스크 배분

| | 권장 | 들어가는 것 |
|---|---|---|
| **Network Volume** (`/workspace`) | 기존 60 GB 유지 | 1b 가중치 5.5GB + 리포 + 캐시 |
| **Container Disk** | 30 GB (기본값) | 베이스 이미지 + pip 설치분 (~15–18GB) |

⚠️ **`MODEL_DIR`과 `HF_HOME`을 반드시 `/workspace` 아래로 두세요.**
컨테이너 디스크는 팟을 종료하면 사라집니다. 여기에 가중치가 들어가면 매번 재다운로드해야 하고,
HuggingFace 캐시까지 겹치면 컨테이너 디스크 용량을 넘겨 설치가 실패합니다.

### 최초 1회

```bash
git clone https://github.com/KingDreamTree/KingDreamTree-Backend.git
cd KingDreamTree-Backend
bash scripts/install_runpod.sh     # 의존성 + 가중치 다운로드 (1b는 5.9GB)
```

> ⚠️ 팟을 **재시작**할 때도 컨테이너가 초기화되므로 `install_runpod.sh`는 다시
> 돌려야 한다 (가중치는 볼륨에 있어 재다운로드는 안 한다, pip만 몇 분).

### 워커 기동 — 무인 상주 (표준)

터미널·Jupyter 탭을 닫아도 유지되고, 워커가 죽으면 5초 뒤 스스로 재기동한다:

```bash
cd /workspace/KingDreamTree-Backend
nohup bash -c 'while true; do python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER >> /workspace/worker.log 2>&1; echo "[$(date)] worker died, restarting" >> /workspace/worker.log; sleep 5; done' > /dev/null 2>&1 &
```

```bash
tail -f /workspace/worker.log      # 로그 확인
ps aux | grep app.worker           # 생존 확인
pkill -f app.worker.run            # 내릴 때 (루프까지 죽이려면 pkill -f "while true" 도)
```

⚠️ 상주 운영은 **잔액이 생명선**이다. 잔액 0 = RunPod이 팟 강제 정지 = 서비스
정지. $0.24/hr 기준 하루 $5.76씩 소진되니 운영 기간만큼 미리 충전할 것.

---

## 운영 메모

**콜드 스타트** — 워커 기동 후 **첫 잡**만 모델 로딩 때문에 ~35초 걸리고, 이후에는
잡당 ~10초(추론 0.5~1초)다. 상주 운영이면 사용자는 항상 예열 상태를 만난다.

**비용** — 밀리초 단위 과금. 2000 Ada 상주 기준 하루 $5.76. 개발만 할 때는 끄고
로컬 GPU 워커를 쓰는 게 싸다 (`docs/local-gpu-setup.md`).

**동결 운영 (2026-08-20~)** — 8/20 이후 무개입 방침. 팟은 Stop 하지 않고 상주,
잔액만 대시보드에서 하루 한 번 확인한다.

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

로컬(RTX 5060, VRAM 8GB)에서도 **1b가 그대로** 돌아갑니다(추론 2.2초/장,
`docs/local-gpu-setup.md`). 팟을 끄고 개발할 때는 로컬 워커로 대체하면 됩니다.

⚠️ **워커는 한 시점에 한 곳만.** 잡 큐가 공유라서 팟·A 로컬·B 로컬 워커가 동시에
뜨면 잡을 나눠 먹고, 서로 다른 크기로 떠 있으면 한 세션의 두 사진을 다른 모델이
처리하는 품질 사고가 난다 (2026-08-16/17 실제 발생: 5b/1b 혼재, 0.4b 가로채기).
켜기 전에 팀에 선언하고, 서비스 모델은 **1b로 통일**한다.

---

## 라벨 매핑 (⚠️ 최초 1회 필수)

`config.json`의 `id2label`이 `LABEL_0`…`LABEL_28` 플레이스홀더라, **어느 픽셀 값이 어느 부위인지 모델 파일만으로는 알 수 없습니다.** 검증 전에는 워커가 실행을 거부합니다.

```bash
python scripts/verify_labels.py --image <정면 전신 사진>
```

결과를 보고 `app/services/sapiens_labels.py`의 `VERIFIED_ORDER`에 반영하세요. 자세한 배경은 그 파일의 docstring에 있습니다.
