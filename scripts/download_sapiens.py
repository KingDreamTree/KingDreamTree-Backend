"""Sapiens2 세그멘테이션 모델 가중치 다운로드 스크립트.

사용법:
    pip install huggingface-hub
    python scripts/download_sapiens.py

    # HuggingFace 토큰이 필요한 경우 (gated model):
    huggingface-cli login
    python scripts/download_sapiens.py

모델 출처:
    https://huggingface.co/facebook/sapiens2-seg-0.4b
    Meta AI Sapiens2 — semantic segmentation 0.4B 파라미터.
    상업적 이용 시 Meta 라이선스 확인 필수.

다운로드 후 구조:
    models/
    └── sapiens2-seg-0.4b/
        └── *.pth  (또는 safetensors)
"""

import os
import sys

REPO_ID = "facebook/sapiens2-seg-0.4b"


def main() -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface-hub 가 설치되어 있지 않습니다.")
        print("  pip install huggingface-hub")
        sys.exit(1)

    model_dir = os.environ.get("MODEL_DIR", "models")
    target = os.path.join(model_dir, "sapiens2-seg-0.4b")
    os.makedirs(target, exist_ok=True)

    print(f"repo  : {REPO_ID}")
    print(f"저장  : {os.path.abspath(target)}")
    print("다운로드 중... (수 GB, 시간이 걸릴 수 있습니다)")
    print()

    snapshot_download(
        repo_id=REPO_ID,
        local_dir=target,
        ignore_patterns=["*.md", ".gitattributes"],
    )

    print()
    print(f"완료. {target} 에 가중치가 저장되었습니다.")
    print("이제 USE_MOCK=false 로 서버를 기동할 수 있습니다.")


if __name__ == "__main__":
    main()
