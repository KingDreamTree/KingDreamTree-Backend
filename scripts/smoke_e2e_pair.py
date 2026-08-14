"""레퍼런스 + 사용자 **두 장**을 올려 비교가 성립하는 데까지 간다.

    python scripts/smoke_e2e_pair.py --ref photos/123.jpg --user photos/456.jpg

⚠️ smoke_e2e_segmentation.py 는 **레퍼런스 한 장만** 올린다. 그래서 세그멘테이션은
   확인되지만 **진단은 못 돌린다** — 진단은 두 장을 견주는 것이라서.
   담당 B 에게 넘기려면 이 스크립트로 만든 세션이어야 한다.

⚠️ 사용자 사진은 **자세 관문을 통과해야** 올라간다. 서로 다른 사진 두 장이면
   막히는 게 정상이다. 그때 무엇에 걸렸는지 그대로 찍어준다 —
   "실패"가 아니라 관문이 일하고 있다는 뜻이다.

⚠️ 자세 값은 MediaPipe(프론트)가 재는 것이라 여기서는 잴 수 없다.
   --pose 로 넘기거나, 기본값(통과하는 값)으로 배선만 확인한다.
   **그래서 이 스크립트는 자세 판정을 검증하지 않는다.** 배선 확인용이다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import db  # noqa: E402

API = "/api/v1"
client = TestClient(app)

ok = fail = 0


def check(label: str, cond: bool, extra: str = "") -> bool:
    global ok, fail
    if cond:
        ok += 1
        print(f"  [O] {label}" + (f"  — {extra}" if extra else ""))
    else:
        fail += 1
        print(f"  [X] {label}" + (f"  — {extra}" if extra else ""))
    return cond


def landmarks_json() -> str:
    """33개 자리를 채운 더미. 서버는 형식만 본다(개수·범위)."""
    return json.dumps(
        [{"index": i, "x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.95} for i in range(33)]
    )


def wait_for_job(job_id: str, headers: dict, timeout: int) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        job = client.get(f"{API}/jobs/{job_id}", headers=headers).json()
        if job["status"] != last:
            last = job["status"]
            print(f"      {last} ({int(time.time() - t0)}s)")
        if job["status"] in ("DONE", "FAILED"):
            return job
        time.sleep(2)
    return {"status": "TIMEOUT"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="레퍼런스 (닮고 싶은 몸)")
    ap.add_argument("--user", required=True, help="사용자 사진")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--pose", type=float, default=95.0, help="pose_similarity")
    ap.add_argument("--framing", type=float, default=1.0, help="framing_score")
    ap.add_argument("--facing", type=float, default=0.0, help="facing_delta")
    args = ap.parse_args()

    ref_path, user_path = Path(args.ref), Path(args.user)
    for p in (ref_path, user_path):
        if not p.exists():
            print(f"[X] 파일이 없습니다: {p}")
            return 1

    print("=" * 70)
    print("전 구간 — 레퍼런스 + 사용자 두 장")
    print("=" * 70)

    user_id = client.post(f"{API}/users").json()["user_id"]
    H = {"X-User-Id": user_id}
    session_id = client.post(f"{API}/sessions", headers=H).json()["session_id"]
    SP = f"{API}/sessions/{session_id}/photos"

    print(f"  user_id     {user_id}")
    print(f"  session_id  {session_id}")

    base = {"pose_landmarks": landmarks_json(), "pose_scale_basis": "TORSO"}

    # ── 레퍼런스 ────────────────────────────────────────────────────────
    print(f"\n[1] 레퍼런스 업로드 — {ref_path.name}")
    r = client.post(
        f"{SP}/reference",
        headers=H,
        data=base,
        files={"file": (ref_path.name, ref_path.read_bytes(), "image/jpeg")},
    )
    if not check("업로드 → 201", r.status_code == 201, r.text[:200]):
        return 1
    ref = r.json()

    print("\n[2] 레퍼런스 세그멘테이션")
    job = wait_for_job(ref["job_id"], H, args.timeout)
    if not check("SEG_REFERENCE 완료", job["status"] == "DONE", str(job.get("error"))[:200]):
        return 1
    res = job.get("result") or {}
    print(f"      검출 {res.get('detected')}클래스 / 비교가능 {res.get('valid_comparable')}")

    # ── 사용자 ──────────────────────────────────────────────────────────
    print(f"\n[3] 사용자 업로드 — {user_path.name}")
    print(f"      자세 {args.pose} / 거리 {args.framing} / 방향 {args.facing}")
    r = client.post(
        f"{SP}/user",
        headers=H,
        data={
            **base,
            "capture_source": "UPLOAD",
            "pose_similarity": args.pose,
            "framing_score": args.framing,
            "facing_delta": args.facing,
        },
        files={"file": (user_path.name, user_path.read_bytes(), "image/jpeg")},
    )
    if r.status_code != 201:
        # ⚠️ 관문에 걸린 것과 서버가 깨진 것을 구분해서 보여준다.
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        err = body.get("error", {})
        print(f"  [!] 업로드 거부 — HTTP {r.status_code}")
        print(f"      code    {err.get('code')}")
        print(f"      message {err.get('message')}")
        if err.get("detail"):
            print(f"      detail  {json.dumps(err['detail'], ensure_ascii=False)[:300]}")
        print("\n      ⚠️ 관문이 막은 것이면 **정상 동작**이다.")
        print("         자세 값을 --pose/--framing/--facing 으로 조정해 배선만 볼 수 있다.")
        print(f"\n      user_id={user_id}  session_id={session_id}  (레퍼런스는 남아 있음)")
        return 1
    usr = r.json()
    check("업로드 → 201", True, f"photo_id={usr['photo_id']}")

    print("\n[4] 사용자 세그멘테이션")
    job = wait_for_job(usr["job_id"], H, args.timeout)
    if not check("SEG_USER 완료", job["status"] == "DONE", str(job.get("error"))[:200]):
        return 1
    res = job.get("result") or {}
    print(f"      검출 {res.get('detected')}클래스 / 비교가능 {res.get('valid_comparable')}")

    # ── 비교가 성립하는가 ───────────────────────────────────────────────
    print("\n[5] 두 장을 견줄 수 있는가")
    r = client.get(f"{API}/sessions/{session_id}", headers=H)
    check("세션 조회 → 200", r.status_code == 200, r.text[:150])
    steps = r.json().get("steps", {})
    print(f"      단계: {json.dumps(steps, ensure_ascii=False)}")

    print("\n" + "=" * 70)
    print(f"통과 {ok} / 실패 {fail}")
    print("=" * 70)
    print("\n담당 B 에게 넘길 값:")
    print(f"  user_id     {user_id}")
    print(f"  session_id  {session_id}")
    print("\n  ⚠️ 모든 요청에 X-User-Id 헤더 필요")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
