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

> ⚠️ 기본값이 `5b` 다. 로컬에 받아둔 건 `0.4b` 뿐이고, 5b 는 가중치만 ~9.5GB 라
> 12GB VRAM 에서 빠듯하다. 이 줄이 없으면 "모델 폴더 없음" 으로 죽는다.

## 확인

```bash
.venv/Scripts/python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_capability(0))"
# True (12, 0)  ← sm_120 이 나와야 Blackwell 커널이 맞다
```

## 세그 워커 띄우기

```bash
python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER
```

LLM 워커와 **따로** 띄운다 (`app/worker/run.py` 주석 — 모델 1.6GB 를 LLM 워커가
들고 있을 이유가 없다).

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
