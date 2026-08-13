"""Sapiens2 body-part segmentation 가중치 다운로드.

사용법:
    pip install huggingface-hub
    python scripts/download_sapiens.py            # 기본: 1b
    python scripts/download_sapiens.py --size 0.4b
    python scripts/download_sapiens.py --list     # 받지 않고 파일 목록만 확인

모델 출처
    https://huggingface.co/facebook/sapiens2
    Meta AI. body-part segmentation 29클래스 (Sapiens 28 + Eyeglasses).

⚠️ 라이선스는 "Sapiens2 License"입니다 (CC BY 4.0 아님 — 그건 논문 라이선스).
   상업적 이용 조건을 반드시 직접 확인하세요:
   https://github.com/facebookresearch/sapiens2/blob/main/LICENSE.md

⚠️ 가중치는 절대 커밋하지 마세요. .gitignore에 *.safetensors / models/* 가 있습니다.
"""

import argparse
import os
import sys

#: 크기별 세그멘테이션 체크포인트 레포
REPOS = {
    "0.4b": "facebook/sapiens2-seg-0.4b",
    "0.8b": "facebook/sapiens2-seg-0.8b",
    "1b": "facebook/sapiens2-seg-1b",
    "5b": "facebook/sapiens2-seg-5b",
}

#: ⚠️ 반드시 좁혀서 받는다. 패턴 없이 snapshot_download를 부르면 레포 전체를 받는다.
ALLOW_PATTERNS = ["*.safetensors", "*.json", "*.txt"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Sapiens2 세그멘테이션 가중치 다운로드")
    parser.add_argument(
        "--size",
        default="1b",
        choices=sorted(REPOS),
        help="백본 크기 (기본 1b). t3.large CPU 추론이면 0.4b도 검토할 것",
    )
    parser.add_argument("--list", action="store_true", help="받지 않고 파일 목록만 출력")
    args = parser.parse_args()

    try:
        from huggingface_hub import list_repo_files, snapshot_download
    except ImportError:
        print("huggingface-hub 가 설치되어 있지 않습니다.")
        print("  pip install huggingface-hub")
        sys.exit(1)

    repo_id = REPOS[args.size]

    if args.list:
        print(f"{repo_id} 파일 목록:")
        for f in sorted(list_repo_files(repo_id)):
            print(f"  {f}")
        return

    model_dir = os.environ.get("MODEL_DIR", "models")
    target = os.path.join(model_dir, "sapiens2", args.size)
    os.makedirs(target, exist_ok=True)

    print(f"레포     : {repo_id}")
    print(f"저장 위치: {os.path.abspath(target)}")
    print(f"패턴     : {', '.join(ALLOW_PATTERNS)}")
    print()
    print("다운로드를 시작합니다. 백본 크기에 따라 수 GB이며 시간이 걸립니다...")
    print()

    snapshot_download(
        repo_id=repo_id,
        local_dir=target,
        allow_patterns=ALLOW_PATTERNS,
    )

    print()
    print(f"완료. {target}")
    print()
    print("다음 단계 — 첫 추론에서 반드시 확인할 것:")
    print("  1. 출력 클래스 개수가 29인지")
    print("  2. 각 픽셀 값 ↔ 클래스명 매핑 (segmentation.label_map 에 저장할 값)")
    print("  3. Eyeglasses 클래스의 정확한 철자")
    print("     → scripts/seed_body_parts.py --check 로 마스터와 대조")


if __name__ == "__main__":
    main()
