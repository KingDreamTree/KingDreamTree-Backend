"""워커가 잡 하나 때문에 통째로 죽지 않는가 (DB·GPU·키 불필요).

    python scripts/verify_worker_resilience.py

━━ 왜 이 검사가 있는가 ━━

실측으로 겪은 사고다. 스모크가 만든 세션이 CASCADE 로 지워지면서 job 행까지
사라졌는데, 그 잡을 이미 집어간 워커가 마무리하려다 이렇게 됐다:

    queue.complete()  → UPDATE 가 0행 → .data[0] → IndexError
    → run.py 의 except 로 넘어가 queue.fail() 호출
    → queue.fail() 도 같은 이유로 IndexError
    → **except 를 뚫고 나가 워커 루프 종료**

잡 하나 때문에 워커 프로세스가 내려갔고, 그 뒤 들어온 모든 잡이 PENDING 에
쌓였다. 화면에서는 "느리다" 로만 보여서 원인을 찾기 어렵다.

⚠️ 이 검사는 **DB 를 쓰지 않는다.** get_client 를 가짜로 바꿔서 "UPDATE 가
   0행을 돌려주는" 상황만 재현한다. 실제 FK 위반을 만들려면 세션을 지워야
   하는데, 공유 DB 에서 그런 실험은 하지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


# ── 0행을 돌려주는 가짜 Supabase 클라이언트 ─────────────────────────────────


class _Result:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class _Query:
    """어떤 체이닝을 해도 지정된 결과를 돌려준다."""

    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    def __getattr__(self, _name: str):
        return lambda *a, **k: self

    def execute(self) -> _Result:
        return _Result(self._data)


class _Client:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    def table(self, _name: str) -> _Query:
        return _Query(self._data)


def main() -> int:
    print("워커 복원력 검증\n")

    from app.worker import queue

    original = queue.get_client
    job_id = uuid4()

    try:
        # ── 1. 잡 행이 사라진 상태 ──────────────────────────────────────────
        print("[잡 행이 사라졌을 때 — 세션 CASCADE 삭제]")
        queue.get_client = lambda: _Client([])  # 모든 쿼리가 0행

        try:
            r = queue.complete(job_id, {"ok": True})
            check("complete() 가 예외를 던지지 않는다", True, f"반환 {r}")
        except Exception as e:  # noqa: BLE001
            check("complete() 가 예외를 던지지 않는다", False, f"{type(e).__name__}: {e}")

        try:
            r = queue.fail(job_id, "테스트 실패 메시지", retryable=False)
            check("fail() 이 예외를 던지지 않는다", True, f"반환 {r}")
        except Exception as e:  # noqa: BLE001
            check("fail() 이 예외를 던지지 않는다", False, f"{type(e).__name__}: {e}")

        # ── 2. 정상 경로는 그대로 ───────────────────────────────────────────
        print("\n[잡 행이 있을 때 — 정상 경로]")
        row = {"job_id": str(job_id), "attempts": 1, "status": "DONE"}
        queue.get_client = lambda: _Client([row])

        check("complete() 가 행을 돌려준다", queue.complete(job_id, {}) == row)
        check("fail() 이 행을 돌려준다", queue.fail(job_id, "x") == row)

    finally:
        queue.get_client = original

    # ── 3. run.py 가 fail() 예외를 삼키는가 ─────────────────────────────────
    #     ⚠️ 소스를 읽어 확인한다. 실제 루프를 돌리려면 DB 가 필요한데,
    #        여기서 보려는 것은 "예외가 루프 밖으로 새는가" 하나뿐이다.
    print("\n[run.py — 실패 기록이 또 터져도 루프가 안 죽는가]")
    src = (PROJECT_ROOT / "app" / "worker" / "run.py").read_text(encoding="utf-8")
    idx = src.find("queue.fail(")
    guarded = idx != -1 and "try:" in src[max(0, idx - 400) : idx]
    check("queue.fail() 호출이 try 로 감싸여 있다", guarded)

    # ── 4. 다른 곳에 남은 무방비 .data[0] ───────────────────────────────────
    print("\n[queue.py — 무방비 .data[0] 이 남았는가]")
    # ⚠️ 문자열 검색으로 하면 **이 사고를 설명하는 주석·독스트링이 걸린다.**
    #    (실제로 걸렸다 — 고쳐놨는데 빨간불이 떴다) AST 로 실제 코드만 본다.
    import ast

    q_path = PROJECT_ROOT / "app" / "worker" / "queue.py"
    tree = ast.parse(q_path.read_text(encoding="utf-8"))
    src_lines = q_path.read_text(encoding="utf-8").splitlines()

    bare: list[str] = []
    for node in ast.walk(tree):
        # `<무언가>.data[0]` 모양 — Subscript(value=Attribute(attr="data"))
        if not isinstance(node, ast.Subscript):
            continue
        val = node.value
        if not (isinstance(val, ast.Attribute) and val.attr == "data"):
            continue
        line = src_lines[node.lineno - 1].strip()
        # insert 는 항상 1행을 돌려주므로 대상이 아니다. update/select 가 문제다.
        chain = ast.unparse(val)
        if "insert" in chain:
            continue
        bare.append(f"{node.lineno}행: {line}")
    check("update/select 뒤 무방비 .data[0] 없음", not bare, "; ".join(bare))

    print()
    if _failures:
        print(f"실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
