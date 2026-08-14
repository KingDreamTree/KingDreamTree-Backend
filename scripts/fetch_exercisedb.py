"""ExerciseDB(ascendapi) 운동 풀 배치 수집 → 로컬 캐시.

    RAPIDAPI_KEY=... python scripts/fetch_exercisedb.py enums      # bodyparts/muscles 등 enum 확정
    RAPIDAPI_KEY=... python scripts/fetch_exercisedb.py fetch      # 전체 운동 수집 → data/exercise_catalog.json
    RAPIDAPI_KEY=... python scripts/fetch_exercisedb.py sample     # 5건만 받아 필드 구조 확인

왜 배치 1회 수집인가 (docs/routine-logic-decision.md §5 D6)
    * 사용자 요청마다 외부 API를 부르면 비용·rate limit·응답 속도가 전부 외부 종속이 된다.
    * 시연 중 RapidAPI 장애가 우리 장애가 되는 것을 원천 차단한다.
    * 운동 목록은 사실상 정적 데이터다 — 수집 시각(fetched_at)만 남기면 된다.

⚠️ 키는 .env 가 아니라 환경변수로만 받는다. .env 는 공유 파일(A 리뷰 대상)이라
   확정 전 변수를 늘리지 않는다. 확정되면 .env.example 에 이름만 추가한다.

⚠️ ascendapi 변종은 공개 문서가 빈약해 엔드포인트 경로가 확실치 않다.
   후보 경로를 순서대로 찔러보고 되는 것을 쓴다 — 실패해도 어떤 경로를
   시도했는지 전부 출력하므로, RapidAPI playground 의 실제 경로와 대조해
   상단의 *_PATH_CANDIDATES 상수에 추가하면 된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOST = "edb-with-videos-and-images-by-ascendapi.p.rapidapi.com"
OUT_DIR = Path(__file__).resolve().parent.parent / "data"

#: 표준 ExerciseDB 계열에서 흔한 경로들. 되는 것을 처음 발견하는 순간 고정한다.
EXERCISE_PATH_CANDIDATES = ("/v1/exercises", "/exercises", "/api/v1/exercises")
ENUM_PATH_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bodyparts": ("/v1/bodyparts", "/bodyparts", "/exercises/bodyPartList"),
    "muscles": ("/v1/muscles", "/muscles", "/exercises/targetList"),
    "equipments": ("/v1/equipments", "/equipments", "/exercises/equipmentList"),
}

#: D8 초보 제외 후보 — 이름/키워드에 이게 들어가면 is_beginner_safe=false.
#: ⚠️ 임의 목록이다. PM 승인(D8) 후 확정하고, 승인 전에는 "표시만" 한다.
ADVANCED_KEYWORDS = (
    "hanging",
    "muscle up",
    "muscle-up",
    "pistol",
    "snatch",
    "clean and jerk",
    "clean & jerk",
    "power clean",
    "handstand",
    "planche",
    "front lever",
    "dragon flag",
    "one arm pull",
)


def _key() -> str:
    key = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not key:
        print("[X] RAPIDAPI_KEY 환경변수가 없습니다.")
        print("    RapidAPI 콘솔(rapidapi.com) → 해당 API → X-RapidAPI-Key 값을 넣어 실행:")
        print("    RAPIDAPI_KEY=xxxx python scripts/fetch_exercisedb.py enums")
        sys.exit(1)
    return key


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET 1회. 429(rate limit)면 잠깐 쉬고 한 번 더."""
    url = f"https://{HOST}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"X-RapidAPI-Key": _key(), "X-RapidAPI-Host": HOST},
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                print("    rate limit — 5초 대기 후 재시도")
                time.sleep(5)
                continue
            raise
    return None


def _try_paths(paths: tuple[str, ...], params: dict[str, Any] | None = None) -> tuple[str, Any]:
    """후보 경로를 순서대로 시도해 (성공 경로, 응답)을 반환한다."""
    errors = []
    for path in paths:
        try:
            data = _get(path, params)
            print(f"  [OK] {path}")
            return path, data
        except urllib.error.HTTPError as e:
            errors.append(f"{path} -> HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{path} -> {type(e).__name__}")
    print("  [X] 모든 후보 경로 실패:")
    for err in errors:
        print(f"      {err}")
    print("    RapidAPI playground 의 실제 경로를 확인해 *_PATH_CANDIDATES 에 추가하세요.")
    sys.exit(1)


