"""노트북 GPU 점검/시연 워커. check는 DB·Storage·OpenAI를 호출하지 않는다.

    python scripts/local_gpu.py check
    python scripts/local_gpu.py run

run은 공유 DB의 SEG_REFERENCE/SEG_USER 작업을 실제로 처리한다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_gpu(repeat: int) -> dict:
    import torch
    from PIL import Image

    from app.config import settings
    from app.services import sapiens_labels, segmenter

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU를 사용할 수 없습니다. 로컬 GPU 설치와 드라이버를 확인하세요.")
    if segmenter.resolve_device() != "cuda":
        raise RuntimeError(
            "SAPIENS_DEVICE를 auto 또는 cuda로 설정하세요. CPU 시연은 실행하지 않습니다."
        )
    if not Path(segmenter.model_path()).is_dir():
        raise RuntimeError("모델 폴더가 없습니다. MODEL_DIR과 기존 모델 파일을 확인하세요.")
    sapiens_labels.ensure_supported(segmenter.model_version())
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    print("Loading the existing model and checking local inference...", flush=True)
    segmenter.load_model()
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started
    timings = []
    # 실제 사진이 아닌 합성 이미지로 메모리/실행 여부만 확인한다. 품질 검사가 아니다.
    sample = Image.new("RGB", (768, 1024), (96, 96, 96))
    for _ in range(repeat):
        labels, classes, elapsed = segmenter.infer_labels(sample)
        timings.append(elapsed)
    return {
        "device": torch.cuda.get_device_name(0),
        "model": segmenter.model_version(),
        "photo_pipeline": settings.photo_pipeline,
        "load_seconds": round(load_seconds, 2),
        "inference_ms": timings,
        "map_height": labels.shape[0],
        "map_width": labels.shape[1],
        "classes": classes,
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2),
        "uses_real_photos": False,
        "external_services_called": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run"))
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=2)
    args = parser.parse_args(argv)
    # .env와 상대 모델 경로는 저장소 기준. 임의 위치에서 실행해도 설정이 바뀌지 않는다.
    os.chdir(ROOT)
    from app.config import settings

    if args.command == "run" and settings.photo_pipeline != "storage":
        print("PHOTO_PIPELINE=storage가 필요합니다. 현재 설정에서는 작업을 가져오지 않습니다.")
        return 1
    try:
        report = check_gpu(args.repeat)
    except Exception as exc:
        print(f"Local GPU check failed ({type(exc).__name__}): {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.command == "check":
        return 0

    from app.schemas.enums import JobKind
    from app.worker.run import run

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    for noisy in ("httpx", "httpcore", "hpack", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    print(
        "Starting SEG_REFERENCE/SEG_USER only. Press Ctrl+C to stop after the current job.",
        flush=True,
    )
    return run([JobKind.SEG_REFERENCE, JobKind.SEG_USER], settings.worker_poll_interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
