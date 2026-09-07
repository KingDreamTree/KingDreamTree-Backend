"""#172 검증 — 금기(부상 주의) 병합이 DB 함수로 원자적으로 되는가.

    python scripts/verify_contraindication_merge.py                       # 함수 적용 여부만 (읽기 전용)
    python scripts/verify_contraindication_merge.py --session <uuid>      # 그 세션에 동시 병합 시험 (⚠️ 씀)

무엇을 보나
    1. DB 에 merge_contraindications(uuid, jsonb) 가 있는가 — 없으면(PGRST202) 앱은 종전
       읽고-덮어쓰기 방식으로 폴백하므로 수리가 꺼진 상태다. 마이그레이션을 적용해야 한다.
    2. --session 을 주면: 그 세션의 contraindications 를 잠시 비우고, 서로 다른 부위 8개를
       **스레드 8개가 동시에** 병합한 뒤 8개가 전부 남았는지 본다. 종전 방식이면 여기서 몇 개가
       사라진다(lost update). 끝나면 원래 값으로 되돌린다.

⚠️ --session 없이 실행하면 DB 에 아무것도 쓰지 않는다. --session 은 테스트용 세션에만 쓸 것.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from postgrest.exceptions import APIError  # noqa: E402

from app.services.db import get_client  # noqa: E402

FN = "merge_contraindications"


def function_exists() -> bool:
    try:
        get_client().rpc(FN, {"p_session_id": str(uuid.uuid4()), "p_added": []}).execute()
        return True
    except APIError as e:
        if e.code in ("PGRST202", "42883"):
            return False
        raise


def concurrent_merge(session_id: str) -> bool:
    client = get_client()
    before = (
        client.table("analysis_session")
        .select("contraindications")
        .eq("session_id", session_id)
        .execute()
        .data
    )
    if not before:
        print("세션을 찾을 수 없습니다.")
        return False
    original = before[0].get("contraindications") or []
    client.table("analysis_session").update({"contraindications": []}).eq(
        "session_id", session_id
    ).execute()

    parts = [f"시험부위{i}" for i in range(8)]

    def merge_one(part: str) -> None:
        get_client().rpc(
            FN, {"p_session_id": session_id, "p_added": [{"body_part": part, "severity": "WARN"}]}
        ).execute()

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(merge_one, parts))
        after = (
            client.table("analysis_session")
            .select("contraindications")
            .eq("session_id", session_id)
            .execute()
            .data[0]
            .get("contraindications")
            or []
        )
        got = {c.get("body_part") for c in after}
        missing = [p for p in parts if p not in got]
        print(f"동시 병합 8건 → 남은 부위 {len(got)}개, 사라진 것 {len(missing)}개 {missing or ''}")
        # 같은 것을 한 번 더 넣어도 중복되지 않아야 한다
        merge_one(parts[0])
        again = (
            client.table("analysis_session")
            .select("contraindications")
            .eq("session_id", session_id)
            .execute()
            .data[0]
            .get("contraindications")
            or []
        )
        dup_ok = len(again) == len(after)
        print(
            f"중복 병합 → 개수 그대로: {'OK' if dup_ok else 'FAIL'} ({len(after)} → {len(again)})"
        )
        return not missing and dup_ok
    finally:
        client.table("analysis_session").update({"contraindications": original}).eq(
            "session_id", session_id
        ).execute()
        print("원래 값으로 복원")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--session", help="동시 병합 시험을 할 테스트 세션 (⚠️ 그 세션에 씀)")
    args = parser.parse_args()

    if not function_exists():
        print(
            f"DB 에 {FN}() 가 없다 — db/migrations/2026-09-07_merge_contraindications_fn.sql 을 적용할 것."
        )
        print("앱은 종전 방식으로 폴백 중이라 동시 저장 유실 방어가 꺼져 있다.")
        return 1
    print(f"{FN}() 있음 — 앱이 원자적 병합을 쓴다.")

    if args.session:
        return 0 if concurrent_merge(args.session) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
