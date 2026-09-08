"""팟 직접 업로드 경로 **전 구간** 스모크 — 실제 API + 실제 팟 + 실제 Supabase/OpenAI.

    # 터미널 1 — 팟 (GPU, PHOTO_PIPELINE=pod, POD_UPLOAD_SECRET)
    python -m app.pod.main
    # 터미널 2 — API (같은 .env)
    uvicorn app.main:app --port 8000
    # 터미널 3
    python scripts/smoke_pod_pipeline.py --ref photos/123.jpg --user photos/456.jpg

무엇을 보나
    사용자 생성 → 세션 → 기준/사용자 **랜드마크만** 등록 → 토큰 → 팟에 두 장 업로드(202)
    → 잡 폴링(SEG×2, VLM_PART, VLM_OVERALL) → 결과 조회
    그리고 프라이버시 사후 확인:
      · photo.storage_path 가 NULL, crop_box 가 있음
      · Storage photos 버킷의 {user_id}/ 아래에 파일이 없음
      · 팟 /health 의 held_photos 가 0 (메모리 해제)
      · part/overall 진단 raw_response 에 이미지 데이터가 없음

⚠️ **진짜 DB·Storage·OpenAI 를 쓴다** (스크리닝 1 + 부위 1 + 종합 1 = GPT 3회).
   끝나면 만든 유저를 통째로 지운다. --keep 으로 남길 수 있다.
⚠️ 자세 값은 프론트(MediaPipe)가 재는 것이라 여기서는 통과값을 넣는다 — 배선 확인용이다.
⚠️ 사용자 사진이 스크리닝에 반려(422)되면 그건 실패가 아니라 관문이 일한 것이다.
   그때는 다른 사진으로 다시 돌린다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FAILS: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> bool:
    print(("[O] " if cond else "[X] ") + label + (f"  — {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)
    return cond


def landmarks_json(path: str | None = None) -> str:
    """33개 랜드마크 JSON. 파일(scripts/pose_landmarks.py 출력)이 있으면 그것, 없으면 더미.

    ⚠️ 더미는 인물이 중앙 세로로 서 있다고 가정한 것이라 얼굴 박스가 실제 얼굴과
       안 맞는다. 얼굴 가림 실측(진단 비교)에는 반드시 실제 랜드마크 파일을 준다.
    """
    if path:
        return Path(path).read_text(encoding="utf-8")
    return json.dumps(
        [
            {"index": i, "x": 0.5, "y": 0.1 + i * 0.025, "z": 0.0, "visibility": 0.95}
            for i in range(33)
        ]
    )


def wait_job(api: httpx.Client, job_id: str, headers: dict, timeout: int, label: str) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        job = api.get(f"/jobs/{job_id}", headers=headers).json()
        if job.get("status") != last:
            print(f"    {label}: {job.get('status')} (시도 {job.get('attempts')})")
            last = job.get("status")
        if job.get("status") in ("DONE", "FAILED"):
            return job
        time.sleep(1.5)
    return {"status": "TIMEOUT", "job_id": job_id}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000/api/v1")
    ap.add_argument("--pod", default="http://localhost:8080")
    ap.add_argument("--ref", default="photos/123.jpg")
    ap.add_argument("--user", default="photos/456.jpg")
    ap.add_argument(
        "--ref-landmarks", default=None, help="기준 사진 랜드마크 JSON 파일 (없으면 더미)"
    )
    ap.add_argument(
        "--user-landmarks", default=None, help="사용자 사진 랜드마크 JSON 파일 (없으면 더미)"
    )
    ap.add_argument("--quick", action="store_true", help="세그 없는 웹캠 경로")
    ap.add_argument("--keep", action="store_true", help="끝나도 유저를 지우지 않는다")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument(
        "--pre-migration",
        action="store_true",
        help=(
            "photo.storage_path NOT NULL 해제 마이그레이션이 아직 안 된 DB 에서 파이프라인만 볼 때. "
            "사진 행을 API 대신 직접 넣는다(storage_path 에 자리표시 문자열). "
            "⚠️ 'storage_path NULL' 확인은 건너뛴다 — 마이그레이션 뒤 옵션 없이 다시 돌릴 것"
        ),
    )
    args = ap.parse_args()

    ref_path, user_path = Path(args.ref), Path(args.user)
    for p in (ref_path, user_path):
        if not p.is_file():
            print(f"파일 없음: {p}")
            return 2

    api = httpx.Client(base_url=args.api, timeout=60)
    pod = httpx.Client(base_url=args.pod, timeout=120)

    print("== 팟 헬스 ==")
    h = pod.get("/health").json()
    print("   ", json.dumps(h, ensure_ascii=False)[:300])
    if not check("팟 status ok", h.get("status") == "ok", str(h.get("status"))):
        return 1
    held_before = h["pipeline"]["held_photos"]

    user_id = api.post("/users").json()["user_id"]
    headers = {"X-User-Id": user_id}
    session_id = None
    try:
        session_id = api.post("/sessions", headers=headers).json()["session_id"]
        print(f"\n== 세션 {session_id} (user {user_id}) ==")

        print("== 1. 랜드마크만 등록 (사진 없음) ==")
        if args.pre_migration:
            from app.services import db as _db

            print("    (--pre-migration: 행을 직접 넣는다 — storage_path 자리표시)")
            for kind, lm_path, extra in (
                ("REFERENCE", args.ref_landmarks, {}),
                (
                    "USER",
                    args.user_landmarks,
                    {
                        "capture_source": "UPLOAD",
                        "pose_similarity": 95,
                        "framing_score": 1.0,
                        "facing_delta": 0.0,
                    },
                ),
            ):
                _db.create_photo(
                    {
                        "session_id": session_id,
                        "kind": kind,
                        "storage_bucket": "photos",
                        "storage_path": f"{user_id}/{session_id}/pending-pod.jpg",
                        "pose_landmarks": json.loads(landmarks_json(lm_path)),
                        "pose_scale_basis": "TORSO",
                        "was_mirrored": False,
                        **extra,
                    }
                )
            check("사진 행 2개 직접 삽입", True)
        else:
            r = api.post(
                f"/sessions/{session_id}/photos/reference",
                headers=headers,
                data={
                    "pose_landmarks": landmarks_json(args.ref_landmarks),
                    "pose_scale_basis": "TORSO",
                },
            )
            check("기준 사진 등록 201", r.status_code == 201, r.text[:200])
            check("  signed_url 없음", r.status_code == 201 and r.json()["signed_url"] is None)
            r = api.post(
                f"/sessions/{session_id}/photos/user",
                headers=headers,
                data={
                    "capture_source": "UPLOAD",
                    "pose_landmarks": landmarks_json(args.user_landmarks),
                    "pose_similarity": "95",
                    "framing_score": "1.0",
                    "pose_scale_basis": "TORSO",
                    "pipeline": "quick" if args.quick else "full",
                },
            )
            check("사용자 사진 등록 201", r.status_code == 201, r.text[:200])

        print("== 2. 토큰 → 팟 업로드 ==")
        r = api.post(f"/sessions/{session_id}/upload-token", headers=headers)
        check("토큰 발급 200", r.status_code == 200, r.text[:200])
        token = r.json()["token"]

        t0 = time.time()
        r = pod.post(
            "/upload",
            headers={"X-Upload-Token": token},
            data={"pipeline": "quick" if args.quick else "full"},
            files={
                "reference": (ref_path.name, ref_path.read_bytes(), "image/jpeg"),
                "user": (user_path.name, user_path.read_bytes(), "image/jpeg"),
            },
        )
        elapsed = time.time() - t0
        print(f"    업로드 응답 {r.status_code} ({elapsed:.1f}s): {r.text[:300]}")
        if r.status_code == 422:
            print("    → 스크리닝 반려. 관문이 일한 것이다. 다른 사진으로 다시 돌리세요.")
            return 3
        if not check("업로드 202 (스크리닝 통과, 접수)", r.status_code == 202):
            return 1
        check("  응답이 100초 안", elapsed < 100, f"{elapsed:.1f}s")
        body = r.json()
        jobs = body["jobs"]
        check("  crop_box 두 장", set(body.get("crop_box", {})) == {"REFERENCE", "USER"})
        masked = body.get("face_masked") or {}
        print(
            f"    face_masked: {masked}  (팟 POD_FACE_MASK 가 false 면 둘 다 false 가 정상. "
            "머리가 잘린 사진은 가릴 얼굴이 없어 false 가 정상)"
        )
        if h["pipeline"].get("face_mask", True):
            # 기준 사진은 얼굴이 있는 것을 쓴다는 전제. 사용자 사진은 머리 잘림이 허용되므로 강제하지 않는다
            check(
                "  기준 사진 얼굴 가림 (face_masked.REFERENCE)",
                masked.get("REFERENCE") is True,
                str(masked),
            )

        print("== 3. 잡 폴링 ==")
        results = {}
        for kind, job_id in jobs.items():
            results[kind] = wait_job(api, job_id, headers, args.timeout, kind)
        overall_jobs = api.get(
            f"/sessions/{session_id}/jobs", headers=headers, params={"kind": "VLM_OVERALL"}
        ).json()["items"]
        if overall_jobs:
            results["VLM_OVERALL"] = wait_job(
                api, overall_jobs[-1]["job_id"], headers, args.timeout, "VLM_OVERALL"
            )
        for kind, job in results.items():
            check(
                f"{kind} DONE",
                job.get("status") == "DONE",
                f"{job.get('status')} {job.get('error') or ''}",
            )

        prog = api.get(f"/sessions/{session_id}/analysis/progress", headers=headers).json()
        check("progress.completed", prog.get("completed") is True, json.dumps(prog)[:200])
        analysis = api.get(f"/sessions/{session_id}/analysis", headers=headers).json()
        parts = analysis.get("parts") or []
        check("부위 진단 행 있음", len(parts) > 0, f"{len(parts)}부위")
        check("종합 진단 있음", bool(analysis.get("overall")))

        print("== 4. 프라이버시 사후 확인 ==")
        from app.services import db, storage  # 서비스 키로 직접 본다 (관리자 시점)

        for kind in ("REFERENCE", "USER"):
            row = db.get_photo(session_id, kind)  # type: ignore[arg-type]
            if args.pre_migration:
                print(f"    {kind}: storage_path NULL · crop_box 확인은 건너뜀 (--pre-migration)")
            else:
                check(
                    f"{kind}: storage_path NULL",
                    row is not None and row.get("storage_path") is None,
                    str(row and row.get("storage_path")),
                )
                check(
                    f"{kind}: crop_box 기록",
                    bool(row and row.get("crop_box")),
                    str(row and row.get("crop_box")),
                )
            check(
                f"{kind}: width/height 기록", bool(row and row.get("width") and row.get("height"))
            )
        leftover = storage.list_prefix("photos", user_id)
        check("Storage photos 버킷에 이 유저 파일 없음", not leftover, str(leftover))
        if not args.quick and not args.pre_migration:
            seg = api.get(f"/sessions/{session_id}/segmentation", headers=headers).json()
            check(
                "세그 응답의 photo_url 이 null (사진 없음)",
                (seg.get("user") or {}).get("photo_url") is None,
            )
            check("세그 응답에 crop_box", bool((seg.get("user") or {}).get("crop_box")))
        elif not args.quick:
            # 자리표시 경로에 서명 URL 을 만들다 404 가 나므로(진짜 pod 행은 NULL 이라 건너뜀) 여기선 안 본다
            print("    세그 응답(photo_url null·crop_box) 확인은 건너뜀 (--pre-migration)")
        h2 = pod.get("/health").json()
        check(
            "팟 메모리 사진 해제 (held_photos 0)",
            h2["pipeline"]["held_photos"] == 0,
            str(h2["pipeline"]),
        )
        check("팟 held_photos 가 시작 전과 같음", h2["pipeline"]["held_photos"] == held_before)

        blob = json.dumps(
            {
                "parts": db.rows_for_session("part_diagnosis", session_id, "raw_response"),  # type: ignore[arg-type]
                "overall": db.rows_for_session("overall_diagnosis", session_id, "raw_response"),  # type: ignore[arg-type]
                "jobs": db.rows_for_session("job", session_id, "result,error,payload"),  # type: ignore[arg-type]
            }
        )
        check(
            "저장된 결과에 이미지 데이터 없음",
            "data:image" not in blob and "base64," not in blob,
            f"{len(blob)} bytes",
        )

    finally:
        if session_id and args.keep:
            print(f"\n--keep: user={user_id} session={session_id} 를 남깁니다")
        else:
            r = api.delete("/users/me", headers=headers)
            print(f"\n정리: DELETE /users/me → {r.status_code}")

    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
