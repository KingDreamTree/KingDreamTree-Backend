#!/usr/bin/env bash
# 로컬 개발 (NVIDIA GPU) 설치.
#
# ⚠️ RTX 50 시리즈는 Blackwell(sm_120)이라 CUDA 12.8 이상 빌드가 필요합니다.
#    cu121 이하 빌드를 깔면 torch.cuda.is_available()이 True여도
#    "no kernel image is available for execution on the device" 로 추론이 실패합니다.
#
# Windows에서는 PowerShell로:
#   .venv\Scripts\python.exe -m pip install "torch==2.11.0+cu128" --index-url https://download.pytorch.org/whl/cu128
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 공통 의존성 =="
pip install -r requirements.txt

echo
echo "== torch (CUDA 12.8 빌드) =="
pip install "torch==2.11.0+cu128" torchvision --index-url https://download.pytorch.org/whl/cu128

echo
echo "== transformers 계열 =="
pip install \
  "transformers==5.15.0" \
  "safetensors==0.8.0" \
  "huggingface-hub==1.27.0" \
  "accelerate==1.14.0"

echo
echo "== 확인 =="
python - <<'PY'
import torch

print(f"torch {torch.__version__}")
print(f"CUDA 사용 가능: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    cap = torch.cuda.get_device_capability(0)
    print(f"GPU: {name}")
    print(f"VRAM: {vram:.1f} GB   compute capability: sm_{cap[0]}{cap[1]}")
    print()
    print("이 VRAM으로 가능한 백본 (fp16 가중치 기준, docs/local-gpu-setup.md 실측):")
    # ⚠️ 5b는 예전 문서의 9.5GB가 틀린 값이었다 — fp16 로드만 ~10.2GB (실측).
    #    서비스는 1b로 통일됐다 (2026-08-17). 5b는 참고용으로만 표시한다.
    for size, need in (("0.4b", 0.8), ("0.8b", 1.5), ("1b", 2.7), ("5b", 10.2)):
        mark = "가능" if vram > need + 3 else ("빠듯" if vram > need else "불가")
        note = "  ← 서비스 표준" if size == "1b" else ("  (폐기됨)" if size == "5b" else "")
        print(f"  {size:<5} 가중치 ~{need:>4.1f} GB → {mark}{note}")
else:
    print("⚠️ GPU를 인식하지 못했습니다. 드라이버와 CUDA 빌드 버전을 확인하세요.")
PY
