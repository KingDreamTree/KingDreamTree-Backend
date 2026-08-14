"""자세 판정 데모를 한 번에 띄운다.

    python scripts/run_pose_demo.py

무엇을 하나
    1) API 서버(uvicorn)를 띄운다 — 데모가 임계값을 받아가는 곳
    2) web/ 을 정적 서버로 띄운다
    3) 준비되면 브라우저를 연다

⚠️ **파일을 더블클릭해서 열면 안 된다.** file:// 로는 두 가지가 막힌다.
     * ES 모듈 import (CORS 정책)
     * 카메라 (보안 컨텍스트가 아님)
   localhost 는 보안 컨텍스트로 취급되므로 이 스크립트로 띄워야 한다.

⚠️ 인터넷이 필요하다. MediaPipe 는 CDN(jsdelivr)에서 받는다.

⚠️ API 가 이미 떠 있으면 새로 띄우지 않는다. 그래야 `--reload` 로 코드
   고치면서 쓰던 서버를 죽이지 않는다.
"""

from __future__ import annotations

import http.server
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

API_PORT = 8000
WEB_PORT = 8080
API_BASE = f"http://localhost:{API_PORT}/api/v1"
CRITERIA = f"{API_BASE}/pose-criteria"


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _api_ready() -> bool:
    """임계값 엔드포인트가 실제로 답하는지 본다.

    ⚠️ 포트만 보면 안 된다. 다른 프로세스가 8000 을 쓰고 있을 수도 있고,
       uvicorn 이 떴어도 import 에러로 죽어가는 중일 수 있다.
    """
    try:
        with urllib.request.urlopen(CRITERIA, timeout=1) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """요청 로그를 끈다.

    ⚠️ functools.partial 에 log_message 를 붙이면 **아무 일도 안 일어난다.**
       핸들러 인스턴스는 원래 클래스의 메서드를 쓴다. 서브클래스로 해야 한다.
       (그렇게 했다가 favicon 404 까지 찍혀서 안내가 묻혔다)
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, *args, **kwargs) -> None:
        pass


def _serve_web() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", WEB_PORT), _QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> int:
    if not WEB.is_dir():
        print(f"web/ 폴더가 없습니다: {WEB}")
        return 1

    if _port_open(WEB_PORT):
        print(f"포트 {WEB_PORT} 가 이미 쓰이고 있습니다. 그 프로그램을 끄고 다시 실행하세요.")
        return 1

    # ── 1. API ────────────────────────────────────────────────────────────
    api: subprocess.Popen[bytes] | None = None

    if _api_ready():
        print(f"API 이미 떠 있음 — {API_BASE}")
    elif _port_open(API_PORT):
        print(f"포트 {API_PORT} 를 다른 프로그램이 쓰고 있는데 API 응답이 아닙니다.")
        print("그 프로그램을 끄거나, 이미 uvicorn 이면 로그에서 에러를 확인하세요.")
        return 1
    else:
        print("API 서버 시작 중...")
        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(API_PORT)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        for _ in range(60):  # 최대 30초
            if _api_ready():
                break
            if api.poll() is not None:
                print("\nAPI 서버가 시작하자마자 죽었습니다. 직접 띄워서 에러를 보세요:")
                print("    uvicorn app.main:app --reload")
                print("(대개 .env 문제입니다)")
                return 1
            time.sleep(0.5)
        else:
            print("\nAPI 가 30초 안에 응답하지 않습니다.")
            api.terminate()
            return 1
        print(f"API 준비됨 — {API_BASE}")

    # ── 2. 정적 서버 ──────────────────────────────────────────────────────
    _serve_web()
    live = f"http://localhost:{WEB_PORT}/pose-live.html"
    demo = f"http://localhost:{WEB_PORT}/pose-demo.html"
    tests = f"http://localhost:{WEB_PORT}/pose-score.test.html"
    e2e = f"http://localhost:{WEB_PORT}/e2e-test.html"

    print()
    print("=" * 62)
    print(f"  전 구간    {e2e}       ← 사진 2장 → 진단 → 루틴")
    print(f"  실서비스형 {live}      ← 사진 올리고 따라 서기")
    print(f"  실험용     {demo}      ← 웹캠으로 기준까지 잡기")
    print(f"  자가진단   {tests}")
    print("=" * 62)
    print()
    print("  전 구간 쓰는 법")
    print("    1. 레퍼런스 사진 + 내 사진을 각각 고르기 (둘 다 전신)")
    print("    2. 다른 사람이면 자세 점수가 낮게 나옵니다 — 정상입니다.")
    print("       '관문 무시'를 켜면 뒷단(세그·진단·루틴)까지 볼 수 있습니다.")
    print("    3. '전 구간 실행'")
    print()
    print("  ⚠️ 워커 2개가 떠 있어야 합니다 (없으면 잡이 PENDING 에서 멈춥니다)")
    print("       python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER")
    print("       python -m app.worker.run --kinds OCR_INBODY,VLM_PART,VLM_OVERALL,"
          "ROUTINE_GEN,ROUTINE_PATCH")
    print()
    print("  실서비스형 쓰는 법")
    print("    1. '기준 받아오기'")
    print("    2. 왼쪽에 닮고 싶은 몸 사진을 끌어다 놓기 (전신)")
    print("    3. '카메라 켜기' → 권한 허용")
    print("    4. 사진과 같은 자세로 서면 점수가 올라갑니다")
    print()
    print("  ⚠️ 전신이 나와야 합니다. 노트북 웹캠이면 2m 이상 떨어지세요.")
    print("  ⚠️ 화면을 눕히지 마세요 — 위에서 내려다보면 다리가 잘립니다.")
    print()
    print("  끝내려면 Ctrl+C")
    print()
    # ⚠️ 여기서 비워주지 않으면 안내가 버퍼에 갇혀 있다가 종료할 때 나온다.
    #    정작 필요한 순간에 화면이 비어 있게 된다.
    sys.stdout.flush()

    webbrowser.open(live)

    try:
        while True:
            time.sleep(1)
            if api is not None and api.poll() is not None:
                print("\nAPI 서버가 종료됐습니다.")
                return 1
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        if api is not None:
            api.terminate()
            try:
                api.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
