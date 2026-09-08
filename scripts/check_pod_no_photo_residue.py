"""팟이 사진을 디스크에 남기지 않았는지 — 처리 후 확인 스크립트.

    python scripts/check_pod_no_photo_residue.py --since "2026-09-09T10:00:00" [--roots . /tmp /workspace] [--pod http://localhost:8080]

무엇을 보나
  1. --since 이후에 **새로 생기거나 바뀐 이미지 파일**이 지정한 폴더 아래에 있는가
     (확장자 + 파일 머리(JPEG/PNG/WEBP/HEIC 매직) 둘 다 본다 — 확장자를 바꿔 숨긴 것도 잡는다)
  2. --pod 를 주면 GET /health 의 held_photos 가 0 인가 (메모리에도 없어야 정상 대기 상태다)

⚠️ 문 없는 프로덕션 팟에서는 **실행할 수 없다** (들어갈 수 없으므로). 같은 이미지로 띄운
   스테이징 팟이나 로컬에서 처리 한 번 돌린 뒤 실행한다. 프로덕션은 코드가 같다는 것으로
   보증한다 — 그래서 이 스크립트는 릴리스 전 필수 항목이다.
⚠️ models/ (가중치)·.venv·node_modules·.git 은 건너뛴다. 가중치 폴더에 이미지는 없다.

종료 코드: 0 = 잔여 없음, 1 = 잔여 있음(목록 출력), 2 = 인자 오류
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".gif", ".tif", ".tiff"}
SKIP_DIRS = {"models", ".venv", "venv", "node_modules", ".git", "__pycache__", "site-packages", ".cache"}
MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),  # 뒤에 WEBP 가 온다
    (b"GIF8", "gif"),
    (b"BM", "bmp"),
)


def looks_like_image(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for magic, name in MAGIC:
        if head.startswith(magic):
            if name == "webp" and head[8:12] != b"WEBP":
                continue
            return name
    if head[4:8] == b"ftyp" and (b"heic" in head or b"heix" in head or b"mif1" in head):
        return "heic"
    return None


def parse_since(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        pass
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # 로컬 시간으로 해석
    return dt.timestamp()


def scan(roots: list[Path], since_ts: float, min_bytes: int) -> list[tuple[Path, str, int]]:
    found: list[tuple[Path, str, int]] = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < since_ts or st.st_size < min_bytes:
                    continue
                kind = looks_like_image(p) or (p.suffix.lower()[1:] if p.suffix.lower() in IMAGE_EXT else None)
                if kind:
                    found.append((p, kind, st.st_size))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="이 시각 이후 변경분만 (ISO 또는 epoch 초). 보통 팟 기동 시각")
    ap.add_argument("--roots", nargs="*", default=None, help="검사할 폴더 (기본: cwd, 임시폴더, /workspace, 홈)")
    ap.add_argument("--min-bytes", type=int, default=8 * 1024, help="이보다 작은 파일은 무시 (아이콘·픽스처)")
    ap.add_argument("--pod", default=None, help="팟 주소 — /health 의 held_photos 도 확인")
    args = ap.parse_args()

    try:
        since_ts = parse_since(args.since)
    except ValueError:
        print("--since 형식 오류 (ISO 8601 또는 epoch 초)")
        return 2

    roots = [Path(r) for r in args.roots] if args.roots else [
        Path.cwd(),
        Path(tempfile.gettempdir()),
        Path("/workspace"),
        Path.home(),
    ]
    print(f"기준 시각: {datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()} (UTC)")
    print("검사 폴더:", ", ".join(str(r) for r in roots))

    found = scan(roots, since_ts, args.min_bytes)
    ok = True
    if found:
        ok = False
        print(f"\n[X] 기준 시각 이후 생긴 이미지 파일 {len(found)}개:")
        for p, kind, size in found[:50]:
            print(f"    {p}  ({kind}, {size // 1024} KB)")
        if len(found) > 50:
            print(f"    … 외 {len(found) - 50}개")
    else:
        print("\n[O] 디스크에 새로 생긴 이미지 파일 없음")

    if args.pod:
        try:
            with urllib.request.urlopen(f"{args.pod.rstrip('/')}/health", timeout=5) as r:
                health = json.loads(r.read())
            held = health.get("pipeline", {}).get("held_photos")
            if held == 0:
                print("[O] 팟 메모리 사진 0")
            else:
                ok = False
                print(f"[X] 팟 메모리에 사진 {held}장 남아 있음 — 처리 중이 아니라면 해제 누락")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[X] 팟 /health 조회 실패: {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
