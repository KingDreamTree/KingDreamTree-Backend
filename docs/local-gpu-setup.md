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