def _unwrap(data: Any) -> list[dict]:
    """{"data": [...]} / {"exercises": [...]} / [...] 어느 형태든 목록으로."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "exercises", "results", "items"):
            inner = data.get(key)
            if isinstance(inner, list):
                return inner
            # {"data": {"exercises": [...], "nextCursor": ...}} 형태
            if isinstance(inner, dict):
                for key2 in ("exercises", "results", "items"):
                    if isinstance(inner.get(key2), list):
                        return inner[key2]
    return []


def cmd_enums() -> None:
    """bodyparts / muscles / equipments 실제 enum 을 받아 저장한다.

    docs/routine-logic-decision.md §3 매핑 표의 '추정'을 '확정'으로 바꾸는 근거.
    """
    OUT_DIR.mkdir(exist_ok=True)
    out: dict[str, Any] = {"host": HOST, "fetched_at": datetime.now(timezone.utc).isoformat()}
    for name, candidates in ENUM_PATH_CANDIDATES.items():
        print(f"{name}:")
        path, data = _try_paths(candidates)
        values = _unwrap(data) or data
        out[name] = {"path": path, "values": values}
        print(f"    {len(values) if isinstance(values, list) else '?'}개")

    dest = OUT_DIR / "exercisedb_enums.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {dest}")
    print("→ docs/routine-logic-decision.md §3 매핑 표와 대조해 어긋난 값을 보고할 것")


def cmd_sample() -> None:
    """5건만 받아 필드 구조를 눈으로 확인한다 (스키마 검증용)."""
    print("exercises:")
    _, data = _try_paths(EXERCISE_PATH_CANDIDATES, {"limit": 5, "offset": 0})
    items = _unwrap(data)
    print(json.dumps(items[:5], ensure_ascii=False, indent=2)[:4000])
    if items:
        print(f"\n필드: {sorted(items[0].keys())}")


def _mark_beginner_safe(item: dict) -> bool:
    text = " ".join(
        [str(item.get("name", ""))] + [str(k) for k in item.get("keywords") or []]
    ).lower()
    return not any(kw in text for kw in ADVANCED_KEYWORDS)


def cmd_fetch(page_size: int, max_pages: int) -> None:
    """전체 운동을 페이지네이션으로 수집해 data/exercise_catalog.json 에 쓴다.

    exercise_catalog 테이블(docs/routine-schema-draft.md)에 넣기 직전 형태로
    정규화한다. 한글화(name_ko)는 별도 배치(LLM 1회)에서 채운다.
    """
    OUT_DIR.mkdir(exist_ok=True)
    print("exercises (첫 페이지로 경로 확정):")
    path, first = _try_paths(EXERCISE_PATH_CANDIDATES, {"limit": page_size, "offset": 0})

    all_items: list[dict] = list(_unwrap(first))
    for page in range(1, max_pages):
        batch = _unwrap(_get(path, {"limit": page_size, "offset": page * page_size}))
        if not batch:
            break
        all_items.extend(batch)
        print(f"  {len(all_items)}개 수집…")
        time.sleep(0.4)  # rate limit 예방

    # exerciseId 기준 중복 제거 + 카탈로그 스키마로 정규화
    catalog: dict[str, dict] = {}
    for item in all_items:
        ref = item.get("exerciseId") or item.get("id")
        if not ref or ref in catalog:
            continue
        catalog[ref] = {
            "exercise_ref": ref,
            "name_en": item.get("name", "").strip(),
            "name_ko": None,  # 한글화 배치에서 채움
            "body_parts": item.get("bodyParts") or [],
            "equipments": item.get("equipments") or [],
            "exercise_type": item.get("exerciseType") or "STRENGTH",
            "target_muscles": item.get("targetMuscles") or [],
            "secondary_muscles": item.get("secondaryMuscles") or [],
            "keywords": item.get("keywords") or [],
            "image_url": item.get("imageUrl"),
            "is_beginner_safe": _mark_beginner_safe(item),
        }

    dest = OUT_DIR / "exercise_catalog.json"
    dest.write_text(
        json.dumps(
            {
                "host": HOST,
                "path": path,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "count": len(catalog),
                "exercises": list(catalog.values()),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    flagged = sum(1 for e in catalog.values() if not e["is_beginner_safe"])
    strength = sum(1 for e in catalog.values() if e["exercise_type"] == "STRENGTH")
    print(f"\n저장: {dest}")
    print(
        f"  전체 {len(catalog)}개 · STRENGTH {strength}개 · 초보 제외 후보 {flagged}개 (D8 승인 전 표시만)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="ExerciseDB 배치 수집")
    ap.add_argument("command", choices=["enums", "sample", "fetch"])
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--max-pages", type=int, default=50)
    args = ap.parse_args()

    if args.command == "enums":
        cmd_enums()
    elif args.command == "sample":
        cmd_sample()
    else:
        cmd_fetch(args.page_size, args.max_pages)


if __name__ == "__main__":
    main()
