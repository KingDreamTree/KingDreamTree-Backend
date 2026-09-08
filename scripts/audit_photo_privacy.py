"""사진 프라이버시 감사 — "서버 어디에도 사진이 없다"를 **데이터와 코드에서** 확인한다. 읽기 전용.

    python scripts/audit_photo_privacy.py                 # 코드 감사 + 최근 pod 경로 세션 20개
    python scripts/audit_photo_privacy.py --session <id>  # 특정 세션
    python scripts/audit_photo_privacy.py --code-only     # DB 없이 코드만

1. 코드 감사 (정적)
   · 사진 원본을 Storage 에서 읽는 곳이 app/services/photo_source.py **한 곳뿐**인가
     (핸들러가 storage.download 로 사진을 직접 읽으면 팟 경로에서 저장을 강제하게 된다)
   · 이미지 바이트를 로그에 찍는 패턴이 있는가 (log.*(... jpeg|raw|image_bytes ...))
   · 사진을 파일로 쓰는 패턴이 app/ 안에 있는가 (open(..., "wb") / .save(경로))
2. 데이터 감사 (세션별)
   · photo.storage_path 가 NULL 이고 crop_box 가 있는가
   · Storage photos 버킷의 {user_id}/{session_id} 아래에 파일이 없는가
   · part_diagnosis / overall_diagnosis 의 raw_response, job.result/error/payload 에
     "data:image" · "base64," · 4KB 넘는 문자열이 없는가 (이미지가 섞여 저장되면 "저장 안 함"이 거짓이 된다)

⚠️ 콘솔이 cp949 면 PYTHONIOENCODING=utf-8 을 붙일 것 (다른 verify 스크립트와 같은 이유).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FAILS: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    print(("[O] " if cond else "[X] ") + label + (f"  — {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
# 1. 코드
# --------------------------------------------------------------------------- #

#: 사진(원본)을 Storage 에서 읽어도 되는 유일한 곳.
ALLOWED_PHOTO_DOWNLOAD = {"app/services/photo_source.py"}


def audit_code() -> None:
    print("== 코드 감사 ==")
    app_dir = PROJECT_ROOT / "app"
    photo_downloads: list[str] = []
    log_image_bytes: list[str] = []
    file_writes: list[str] = []
    for path in app_dir.rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            # 사진 행(photo["storage_path"]) 을 storage.download 에 넘기는 곳
            if "storage.download(" in s and "photo[" in s and rel not in ALLOWED_PHOTO_DOWNLOAD:
                photo_downloads.append(f"{rel}:{i}")
            if re.search(r"log\.\w+\(.*\b(jpeg|raw|image_bytes|photo_bytes)\b", s) and "len(" not in s and "bytes" not in s.split("%")[0][-20:]:
                # 크기(len)만 찍는 건 허용. 바이트 자체를 포맷에 넣는 것만 잡는다.
                if re.search(r"%s.*\b(jpeg|raw|image_bytes|photo_bytes)\b|\{(jpeg|raw|image_bytes|photo_bytes)\}", s):
                    log_image_bytes.append(f"{rel}:{i}")
            if re.search(r"open\([^)]*['\"]wb['\"]", s) or re.search(r"\.save\(\s*['\"][^'\"]+\.(jpe?g|png|webp)['\"]", s):
                file_writes.append(f"{rel}:{i}")
    check("사진 원본을 Storage 에서 읽는 곳이 photo_source 뿐", not photo_downloads, ", ".join(photo_downloads))
    check("이미지 바이트를 로그에 찍는 곳 없음", not log_image_bytes, ", ".join(log_image_bytes))
    check("app/ 안에서 이미지를 파일로 쓰는 곳 없음", not file_writes, ", ".join(file_writes))


# --------------------------------------------------------------------------- #
# 2. 데이터
# --------------------------------------------------------------------------- #

_LONG = 4096


def _blob_problems(value: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out += _blob_problems(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _blob_problems(v, f"{path}[{i}]")
    elif isinstance(value, str):
        if "data:image" in value or "base64," in value:
            out.append(f"{path}: 이미지 데이터 URL")
        elif len(value) > _LONG and re.fullmatch(r"[A-Za-z0-9+/=\s]+", value[:512]):
            out.append(f"{path}: base64 로 보이는 긴 문자열 ({len(value)} chars)")
    return out


def audit_session(session_id: str) -> None:
    from app.services import db, storage

    print(f"\n== 세션 {session_id} ==")
    session = db.get_session(session_id)  # type: ignore[arg-type]
    if session is None:
        check("세션 존재", False, "없음")
        return
    user_id = str(session["user_id"])

    for kind in ("REFERENCE", "USER"):
        row = db.get_photo(session_id, kind)  # type: ignore[arg-type]
        if row is None:
            print(f"    {kind}: 사진 행 없음")
            continue
        pod_row = row.get("storage_path") is None
        check(f"{kind}: storage_path NULL (팟 경로)", pod_row, str(row.get("storage_path")))
        if pod_row:
            check(f"{kind}: crop_box 있음", bool(row.get("crop_box")))

    files = storage.list_prefix("photos", f"{user_id}/{session_id}")
    check("Storage photos 버킷에 이 세션 파일 없음", not files, ", ".join(files[:5]))

    problems: list[str] = []
    for table, cols in (
        ("part_diagnosis", "class_name,raw_response"),
        ("overall_diagnosis", "raw_response"),
        ("job", "kind,payload,result,error"),
    ):
        for r in db.rows_for_session(table, session_id, cols):  # type: ignore[arg-type]
            problems += [f"{table}{p}" for p in _blob_problems(r)]
    check("저장된 결과·잡에 이미지 데이터 없음", not problems, "; ".join(problems[:5]))


def recent_pod_sessions(limit: int) -> list[str]:
    from app.services.db import get_client

    rows = (
        get_client()
        .table("photo")
        .select("session_id,created_at")
        .is_("storage_path", "null")
        .order("created_at", desc=True)
        .limit(limit * 2)
        .execute()
        .data
    ) or []
    seen: list[str] = []
    for r in rows:
        if r["session_id"] not in seen:
            seen.append(r["session_id"])
    return seen[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=None)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--code-only", action="store_true")
    args = ap.parse_args()

    audit_code()
    if not args.code_only:
        sessions = [args.session] if args.session else recent_pod_sessions(args.limit)
        if not sessions:
            print("\n(팟 경로로 만든 세션이 아직 없음 — storage_path NULL 인 photo 행 없음)")
        for sid in sessions:
            audit_session(sid)

    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
