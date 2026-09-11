#!/usr/bin/env python
"""프롬프트 버전 관리 — DB(prompt_version)의 버전을 보고, 새로 올리고, 되돌린다 (#164).

    python scripts/prompt_version.py list                                  # 이름별 버전·활성
    python scripts/prompt_version.py show part.rules > /tmp/rules.txt      # 활성 본문
    python scripts/prompt_version.py show part.rules --version 1           # 특정 버전
    python scripts/prompt_version.py new part.rules /tmp/rules.txt --note "왜 바꿨나"
    python scripts/prompt_version.py activate part.rules 1 --note "v2 가 나빠서 되돌림"

━━ new · activate 는 DB 를 바꾸지 않는다 — 마이그레이션 파일을 쓴다 ━━

db/migrations/ 에 SQL 파일을 만든다. 적용은 다른 마이그레이션과 같은 경로(Supabase SQL 에디터)다.

    · 문구가 git 에 남는다 — 누가 언제 왜 바꿨는지 리뷰하고, 파일 하나로 되돌린다
    · 앱 배포는 타지 않는다 — 적용하면 prompt_store 캐시(60초)가 지난 뒤 다음 진단부터 쓰인다
    · 버전 번호는 SQL 안에서 매긴다 (MAX+1) — 파일을 만들 때 DB 를 볼 필요가 없다

⚠️ 적용 후 확인: verify_quick.py · verify_analysis.py 의 프롬프트 검사는 DB 의 **활성 버전**을
   읽어 잰다. 새 버전이 필수 규칙을 빠뜨렸으면 거기서 빨개진다.

이름 (지금 쓰는 것 — 쓰는 곳은 app/services/vlm.py 의 *_PROMPT)
    part.intro.live   라이브(웹캠) 부위 진단 입력 설명
    part.intro.photo  사진(갤러리) 부위 진단 입력 설명
    part.rules        부위 카드 공통 규칙 (두 경로 공용)
    part.output       부위 카드 공통 출력 형식 (두 경로 공용)
    overall.system    종합 진단 시스템 프롬프트
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MIGRATIONS = ROOT / "db" / "migrations"

#: 이름은 SQL 문자열에 그대로 들어간다 — 따옴표·공백이 끼면 SQL 이 깨지거나 주입되므로 좁게 받는다.
_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"프롬프트 이름 형식이 아닙니다: {name!r} (예: part.rules)")
    return name


def _dollar(text: str, tag: str) -> str:
    """$tag$…$tag$ 로 감싼다 — 따옴표를 이스케이프할 필요가 없다. 본문에 구분자가 있으면 거부."""
    quote = f"${tag}$"
    if quote in text:
        raise ValueError(f"본문에 {quote} 가 있어 SQL 로 감쌀 수 없습니다")
    return f"{quote}{text}{quote}"


def _headline(note: str) -> str:
    return (note.strip().splitlines() or [""])[0]


def sql_new(name: str, content: str, note: str) -> str:
    """새 버전을 올리고 활성화하는 SQL. 이전 활성 버전은 끈다."""
    _check_name(name)
    if not content.strip():
        raise ValueError("본문이 비어 있습니다")
    return f"""-- 프롬프트 {name} 새 버전 — {_headline(note)}
BEGIN;
UPDATE prompt_version SET is_active = false WHERE name = '{name}' AND is_active;
INSERT INTO prompt_version (name, version, content, note, is_active)
VALUES (
    '{name}',
    (SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_version WHERE name = '{name}'),
    {_dollar(content, "prompt")},
    {_dollar(note.strip(), "note")},
    true
);
COMMIT;
"""


def sql_seed(name: str, content: str, note: str) -> str:
    """v1 시드. **그 이름이 아직 없을 때만** 넣는다 — 마이그레이션을 두 번 돌려도 버전이 안 는다."""
    _check_name(name)
    return f"""-- 프롬프트 {name} v1
INSERT INTO prompt_version (name, version, content, note, is_active)
SELECT '{name}', 1, {_dollar(content, "prompt")}, {_dollar(note.strip(), "note")}, true
WHERE NOT EXISTS (SELECT 1 FROM prompt_version WHERE name = '{name}');
"""


def sql_activate(name: str, version: int, note: str) -> str:
    """이미 있는 버전으로 전환. 그 버전이 없으면 트랜잭션째 멈춘다 — 활성 버전이 사라지면 안 된다."""
    _check_name(name)
    if version < 1:
        raise ValueError("버전은 1 이상입니다")
    return f"""-- 프롬프트 {name} 를 v{version} 로 전환 — {_headline(note)}
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM prompt_version WHERE name = '{name}' AND version = {version}) THEN
        RAISE EXCEPTION '프롬프트 {name} v{version} 가 없습니다';
    END IF;
END $$;
UPDATE prompt_version SET is_active = false WHERE name = '{name}' AND is_active;
UPDATE prompt_version SET is_active = true WHERE name = '{name}' AND version = {version};
COMMIT;
"""


def _write(slug: str, sql: str) -> Path:
    base = f"{date.today().isoformat()}_prompt_{slug}"
    path, n = MIGRATIONS / f"{base}.sql", 2
    while path.exists():
        path, n = MIGRATIONS / f"{base}_{n}.sql", n + 1
    path.write_text(sql, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="프롬프트 버전 관리 (#164)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="이름별 버전·활성 여부")
    p = sub.add_parser("show", help="본문 출력 (기본: 활성 버전)")
    p.add_argument("name")
    p.add_argument("--version", type=int)
    p = sub.add_parser("new", help="새 버전 마이그레이션 파일 생성")
    p.add_argument("name")
    p.add_argument("file", type=Path)
    p.add_argument("--note", required=True, help="왜 바꿨나 — 버전 행과 파일에 남는다")
    p = sub.add_parser("activate", help="이미 있는 버전으로 전환하는 마이그레이션 파일 생성")
    p.add_argument("name")
    p.add_argument("version", type=int)
    p.add_argument("--note", required=True)
    args = ap.parse_args()

    if args.cmd == "new":
        path = _write(
            args.name.replace(".", "_"), sql_new(args.name, args.file.read_text(), args.note)
        )
        print(
            f"{path.relative_to(ROOT)} 를 만들었습니다. SQL 에디터에서 적용하면 60초 안에 쓰입니다."
        )
        return 0
    if args.cmd == "activate":
        slug = f"{args.name.replace('.', '_')}_activate_v{args.version}"
        path = _write(slug, sql_activate(args.name, args.version, args.note))
        print(f"{path.relative_to(ROOT)} 를 만들었습니다. SQL 에디터에서 적용하세요.")
        return 0

    # list · show 는 DB 를 읽는다 — 지연 import (new/activate 는 DB 설정 없이 돈다)
    from app.services.db import get_client

    client = get_client()
    if args.cmd == "list":
        rows = (
            client.table("prompt_version")
            .select("name,version,is_active,created_at,note")
            .order("name")
            .order("version")
            .execute()
            .data
        )
        for r in rows:
            mark = "●" if r["is_active"] else " "
            print(
                f"{mark} {r['name']:<18} v{r['version']:<3} {str(r['created_at'])[:16]}  {_headline(r.get('note') or '')}"
            )
        return 0

    q = client.table("prompt_version").select("content,version").eq("name", _check_name(args.name))
    q = q.eq("version", args.version) if args.version else q.eq("is_active", True)
    rows = q.limit(1).execute().data
    if not rows:
        print(f"없습니다: {args.name}", file=sys.stderr)
        return 1
    sys.stdout.write(rows[0]["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
