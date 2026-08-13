"""Sapiens2 모델 가중치 다운로드 스크립트.

사용법:
    python scripts/download_sapiens.py

요구사항:
    pip install huggingface-hub  (requirements.txt에 없음 — 개발 도구용)

모델 출처:
    https://huggingface.co/facebook/sapiens
    Meta AI의 Sapiens 시리즈. 상업적 이용 시 라이선스 확인 필수.

다운로드 후 구조:
    models/
    └── sapiens/
        ├── sapiens_1b/          # 1B 파라미터 체크포인트
        │   └── *.pth
        └── sapiens_0.3b/        # 경량 버전 (선택)
            └── *.pth
"""

import os
import sys


def main() -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface-hub 가 설치되어 있지 않습니다.")
        print("  pip install huggingface-hub")
        sys.exit(1)

    model_dir = os.environ.get("MODEL_DIR", "models")
    target = os.path.join(model_dir, "sapiens")
    os.makedirs(target, exist_ok=True)

    print(f"다운로드 위치: {os.path.abspath(target)}")
    print("HuggingFace에서 Sapiens 가중치를 내려받습니다 (수 GB, 시간이 걸릴 수 있습니다)...")
    print()

    # TODO: 실제 사용할 체크포인트 파일명을 확정한 뒤 아래 repo_id와 파일 목록을 업데이트
    # Sapiens2가 공개되면 repo_id 및 파일 경로 조정 필요
    snapshot_download(
        repo_id="facebook/sapiens",
        local_dir=target,
        # allow_patterns=["sapiens_1b/...pth"],  # 특정 파일만 받을 때 주석 해제
        ignore_patterns=["*.md", ".gitattributes"],
    )

    print()
    print(f"완료. {target} 에 가중치가 저장되었습니다.")
    print("이제 USE_MOCK=false 로 서버를 기동할 수 있습니다.")


if __name__ == "__main__":
    main()
