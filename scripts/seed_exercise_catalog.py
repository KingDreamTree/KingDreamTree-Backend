"""data/exercise_catalog.json → exercise_catalog 테이블 seed.

    python scripts/seed_exercise_catalog.py            # upsert
    python scripts/seed_exercise_catalog.py --dry-run  # 넣지 않고 요약만

선행: scripts/fetch_exercisedb.py fetch  (수집)
      scripts/localize_exercises.py      (한글화)

⚠️ `is_beginner_safe` 를 **명시적으로** 넣는다. 테이블 DEFAULT 는 false 라
   여기서 안 넣으면 전부 제외되고 후보가 0건이 된다 — 조용히 위험한 운동이
   나가는 것보다 티 나게 비어 있는 쪽이 낫다는 판단이다 (담당 A 지적 반영).

⚠️ **수집본 JSON 의 is_beginner_safe 를 믿지 않고 여기서 다시 평가한다**
   (`exercise_catalog.is_beginner_safe`). 제외 목록을 고칠 때마다 API 를
   재수집해야 한다면 한글화가 날아가고, 그러면 아무도 목록을 안 고친다.
   원본은 raw 로 두고 판정은 코드 한 곳에서만 한다.

⚠️ 스크리닝은 이름 기반 휴리스틱이다 — D8 로 승인 대기 중인 항목이라
   근거를 주장하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.db import get_client  # noqa: E402
from app.services.exercise_catalog import is_beginner_safe  # noqa: E402

CATALOG = Path(__file__).resolve().parent.parent / "data" / "exercise_catalog.json"

#: 한 번에 upsert 할 행 수. 전체를 한 요청에 넣으면 payload 가 커진다.
CHUNK = 50


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not CATALOG.exists():
        print(f"[X] {CATALOG} 가 없습니다.")
        print("    python scripts/fetch_exercisedb.py fetch 를 먼저 실행하세요.")
        return 1

    doc = json.loads(CATALOG.read_text(encoding="utf-8"))
    items = doc["exercises"]

    rows = [
        {
            "exercise_ref": e["exercise_ref"],
            "name_en": e["name_en"].strip(),
            "name_ko": e.get("name_ko"),
            "body_parts": e["body_parts"],
            "target_muscles": e["target_muscles"],
            "secondary_muscles": e.get("secondary_muscles") or [],
            "equipments": e["equipments"],
            "exercise_type": e["exercise_type"],
            "keywords": e.get("keywords") or [],
            "image_url": e.get("image_url"),
            # ⚠️ 수집본 값이 아니라 **지금 규칙으로 다시 평가**한다 (모듈 주석)
            "is_beginner_safe": is_beginner_safe(e["name_en"], e["exercise_type"]),
        }
        for e in items
    ]

    types = Counter(r["exercise_type"] for r in rows)
    unsafe = sum(1 for r in rows if not r["is_beginner_safe"])
    unsafe_names = [r["name_en"] for r in rows if not r["is_beginner_safe"]]
    no_ko = sum(1 for r in rows if not r["name_ko"])

    print(f"수집본: {len(rows)}개  (fetched_at={doc.get('fetched_at')})")
    print(f"  타입: {dict(types)}")
    print(f"  초보 제외: {unsafe}개")
    for n in sorted(unsafe_names):
        print(f"     - {n.strip()}")
    print(
        f"  한글명 없음: {no_ko}개" + ("  ← localize_exercises.py 먼저 돌리세요" if no_ko else "")
    )

    if args.dry_run:
        print("\n--dry-run: DB 에 넣지 않았습니다.")
        return 0

    client = get_client()
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        client.table("exercise_catalog").upsert(chunk, on_conflict="exercise_ref").execute()
        print(f"  upsert {i + len(chunk)}/{len(rows)}")

    total = client.table("exercise_catalog").select("exercise_ref", count="exact").execute()
    print(f"\n완료 — 테이블 총 {total.count}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
