"""prompt_store 의 캐시·장애 동작과 prompt_version.py 의 SQL 생성을 점검한다 — **DB 불필요.**

    python scripts/verify_prompt_store.py

⚠️ 지키려는 것 (2026-09-11 결정):
    · 한 번 읽은 프롬프트는 DB 가 안 돼도 계속 쓴다
    · 처음부터 못 읽으면 잡이 실패해 재시도로 넘어간다 (조용히 빈 프롬프트로 VLM 을 부르지 않는다)
    · 새 버전 SQL 이 본문을 한 글자도 바꾸지 않고, 이름으로 SQL 이 깨지거나 주입되지 않는다
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_version as pv  # noqa: E402

from app.services import prompt_store as ps  # noqa: E402

_failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  {'OK  ' if ok else 'FAIL'} {label}")
    if not ok:
        _failures.append(label)


def raises(fn, exc: type[Exception]) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


class FakeDB:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, int]] = {}
        self.calls = 0
        self.down = False

    def fetch(self, name: str) -> tuple[str, int] | None:
        self.calls += 1
        if self.down:
            raise ConnectionError("down")
        return self.rows.get(name)


def fresh() -> tuple[FakeDB, list[float]]:
    db, clock = FakeDB(), [0.0]
    ps._cache.clear()
    ps._fetch = db.fetch
    ps._now = lambda: clock[0]
    return db, clock


def main() -> int:
    print("1. 캐시")
    db, clock = fresh()
    db.rows["a"] = ("A1", 1)
    check("처음엔 DB 에서 읽는다", ps.get("a") == ("A1", 1) and db.calls == 1)
    check("주기 안에서는 DB 를 다시 안 본다", ps.get("a") == ("A1", 1) and db.calls == 1)
    db.rows["a"] = ("A2", 2)
    clock[0] = ps._TTL_SEC + 1
    check("주기가 지나면 새 버전을 읽는다", ps.get("a") == ("A2", 2))

    print("2. 못 읽을 때")
    db.down = True
    clock[0] += ps._TTL_SEC + 1
    check("DB 가 안 되면 마지막 값", ps.get("a") == ("A2", 2))
    db, clock = fresh()
    db.down = True
    check(
        "처음부터 못 읽으면 PromptUnavailableError",
        raises(lambda: ps.get("a"), ps.PromptUnavailableError),
    )
    db, clock = fresh()
    db.rows["a"] = ("A1", 1)
    ps.get("a")
    del db.rows["a"]
    clock[0] = ps._TTL_SEC + 1
    check("활성 버전이 사라져도 마지막 값", ps.get("a") == ("A1", 1))
    db, clock = fresh()
    check(
        "활성 버전도 캐시도 없으면 PromptUnavailableError",
        raises(lambda: ps.get("b"), ps.PromptUnavailableError),
    )
    check(
        "워커가 재시도한다 (retryable 기본값)",
        getattr(ps.PromptUnavailableError("x"), "retryable", True),
    )

    print("3. 조합")
    db, clock = fresh()
    db.rows.update({"x": ("X", 3), "y": ("Y", 1)})
    text, tag = ps.compose("x", "y")
    check("조각을 빈 줄로 잇는다", text == "X\n\nY")
    check("버전 태그", tag == "x@3 y@1")

    print("4. SQL 생성")
    body = "본문 'quote' $x$ «»\n둘째 줄"
    sql = pv.sql_new("part.rules", body, "사유")
    m = re.search(r"\$prompt\$(.*)\$prompt\$", sql, re.S)
    check("본문이 한 글자도 안 바뀐다", bool(m) and m.group(1) == body)
    check("번호는 SQL 이 매긴다 (MAX+1)", "COALESCE(MAX(version), 0) + 1" in sql)
    check(
        "이전 활성 버전을 끈다",
        "SET is_active = false WHERE name = 'part.rules' AND is_active" in sql,
    )
    check("한 트랜잭션", sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1)
    for bad in ("part rules", "x'; DROP TABLE users;--", "Part.Rules", "", ".rules"):
        check(f"이름 거부: {bad!r}", raises(lambda b=bad: pv.sql_new(b, "x", "n"), ValueError))
    check(
        "본문에 구분자가 있으면 거부",
        raises(lambda: pv.sql_new("a", "x $prompt$ y", "n"), ValueError),
    )
    check("빈 본문 거부", raises(lambda: pv.sql_new("a", "  \n", "n"), ValueError))
    seed = pv.sql_seed("part.rules", body, "v1")
    check(
        "시드는 그 이름이 없을 때만",
        "WHERE NOT EXISTS (SELECT 1 FROM prompt_version WHERE name = 'part.rules')" in seed,
    )
    act = pv.sql_activate("part.rules", 2, "되돌림")
    check(
        "없는 버전으로 전환하면 멈춘다",
        "RAISE EXCEPTION" in act and act.index("RAISE") < act.index("SET is_active = false"),
    )

    print(f"\n{'통과' if not _failures else f'실패 {len(_failures)}건: ' + ', '.join(_failures)}")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
