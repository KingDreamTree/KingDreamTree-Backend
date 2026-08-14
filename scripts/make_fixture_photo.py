"""테스트용 합성 사진 생성 — 라벨 맵에서 만든다.

    python scripts/make_fixture_photo.py

━━ 왜 실사진을 쓰지 않는가 ━━

리포에 사진을 넣는 것은 **재배포**다. 온라인에서 구한 사진은 출처·라이선스를
알 수 없고, 사람이 찍힌 사진이면 git 히스토리에 영구히 남는다 (파일을 나중에
지워도 커밋에는 남아 히스토리 재작성이 필요하다).

그런데 스모크에서 사진의 역할을 보면 실제 인체일 필요가 없다:

    · Storage 업로드 → 다운로드가 되는지            ← 바이트면 충분
    · 오버레이 합성이 좌표대로 되는지               ← 맵과 크기만 맞으면 됨
    · 진단 품질                                     ← 라벨 맵이 결정한다
    · mock 모드에서는 VLM 이 이미지를 보지도 않는다

그래서 **라벨 맵에서 합성**한다. 부위별로 다른 색을 칠하므로 맵과 좌표가
정확히 일치하고, 오버레이·하이라이트 검증에는 실사진과 동등하다.

⚠️ 다만 **근육 윤곽·질감이 없다.** `--live-llm` 으로 진단문 품질을 볼 때는
   실사진이 필요하다. 그때는 각자 로컬에 두고 쓰되 **커밋하지 않는다.**
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
MAP_PATH = FIXTURES / "sample-map.png"
OUT_PATH = FIXTURES / "sample-photo.jpg"

#: 살구빛 기준색. 부위마다 여기서 흔들어 서로 구분되게 한다.
_BASE_TONE = (214.0, 178.0, 154.0)
_BACKGROUND = (238, 240, 244)

#: 고정 시드 — 같은 맵이면 항상 같은 사진이 나와야 재현이 된다.
_SEED = 42


def main() -> int:
    if not MAP_PATH.exists():
        print(f"라벨 맵이 없습니다: {MAP_PATH}")
        return 1

    labels = np.array(Image.open(MAP_PATH))
    height, width = labels.shape

    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:] = _BACKGROUND

    rng = np.random.default_rng(_SEED)
    painted = 0
    for label in np.unique(labels):
        if label == 0:  # 배경
            continue
        tone = np.clip(np.array(_BASE_TONE) + rng.uniform(-38, 38, size=3), 40, 245)
        rgb[labels == label] = tone.astype(np.uint8)
        painted += 1

    # 경계를 살짝 흐려 JPEG 압축 아티팩트가 라벨 경계와 겹치지 않게 한다.
    image = Image.fromarray(rgb, "RGB").filter(ImageFilter.GaussianBlur(1.2))
    image.save(OUT_PATH, quality=88)

    print(
        f"생성: {OUT_PATH.name}  {image.size}  부위 {painted}개  {OUT_PATH.stat().st_size // 1024}KB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
