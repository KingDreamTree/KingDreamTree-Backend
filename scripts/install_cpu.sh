#!/usr/bin/env bash
# EC2 / CPU 전용 설치.
#
# ⚠️ PyPI 기본 인덱스로 `pip install torch` 하면 Linux에서는 CUDA 빌드(2GB+)를 받습니다.
#    GPU가 없는 인스턴스에서 CUDA 런타임을 통째로 받아 디스크만 날립니다.
#    반드시 CPU 인덱스를 명시해야 합니다.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 공통 의존성 =="
pip install -r requirements.txt

echo
echo "== torch (CPU 빌드) =="
pip install "torch==2.11.0+cpu" --index-url https://download.pytorch.org/whl/cpu

echo
echo "== transformers 계열 =="
pip install \
  "transformers==5.15.0" \
  "safetensors==0.7.0" \
  "huggingface-hub==0.36.0" \
  "accelerate==1.13.0"

echo
echo "== 확인 =="
python - <<'PY'
import torch

print(f"torch {torch.__version__}")
print(f"CUDA 사용 가능: {torch.cuda.is_available()}  (CPU 전용 환경이면 False가 정상)")
PY

echo
echo "완료."
echo "⚠️ 이 환경에서는 5b를 돌리지 마세요. CPU 추론은 이미지 한 장에 수십 분 걸립니다."
echo "   EC2는 API 서버 전용이고, 세그멘테이션 워커는 GPU 환경(RunPod)에서 돌립니다."
