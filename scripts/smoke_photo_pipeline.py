"""사진 파이프라인(F02~F05) 통합 스모크 — 실제 Supabase 대상.

사용법:
    python scripts/smoke_photo_pipeline.py

⚠️ **진짜 DB와 Storage에 쓴다.** 공유 무료 티어라 주의할 것.
   만든 유저를 끝에 통째로 지우고(Storage prefix → DB 순서), 실패해도 finally에서
   정리한다. 사용자·세션에는 이름 컬럼이 없어 test_ 접두사를 붙일 수 없으므로,
   "만든 것만 정확히 지운다"로 대신한다.

⚠️ 세그멘테이션 워커는 돌리지 않는다. SEG_* 잡이 PENDING으로 남았다가 유저와 함께
   CASCADE로 지워진다. 여기서 보는 건 "사진이 들어와 잡이 걸릴 때까지"다.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
from app.services import db, storage  # noqa: E402

API = "/api/v1"
client = TestClient(app)

passed: list[str] = []
failed: list[str] = []


def check(label: str, cond: bool, note: str = "") -> None:
    (passed if cond else failed).append(label)
    mark = "O" if cond else "X"
    print(f"  [{mark}] {label}" + (f"  — {note}" if note else ""))


def jpeg(width: int = 600, height: int = 800) -> bytes:
    """세로로 색이 변하는 더미 사진. 좌우 구분이 되게 왼쪽 절반을 밝게 한다."""
    img = Image.new("RGB", (width, height), (40, 60, 90))
    for x in range(width // 2):
        for y in range(0, height, 4):
            img.putpixel((x, y), (200, 180, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def landmarks(left_shoulder_x: float = 0.70) -> str:
    lms = [{"index": i, "x": 0.5, "y": i / 100, "z": 0.0, "visibility": 0.9} for i in range(33)]
    lms[11].update(x=left_shoulder_x, y=0.30)  # left_shoulder
    lms[12].update(x=1.0 - left_shoulder_x, y=0.35)  # right_shoulder
    return json.dumps(lms)


def upload(path: str, headers: dict, data: dict, name: str = "p.jpg"):
    return client.post(
        path,
        headers=headers,
        data=data,
        files={"file": (name, jpeg(), "image/jpeg")},
    )


def main() -> int:
    print("=" * 68)
    print("Phase 2 통합 스모크 (실제 Supabase)")
    print("=" * 68)

    # ── F02 사용자 ────────────────────────────────────────────────────────
    print("\nF02 사용자 식별자")
    r = client.post(f"{API}/users")
    check("POST /users → 201", r.status_code == 201, f"status={r.status_code}")
    if r.status_code != 201:
        print(r.text[:400])
        return 1
    user_id = r.json()["user_id"]
    H = {"X-User-Id": user_id}

    check("GET /users/me → 200", client.get(f"{API}/users/me", headers=H).status_code == 200)
    check("헤더 없으면 401", client.get(f"{API}/users/me").status_code == 401)
    check(
        "없는 유저는 404",
        client.get(f"{API}/users/me", headers={"X-User-Id": str(UUID(int=0))}).status_code == 404,
    )

    try:
        # ── F03 세션 ──────────────────────────────────────────────────────
        print("\nF03 세션")
        r = client.post(f"{API}/sessions", headers=H)
        check("POST /sessions → 201", r.status_code == 201, f"status={r.status_code}")
        session_id = r.json()["session_id"]

        r2 = client.post(f"{API}/sessions", headers=H)
        check("중복 생성 → 409", r2.status_code == 409, r2.json().get("error", {}).get("code", ""))
        check(
            "409에 기존 session_id 포함",
            r2.json().get("error", {}).get("detail", {}).get("session_id") == session_id,
        )

        r = client.get(f"{API}/sessions/active", headers=H)
        check("GET /sessions/active → 200", r.status_code == 200)
        steps = r.json()["steps"]
        check("초기 단계는 전부 미완료", steps["reference_photo"]["uploaded"] is False)

        SP = f"{API}/sessions/{session_id}/photos"

        # ── F04 레퍼런스 ──────────────────────────────────────────────────
        print("\nF04 레퍼런스 사진")
        base = {"pose_landmarks": landmarks(), "pose_scale_basis": "TORSO"}

        r = upload(f"{SP}/reference", H, base)
        check("업로드 → 201", r.status_code == 201, r.text[:160] if r.status_code != 201 else "")
        if r.status_code != 201:
            return 1
        ref = r.json()
        check("SEG_REFERENCE 잡 생성", bool(ref["job_id"]))
        check("랜드마크 33개 반환", len(ref["pose_landmarks"]) == 33)
        check("was_mirrored=false", ref["was_mirrored"] is False)
        check("signed_url 발급", ref["signed_url"].startswith("http"))
        first_photo_id = ref["photo_id"]

        r = client.get(f"{SP}/reference", headers=H)
        check("GET 레퍼런스 → 200", r.status_code == 200)
        check("segmented=false (워커 안 돌았으므로)", r.json()["segmented"] is False)

        # 거울 재업로드 = 교체
        r = upload(f"{SP}/reference", H, {**base, "is_mirrored": "true"})
        check("거울 재업로드 → 201", r.status_code == 201, r.text[:160])
        mirrored = r.json()
        check("교체되어 photo_id가 바뀜", mirrored["photo_id"] != first_photo_id)
        check("was_mirrored=true", mirrored["was_mirrored"] is True)
        ls = mirrored["pose_landmarks"][11]
        rs = mirrored["pose_landmarks"][12]
        # 보낸 값: left(x .70, y .30) / right(x .30, y .35)
        # ⚠️ x 뒤집기와 좌/우 이름표 스왑이 겹쳐 x는 제자리로 돌아온다.
        #    실제로 바뀌는 건 y·z·visibility 다 — left의 y가 상대쪽(.35)으로 와야 한다.
        #    "x가 그대로라 멀쩡해 보이는" 게 이 로직이 조용히 틀리는 지점이다.
        check(
            "랜드마크 좌우가 되돌려짐 (y가 상대쪽 값으로)",
            abs(ls["x"] - 0.70) < 1e-6
            and abs(ls["y"] - 0.35) < 1e-6
            and abs(rs["y"] - 0.30) < 1e-6,
            f"left=(x{ls['x']:.2f},y{ls['y']:.2f}) right=(x{rs['x']:.2f},y{rs['y']:.2f})",
        )
        check(
            "교체 후에도 사진은 1장",
            len(db.rows_for_session("photo", UUID(session_id), "photo_id,kind")) == 1,
        )

        # ── F05 사용자 사진 — 거부 경로 ───────────────────────────────────
        print("\nF05 사용자 사진 — 거부되어야 하는 것들")
        ok = {
            "capture_source": "CAPTURE",
            "pose_landmarks": landmarks(),
            "pose_scale_basis": "TORSO",
            "pose_similarity": "95.0",
            "framing_score": "0.9",
        }

        r = upload(f"{SP}/user", H, {**ok, "pose_similarity": "71.2"})
        err = r.json().get("error", {})
        check("포즈 미달 → 422 POSE", r.status_code == 422 and err.get("code") == "POSE_MISMATCH")
        check("reason=POSE", err.get("detail", {}).get("reason") == "POSE")
        check("임계값을 detail에 내려줌", "threshold" in err.get("detail", {}))

        r = upload(f"{SP}/user", H, {**ok, "framing_score": "0.5"})
        check(
            "프레이밍 미달 → 422 FRAMING",
            r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "FRAMING",
        )

        r = upload(f"{SP}/user", H, {**ok, "pose_scale_basis": "HIP_KNEE"})
        check(
            "스케일 기준 불일치 → 422",
            r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "FRAMING",
        )

        r = upload(f"{SP}/user", H, {**ok, "multi_person": "true"})
        check("여러 명 → 422 MULTI_PERSON", r.json().get("error", {}).get("code") == "MULTI_PERSON")

        r = upload(f"{SP}/user", H, {**ok, "pose_landmarks": "[]"})
        check(
            "랜드마크 없음 → 422 NO_PERSON",
            r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "NO_PERSON",
        )

        r = client.post(
            f"{SP}/user",
            headers=H,
            data=ok,
            files={"file": ("p.gif", b"GIF89a", "image/gif")},
        )
        check("gif → 415", r.status_code == 415, f"status={r.status_code}")

        check(
            "거부된 사진은 저장되지 않음",
            db.get_photo(UUID(session_id), "USER") is None,
        )

        # ── F05 통과 경로 ─────────────────────────────────────────────────
        print("\nF05 사용자 사진 — 통과")
        r = upload(f"{SP}/user", H, ok)
        check("업로드 → 201", r.status_code == 201, r.text[:160] if r.status_code != 201 else "")
        if r.status_code == 201:
            u = r.json()
            check("SEG_USER 잡 생성", bool(u["job_id"]))
            check("pose_similarity 저장", abs(u["pose_similarity"] - 95.0) < 1e-6)

        r = client.get(f"{API}/sessions/active", headers=H)
        steps = r.json()["steps"]
        check("active: 레퍼런스 uploaded", steps["reference_photo"]["uploaded"] is True)
        check("active: 사용자 uploaded", steps["user_photo"]["uploaded"] is True)
        check("active: 잡 상태 노출", steps["user_photo"]["job_status"] == "PENDING")

        # ── 소유권 ────────────────────────────────────────────────────────
        print("\n소유권 검증")
        other = client.post(f"{API}/users").json()["user_id"]
        r = client.get(f"{SP}/reference", headers={"X-User-Id": other})
        check("남의 세션 조회 → 404 (403 아님)", r.status_code == 404, f"status={r.status_code}")
        storage.delete_user_files(UUID(other))
        db.delete_user(UUID(other))

    finally:
        print("\n정리")
        removed = storage.delete_user_files(UUID(user_id))
        db.delete_user(UUID(user_id))
        print(f"  Storage 삭제: {removed}")
        print(f"  유저 삭제: {user_id}")

    print("\n" + "=" * 68)
    print(f"통과 {len(passed)} / 실패 {len(failed)}")
    if failed:
        for f in failed:
            print(f"  [X] {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
