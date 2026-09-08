"""사진 → MediaPipe 포즈 랜드마크 JSON (프론트가 보내는 것과 같은 형식).

    python scripts/pose_landmarks.py photos/123.jpg --out out/landmarks/123.json

왜 있나
    서버는 MediaPipe 를 돌리지 않는다 — 랜드마크는 프론트가 잰다. 그래서 로컬 스모크는
    더미 랜드마크(인물이 정중앙)를 쓰는데, 얼굴 가림(services/face_mask)은 랜드마크로
    얼굴을 잡으므로 더미로는 실제 얼굴을 못 덮는다. **얼굴 가림 실측(원본 vs 가림
    진단 비교)** 을 하려면 진짜 랜드마크가 필요하다. 이 스크립트가 그걸 만든다.

필요한 것
    pip install mediapipe   (검증 전용 — requirements 에 넣지 않는다)
    모델 파일은 처음 한 번 내려받아 out/ 에 둔다 (약 30MB).

출력
    [{"index": 0..32, "x": 0~1, "y": 0~1, "z": float, "visibility": 0~1}, ...] — 33개.
    거울 사진이면 MediaPipe 가 보는 그대로다 (되돌리기는 API 가 is_mirrored 로 한다).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", default=None, help="저장 경로. 없으면 stdout")
    ap.add_argument("--model", default="out/pose_landmarker_heavy.task")
    args = ap.parse_args()

    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except ImportError:
        print("mediapipe 가 없습니다: pip install mediapipe  (검증 전용)")
        return 2

    model = Path(args.model)
    if not model.is_file():
        model.parent.mkdir(parents=True, exist_ok=True)
        print(f"모델 내려받는 중 → {model}")
        urllib.request.urlretrieve(MODEL_URL, model)

    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.IMAGE,
        num_poses=1,
    )
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        image = mp.Image.create_from_file(args.image)
        result = landmarker.detect(image)

    if not result.pose_landmarks:
        print(f"사람을 못 찾음: {args.image}")
        return 1
    lms = [
        {
            "index": i,
            "x": round(p.x, 5),
            "y": round(p.y, 5),
            "z": round(p.z, 5),
            "visibility": round(p.visibility, 3),
        }
        for i, p in enumerate(result.pose_landmarks[0])
    ]
    text = json.dumps(lms, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        face = [p for p in lms if p["index"] <= 10 and p["visibility"] >= 0.5]
        print(f"{args.image} → {args.out}  (얼굴 점 {len(face)}/11 보임)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
