#!/usr/bin/env bash
# RunPod GPU 워커 설치 + 기동 준비.
#
# 세그멘테이션 워커만 여기서 돌린다. API 서버는 EC2에 남는다.
# 워커는 Supabase(job 테이블 + Storage)하고만 대화하므로 API와 같은 machine일 필요가 없다.
#
# 전제
#   - RunPod 팟 이미지에 CUDA 12.8+ 와 python 3.11 이 있을 것
#   - Network Volume 을 /workspace 에 붙였을 것  ← ⚠️ 안 붙이면 팟 재시작마다 19GB 재다운로드
#   - 환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SAPIENS_SIZE, MODEL_DIR
set -euo pipefail

cd "$(dirname "$0")/.."

: "${MODEL_DIR:=/workspace/models}"
: "${SAPIENS_SIZE:=5b}"

# ⚠️ HuggingFace 캐시를 볼륨으로 돌린다.
#    기본값은 ~/.cache/huggingface 인데 홈은 컨테이너 디스크다.
#    거기 캐시가 쌓이면 (1) 팟 종료 시 사라지고 (2) 컨테이너 디스크가 터진다.
export HF_HOME="${HF_HOME:-/workspace/.cache/huggingface}"
mkdir -p "$HF_HOME"

echo "== 환경 =="
echo "  MODEL_DIR    : $MODEL_DIR"
echo "  HF_HOME      : $HF_HOME"
echo "  SAPIENS_SIZE : $SAPIENS_SIZE"
echo "  디스크 여유  : $(df -h /workspace 2>/dev/null | awk 'NR==2{print $4}' || echo '?')"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || {
  echo "⚠️ nvidia-smi 실패 — GPU 팟이 맞는지 확인하세요."
  exit 1
}

if [[ "$MODEL_DIR" != /workspace/* ]]; then
  echo
  echo "⚠️ MODEL_DIR이 /workspace 아래가 아닙니다."
  echo "   RunPod 컨테이너 저장소는 휘발성이라 팟을 재시작하면 가중치가 사라집니다."
  echo "   Network Volume을 붙이고 MODEL_DIR=/workspace/models 로 두세요."
fi

echo
echo "== 공통 의존성 =="
pip install -r requirements.txt

echo
echo "== torch (팟 이미지의 CUDA에 맞춰) =="
# 팟 이미지에 torch가 이미 있으면 건너뛴다. 덮어쓰면 CUDA 버전이 어긋날 수 있다.
if python -c "import torch" 2>/dev/null; then
  python -c "import torch; print(f'  기존 torch {torch.__version__} 사용')"
else
  pip install "torch==2.11.0+cu128" --index-url https://download.pytorch.org/whl/cu128
fi

echo
echo "== transformers 계열 =="
pip install \
  "transformers==5.15.0" \
  "safetensors==0.8.0" \
  "huggingface-hub==1.27.0" \
  "accelerate==1.14.0"

echo
echo "== 가중치 =="
if [ -f "$MODEL_DIR/sapiens2-seg-$SAPIENS_SIZE/model.safetensors" ]; then
  echo "  이미 있음 — 건너뜀"
else
  echo "  다운로드 시작 (5b는 약 19GB, 시간이 걸립니다)"
  MODEL_DIR="$MODEL_DIR" python scripts/download_sapiens.py --size "$SAPIENS_SIZE"
fi

echo
echo "== 확인 =="
python - <<'PY'
import sys

import torch

print(f"torch {torch.__version__}  CUDA {torch.version.cuda}")
if not torch.cuda.is_available():
    sys.exit("⚠️ GPU 인식 실패")

p = torch.cuda.get_device_properties(0)
cap = torch.cuda.get_device_capability(0)
vram = p.total_memory / 1024**3
print(f"GPU: {p.name}  VRAM {vram:.1f} GB  compute capability sm_{cap[0]}{cap[1]}")

built = torch.cuda.get_arch_list()
print(f"이 torch가 지원하는 아키텍처: {', '.join(built)}")

# ⚠️ is_available()이 True여도 이 GPU용 커널이 없으면 연산에서 죽는다.
#    "no kernel image is available for execution on the device"
#    RTX 50 시리즈 / RTX PRO (Blackwell, sm_120)에서 실제로 자주 겪는 문제라
#    설치 단계에서 진짜 연산을 한 번 돌려 확인한다.
try:
    a = torch.randn(64, 64, device="cuda", dtype=torch.float16)
    _ = (a @ a).sum().item()
    torch.cuda.synchronize()
    print("[O] GPU 연산 테스트 통과 (fp16 matmul)")
except Exception as e:
    print(f"[X] GPU 연산 실패: {type(e).__name__}: {e}")
    print()
    print("    이 torch 빌드가 현재 GPU 아키텍처를 지원하지 않습니다.")
    print("    CUDA 12.8 이상 빌드로 다시 설치하세요:")
    print("      pip install --force-reinstall 'torch==2.11.0+cu128' \\")
    print("        --index-url https://download.pytorch.org/whl/cu128")
    sys.exit(1)

if vram < 15:
    print("⚠️ 5b fp16은 가중치만 ~9.5GB입니다. 활성값까지 하면 이 VRAM으로는 빠듯합니다.")
elif vram < 22:  # 24GB 카드는 실제로 23.5GB 정도로 보고된다
    print("⚠️ VRAM 여유가 크지 않습니다. OOM이 나면 SAPIENS_SIZE=1b 로 내리거나")
    print("   SAPIENS_OFFLOAD=true 를 쓰세요.")
PY

echo
echo "완료. 워커 기동:"
echo "  python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER"
