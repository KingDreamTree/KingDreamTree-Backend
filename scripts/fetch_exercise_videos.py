"""카탈로그에 운동 시연 **영상 URL**을 채운다 (data/exercise_catalog.json 갱신).

    RAPIDAPI_KEY=... python scripts/fetch_exercise_videos.py

왜 별도 스크립트인가 — fetch_exercisedb.py 는 **목록** 엔드포인트로 수집하는데
목록 응답에는 videoUrl 이 없다. 실측(2026-08-17):

    GET /api/v1/exercises        → exerciseId, name, imageUrl, bodyParts, …  (영상 없음)
    GET /api/v1/exercises/{id}   → 위 + **videoUrl**, imageUrls, instructions, …

그래서 운동당 한 번씩 상세를 불러야 한다. 200개면 200회 —
무료 플랜 한도(월 2,000회) 안이지만 매번 돌릴 일은 아니라 수집과 분리했다.

⚠️ 이미 채워진 항목은 건너뛴다. 중단됐다 다시 돌려도 남은 것만 받는다.
⚠️ 영상이 없는 운동이 있어도 정상이다 — 그 운동은 image_url 로 표시된다.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402

CATALOG = PROJECT_ROOT / "data" / "exercise_catalog.json"

#: 호출 간격(초). 무료 플랜에서 429 를 만나지 않을 정도로만 둔다.
DELAY = 0.15


def _get(path: str) -> Any:
    """GET 1회. 429 면 한 번 쉬었다 재시도한다."""
    key = settings.rapidapi_key.strip()
    if not key:
        raise SystemExit("RAPIDAPI_KEY 가 없습니다. .env 에 넣어주세요.")
    host = settings.rapidapi_exercisedb_host
    req = urllib.request.Request(
        f"https://{host}{path}",
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": host},
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                time.sleep(5)
                continue
            raise
    return None


def main() -> int:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    exercises: list[dict[str, Any]] = data["exercises"]

    todo = [e for e in exercises if not e.get("video_url")]
    print(f"카탈로그 {len(exercises)}개 · 영상 미보유 {len(todo)}개")
    if not todo:
        print("전부 채워져 있습니다.")
        return 0

    filled = missing = failed = 0
    for i, ex in enumerate(todo, 1):
        ref = ex["exercise_ref"]
        try:
            body = _get(f"/api/v1/exercises/{ref}")
            detail = body.get("data", body)
            url = detail.get("videoUrl")
            if url:
                ex["video_url"] = url
                filled += 1
            else:
                # ⚠️ None 이 아니라 빈 값으로 남긴다 — "받아봤는데 없다"와
                #    "아직 안 받아봤다"를 다음 실행에서 구분하지 못하면
                #    매번 200회를 다시 쏘게 된다. (get 은 둘 다 falsy 라
                #    재시도되지만, 로그로는 구분된다)
                missing += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [실패] {ref}: {type(e).__name__}")
        if i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} — 영상 {filled} · 없음 {missing} · 실패 {failed}")
        time.sleep(DELAY)

    CATALOG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n저장 완료 — 영상 {filled}개 추가 (없음 {missing} · 실패 {failed})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
