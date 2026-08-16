"""Sapiens2 body-part segmentation 가중치 다운로드.

사용법:
    pip install huggingface-hub
    python scripts/download_sapiens.py               # 기본: 0.4b
    python scripts/download_sapiens.py --size 1b
    python scripts/download_sapiens.py --list        # 받지 않고 파일 목록만 확인

    # gated 모델이라 토큰이 필요한 경우
    huggingface-cli login

모델 출처
    https://huggingface.co/facebook/sapiens2
    Meta AI. body-part segmentation 29클래스 (Sapiens 28 + Eyeglass).

⚠️ 기본값이 0.4b인 이유 — EC2 t3.large는 GPU가 없다. CPU 추론이라 백본이 커질수록
   사용자가 로딩 화면에서 기다리는 시간이 그대로 늘어난다. 0.4b로 시작해 품질을 보고
   올리는 편이 안전하다. (0.4b mIoU 79.5 / 5b 82.5)

⚠️ 라이선스는 "Sapiens2 License"다. 논문의 CC BY 4.0과 다르므로 혼동하지 말 것.
   상업적 이용 조건은 직접 확인해야 한다:
   https://github.com/facebookresearch/sapiens2/blob/main/LICENSE.md

⚠️ 가중치는 절대 커밋하지 말 것. .gitignore에 *.safetensors / *.pth / models/* 가 있다.
"""

import argparse
import os
import sys
from pathlib import Path

# app.config 의 settings 로 MODEL_DIR 을 읽기 위해 (셸 export 없이 .env 만 있어도 되게)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 크기별 세그멘테이션 체크포인트 레포
REPOS = {
    "0.4b": "facebook/sapiens2-seg-0.4b",
    "0.8b": "facebook/sapiens2-seg-0.8b",
    "1b": "facebook/sapiens2-seg-1b",
    "5b": "facebook/sapiens2-seg-5b",
}

#: ⚠️ 반드시 좁혀서 받는다.
#
#   레포에는 같은 가중치가 **두 이름으로** 들어 있다 (0.4b 기준 각 1551MB):
#       model.safetensors               ← transformers가 로드하는 이름. 이것만 받는다
#       sapiens2_0.4b_seg.safetensors   ← 원본 명명. 내용 동일
#   `*.safetensors` 로 받으면 3.1GB, 즉 두 배를 받게 된다.
#
#   실제 파일 구성은 --list 로 먼저 확인할 것.
ALLOW_PATTERNS = [
    "model.safetensors",
    "config.json",
    "preprocessor_config.json",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Sapiens2 세그멘테이션 가중치 다운로드")
    parser.add_argument(
        "--size",
        default="0.4b",
        choices=sorted(REPOS),
        help="백본 크기 (기본 0.4b — t3.large CPU 추론 기준)",
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

    # ⚠️ 셸 환경변수만 읽으면 .env 에 MODEL_DIR 을 둔 환경(RunPod 규약)에서
    #    리포 안 상대경로 models/ 에 받아 버리고, 워커는 .env 경로를 찾다 죽는다.
    #    우선순위: 셸 환경변수 > .env(app.config) > 기본값 "models".
    model_dir = os.environ.get("MODEL_DIR")
    if not model_dir:
        try:
            from app.config import settings

            model_dir = settings.model_dir
        except Exception:  # noqa: BLE001 — 의존성 없는 환경이면 기본값으로
            model_dir = "models"
    target = os.path.join(model_dir, f"sapiens2-seg-{args.size}")
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
    print("     → python scripts/seed_body_parts.py --check 로 마스터와 대조")


if __name__ == "__main__":
    main()
