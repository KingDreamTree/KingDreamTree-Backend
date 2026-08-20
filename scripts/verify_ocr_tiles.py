"""_segment_tiles 기하 검증 — API 호출 없이 조각내기 로직만 본다.

깨지면 안 되는 것:
  · 조각 수 = 2×2 (그리드 상수와 일치)
  · 모든 조각이 gpt-4o 상한(짧은 변 768 / 긴 변 2048) 이내 — 넘으면 API가
    다시 줄여서 확대한 의미가 없어진다
  · 확대가 실제로 일어난다 — 이게 이 함수의 존재 이유다 (통짜 결과지에서는
    부위별 표 숫자가 뭉개져 지어낸 값이 나왔다, 2026-08-20 실측)
  · 이미 거대한 이미지는 더 키우지 않는다 (API가 줄일 것까지 미리 뻥튀기 금지)
"""

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ocr import _TILE_GRID, _VLM_LONG, _VLM_SHORT, _segment_tiles


def _jpeg(width: int, height: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "JPEG")
    return buf.getvalue()


# 실제 사고가 난 크기 그대로 — 936×1320 스크린샷
tiles = _segment_tiles(_jpeg(936, 1320))
assert len(tiles) == _TILE_GRID * _TILE_GRID, f"조각 수 {len(tiles)}"
for raw in tiles:
    tw, th = Image.open(BytesIO(raw)).size
    assert min(tw, th) <= _VLM_SHORT and max(tw, th) <= _VLM_LONG, f"상한 초과 {tw}x{th}"
    assert min(tw, th) >= 700, f"확대 안 됨 {tw}x{th} — 원본 조각은 짧은 변 ~538"

# 이미 상한보다 큰 원본 — 확대 없이 그대로 나가야 한다 (scale > 1 조건)
for raw in _segment_tiles(_jpeg(3000, 4200)):
    tw, th = Image.open(BytesIO(raw)).size
    assert max(tw, th) <= 4200 * (0.5 + 0.15) + 2, f"큰 이미지를 더 키움 {tw}x{th}"

print("verify_ocr_tiles: OK — 2×2 조각 · 상한 준수 · 작은 원본만 확대")
