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

기본값은 1b — 서비스 표준 백본이다 (2026-08-17 팀 합의, app/config.py sapiens_size
   와 일치). 예전 기본값 0.4b는 "EC2 CPU 추론" 시절 근거였는데, 지금 세그는 GPU
   워커(RunPod/로컬)가 1b로 돌린다.

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

from app.services.sapiens_weights import ALLOW_PATTERNS, REPOS, ensure  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Sapiens2 세그멘테이션 가중치 다운로드")
    parser.add_argument(
        "--size",
        default="1b",
        choices=sorted(REPOS),
        help="백본 크기 (기본 1b — 서비스 표준, app/config.py와 일치)",
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
    print(f"레포     : {repo_id}")
    print(f"저장 위치: {os.path.abspath(os.path.join(model_dir, f'sapiens2-seg-{args.size}'))}")
    print(f"패턴     : {', '.join(ALLOW_PATTERNS)}")
    print()
    print("다운로드를 시작합니다. 백본 크기에 따라 수 GB이며 시간이 걸립니다...")
    print()
    ensure(args.size, model_dir)
    print("완료")


if __name__ == "__main__":
    main()
