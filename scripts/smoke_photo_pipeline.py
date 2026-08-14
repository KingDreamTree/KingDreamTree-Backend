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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.enums import SEG_KINDS, JobKind  # noqa: E402
from app.services import db, storage  # noqa: E402
from app.worker import queue  # noqa: E402

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

    # ⚠️ 만든 유저는 여기에 모아두고 finally에서 전부 지운다.
    #    유저를 만든 직후부터 try 안이어야 한다 — 그 사이에서 예외가 나면
    #    공유 무료 티어에 테스트 유저가 남는다.
    created: list[str] = [user_id]

    try:
        check("GET /users/me → 200", client.get(f"{API}/users/me", headers=H).status_code == 200)
        check("헤더 없으면 401", client.get(f"{API}/users/me").status_code == 401)
        check(
            "없는 유저는 404",
            client.get(f"{API}/users/me", headers={"X-User-Id": str(UUID(int=0))}).status_code
            == 404,
        )

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

        r = upload(f"{SP}/user", H, {**ok, "pose_similarity": "40.0"})  # THRESHOLD=70 미만
        err = r.json().get("error", {})
        check("포즈 미달 → 422 POSE", r.status_code == 422 and err.get("code") == "POSE_MISMATCH")
        check("reason=POSE", err.get("detail", {}).get("reason") == "POSE")
        check("임계값을 detail에 내려줌", "threshold" in err.get("detail", {}))

        # ⚠️ 거리(framing)는 **유도선(F_MIN)이 아니라 거부선(F_HARD)으로만** 막는다.
        #    부위 굵기를 몸통 길이로 나눠 비교하므로 거리 차이는 계산에서 상쇄된다.
        #    유도선에서 막으면 고쳐도 이득이 없는 이유로 사용자를 돌려보내게 된다.
        #    (통과하는 쪽은 아래 "거부된 사진은 저장되지 않음" 검사 뒤에서 확인한다)
        r = upload(f"{SP}/user", H, {**ok, "framing_score": "0.3"})  # F_HARD=0.40 미만
        check(
            "거리가 극단적으로 다르면 → 422 FRAMING",
            r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "FRAMING",
            f"status={r.status_code}",
        )

        # ⚠️ FACING 관문은 2026-08-14 에 뺐다 — facing_delta 는 관찰용으로 저장만 한다.
        #    돌아간 값이 있어도 다른 사유(여기서는 POSE)로만 거부되는지 본다.
        #    (성공 업로드로 확인하면 사진이 저장돼 뒤의 검사 상태를 흔든다)
        r = upload(f"{SP}/user", H, {**ok, "pose_similarity": "40.0", "facing_delta": "0.5"})
        check(
            "몸이 돌아가도 FACING 으로는 거부하지 않음 (POSE 가 나와야 함)",
            r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "POSE",
            f"status={r.status_code}",
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
            files={"file": ("not-an-image.txt", b"hello world", "image/jpeg")},
        )
        check(
            "이미지가 아니면 415 (Content-Type이 jpeg여도)",
            r.status_code == 415,
            f"status={r.status_code}",
        )

        check(
            "거부된 사진은 저장되지 않음",
            db.get_photo(UUID(session_id), "USER") is None,
        )

        # ⚠️ 위 검사가 끝난 뒤에 확인한다 — 여기서 성공하면 사진이 남기 때문이다.
        r = upload(f"{SP}/user", H, {**ok, "framing_score": "0.5"})  # F_MIN 미만, F_HARD 이상
        check(
            "거리가 유도선에 못 미쳐도 통과 (고쳐도 이득 없는 이유로 막지 않는다)",
            r.status_code == 201,
            f"status={r.status_code}",
        )
        check(
            "그때는 저장된다",
            db.get_photo(UUID(session_id), "USER") is not None,
        )

        # ⚠️ 형식 판별을 Content-Type 헤더가 아니라 실제 디코딩으로 하는지.
        #    아이폰 HEIC는 브라우저가 빈 값이나 octet-stream 을 붙여 보내는 경우가 있어,
        #    헤더로 거르면 멀쩡한 사진이 막힌다.
        webp = io.BytesIO()
        Image.open(io.BytesIO(jpeg())).save(webp, format="WEBP")
        r = client.post(
            f"{SP}/user",
            headers=H,
            data=ok,
            files={"file": ("photo", webp.getvalue(), "application/octet-stream")},
        )
        check(
            "Content-Type이 octet-stream이어도 열리면 통과",
            r.status_code == 201,
            f"status={r.status_code}",
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

        # ── 좀비 잡 회수 ──────────────────────────────────────────────────
        print("\n좀비 잡 회수")
        jobs = queue.list_jobs(UUID(session_id))
        check("세그 잡이 큐에 있음", len(jobs) >= 2, f"{len(jobs)}개")

        sb = db.get_client()  # ⚠️ 모듈 레벨 TestClient(client)와 이름이 겹치지 않게
        stale = (
            datetime.now(timezone.utc) - timedelta(seconds=settings.job_stale_after_sec * 2)
        ).isoformat()

        # (1) 재시도 여력이 남은 좀비 → PENDING 으로 되살아나야 한다
        alive = jobs[0]["job_id"]
        sb.table("job").update({"status": "PROCESSING", "attempts": 1, "started_at": stale}).eq(
            "job_id", alive
        ).execute()

        # (2) 재시도를 소진한 좀비 → PENDING 으로 두면 아무도 안 집는다. FAILED 여야 한다
        exhausted = jobs[1]["job_id"]
        sb.table("job").update(
            {"status": "PROCESSING", "attempts": settings.job_max_attempts, "started_at": stale}
        ).eq("job_id", exhausted).execute()

        # (3) 방금 시작한 잡 → 건드리면 안 된다 (돌고 있는 워커의 잡을 빼앗는 셈)
        fresh = queue.enqueue(UUID(session_id), JobKind.SEG_USER, {"photo_id": None})["job_id"]
        sb.table("job").update(
            {
                "status": "PROCESSING",
                "attempts": 1,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("job_id", fresh).execute()

        reclaimed = queue.reclaim_stale(SEG_KINDS)
        by_id = {j["job_id"]: j for j in reclaimed}

        check("멈춘 잡을 회수함", alive in by_id, f"{len(reclaimed)}개 회수")
        if alive in by_id:
            check(
                "재시도 여력 있으면 PENDING",
                by_id[alive]["status"] == "PENDING",
                by_id[alive]["status"],
            )
        check("재시도 소진 잡도 회수함", exhausted in by_id)
        if exhausted in by_id:
            check(
                "재시도 소진이면 FAILED (PENDING이면 영영 안 집힌다)",
                by_id[exhausted]["status"] == "FAILED",
                by_id[exhausted]["status"],
            )
            check("사용자에게 보여줄 에러 문구", bool(by_id[exhausted]["error"]))
        check("방금 시작한 잡은 건드리지 않음", fresh not in by_id)
        check(
            "회수된 잡을 워커가 다시 집을 수 있음",
            queue.claim(SEG_KINDS) is not None,
        )

        # ── 소유권 ────────────────────────────────────────────────────────
        print("\n소유권 검증")
        other = client.post(f"{API}/users").json()["user_id"]
        created.append(other)  # 여기서 예외가 나도 finally가 지우도록
        r = client.get(f"{SP}/reference", headers={"X-User-Id": other})
        check("남의 세션 조회 → 404 (403 아님)", r.status_code == 404, f"status={r.status_code}")

    finally:
        print("\n정리")
        for uid in created:
            removed = storage.delete_user_files(UUID(uid))
            db.delete_user(UUID(uid))
            print(f"  유저 삭제: {uid}  Storage {removed}")

    print("\n" + "=" * 68)
    print(f"통과 {len(passed)} / 실패 {len(failed)}")
    if failed:
        for f in failed:
            print(f"  [X] {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
