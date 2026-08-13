"""세그멘테이션 조회 API(F06) · signed URL(F14) · 삭제(F15) 통합 스모크.

사용법:
    python scripts/smoke_segmentation_api.py

⚠️ **진짜 DB와 Storage에 쓴다.** 만든 유저를 끝에 통째로 지운다.

⚠️ GPU 워커를 돌리지 않는다. Sapiens2 추론 대신 **워커가 만들었을 행을 직접 넣어**
   조회 API만 검증한다. 팔레트 조립·비교 부위 교집합·제외 사유는 전부 DB 행에서
   파생되므로, 추론 없이도 로직을 정확히 확인할 수 있다.
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

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import db, sapiens_labels, storage  # noqa: E402

API = "/api/v1"
client = TestClient(app)

passed: list[str] = []
failed: list[str] = []


def check(label: str, cond: bool, note: str = "") -> None:
    (passed if cond else failed).append(label)
    print(f"  [{'O' if cond else 'X'}] {label}" + (f"  — {note}" if note else ""))


def photo_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (60, 80, 110)).save(buf, format="JPEG")
    return buf.getvalue()


def landmarks_json() -> str:
    return json.dumps(
        [{"index": i, "x": 0.5, "y": i / 100, "z": 0.0, "visibility": 0.9} for i in range(33)]
    )


def label_map_png(values: list[int], width: int = 120, height: int = 200) -> bytes:
    """8-bit 그레이스케일 라벨 맵. 가로로 띠를 나눠 값을 칠한다."""
    img = Image.new("L", (width, height), 0)
    band = height // (len(values) + 1)
    for i, v in enumerate(values):
        for y in range(band * (i + 1), band * (i + 2)):
            for x in range(width // 4, width * 3 // 4):
                img.putpixel((x, y), v)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def label_value_of(class_name: str) -> int:
    """공식 라벨 목록에서 이 클래스명의 픽셀 값."""
    return sapiens_labels.LABEL_NAMES.index(class_name)


def insert_segmentation(user_id: UUID, session_id: UUID, photo: dict, parts_spec: list[dict]):
    """워커가 만들었을 행을 직접 넣는다 (맵 PNG 업로드 포함)."""
    kind = str(photo["kind"])
    values = [label_value_of(p["class_name"]) for p in parts_spec]
    png = label_map_png(values)

    path = storage.map_path(user_id, session_id, kind)
    storage.upload(settings.bucket_segmentations, path, png, "image/png")

    person_pixels = sum(p["pixel_count"] for p in parts_spec)
    label_map = sapiens_labels.build_label_map(29)

    db.replace_segmentation(
        UUID(str(photo["photo_id"])),
        segmentation={
            "storage_bucket": settings.bucket_segmentations,
            "map_path": path,
            "map_width": 120,
            "map_height": 200,
            "label_map": label_map,
            "model_name": "sapiens2",
            "model_version": "sapiens2-seg-5b",
            "person_pixel_count": person_pixels,
            "person_area_ratio": 0.4,
            "detected_class_count": len(parts_spec) + 1,
            "inference_ms": 670,
        },
        parts=[
            {
                "class_name": p["class_name"],
                "label_value": label_value_of(p["class_name"]),
                "pixel_count": p["pixel_count"],
                "area_ratio": p["pixel_count"] / person_pixels,
                "bbox_x": 10,
                "bbox_y": 10,
                "bbox_w": 50,
                "bbox_h": 60,
                "is_truncated": False,
                "is_valid": p["is_valid"],
                "invalid_reason": p.get("invalid_reason"),
            }
            for p in parts_spec
        ],
    )


# 레퍼런스에는 오른팔 상완이 잘 나왔고, 사용자 사진에서는 옷에 가려진 상황.
# ⚠️ 이 케이스가 제일 중요하다 — 재촬영 안내를 내야 하는 상황이다.
REFERENCE_PARTS = [
    {"class_name": "Torso", "pixel_count": 40000, "is_valid": True},
    {"class_name": "Left_Upper_Arm", "pixel_count": 9000, "is_valid": True},
    {"class_name": "Right_Upper_Arm", "pixel_count": 8800, "is_valid": True},
    {
        "class_name": "Upper_Clothing",
        "pixel_count": 30000,
        "is_valid": False,
        "invalid_reason": "NOT_COMPARABLE",
    },
]
USER_PARTS = [
    {"class_name": "Torso", "pixel_count": 38000, "is_valid": True},
    {"class_name": "Left_Upper_Arm", "pixel_count": 8600, "is_valid": True},
    {
        "class_name": "Right_Upper_Arm",
        "pixel_count": 400,
        "is_valid": False,
        "invalid_reason": "TOO_SMALL",
    },
    {
        "class_name": "Upper_Clothing",
        "pixel_count": 31000,
        "is_valid": False,
        "invalid_reason": "NOT_COMPARABLE",
    },
]


def main() -> int:
    print("=" * 68)
    print("F06 세그멘테이션 조회 / F14 signed URL / F15 삭제")
    print("=" * 68)

    user_id = client.post(f"{API}/users").json()["user_id"]
    H = {"X-User-Id": user_id}

    try:
        session_id = client.post(f"{API}/sessions", headers=H).json()["session_id"]
        SP = f"{API}/sessions/{session_id}/photos"
        base = {"pose_landmarks": landmarks_json(), "pose_scale_basis": "TORSO"}

        client.post(
            f"{SP}/reference",
            headers=H,
            data=base,
            files={"file": ("r.jpg", photo_jpeg(), "image/jpeg")},
        )
        client.post(
            f"{SP}/user",
            headers=H,
            data={
                **base,
                "capture_source": "CAPTURE",
                "pose_similarity": "95.0",
                "framing_score": "0.9",
            },
            files={"file": ("u.jpg", photo_jpeg(), "image/jpeg")},
        )

        ref_photo = db.get_photo(UUID(session_id), "REFERENCE")
        user_photo = db.get_photo(UUID(session_id), "USER")

        print("\n세그 결과가 없을 때")
        r = client.get(f"{API}/photos/{ref_photo['photo_id']}/segmentation", headers=H)
        check("세그 전에는 404", r.status_code == 404, f"status={r.status_code}")

        r = client.get(f"{API}/sessions/{session_id}/segmentation", headers=H)
        check("세션 조회는 200 (null로 내려감)", r.status_code == 200, f"status={r.status_code}")
        check("reference=null", r.json()["reference"] is None)
        check("비교 부위 부족 판정", r.json()["comparable"]["sufficient"] is False)

        # ── 워커가 만들었을 행을 직접 삽입 ────────────────────────────────
        insert_segmentation(UUID(user_id), UUID(session_id), ref_photo, REFERENCE_PARTS)
        insert_segmentation(UUID(user_id), UUID(session_id), user_photo, USER_PARTS)

        print("\nF06 팔레트 조립")
        r = client.get(f"{API}/photos/{ref_photo['photo_id']}/segmentation", headers=H)
        check("조회 → 200", r.status_code == 200, r.text[:160] if r.status_code != 200 else "")
        if r.status_code != 200:
            return 1
        seg = r.json()

        check("맵 signed URL 발급", seg["map_url"].startswith("http"))
        check("원본 signed URL 함께 발급", seg["photo_url"].startswith("http"))
        check("맵 크기 반환", seg["map_width"] == 120 and seg["map_height"] == 200)
        check("모델 정보 반환", seg["model"]["version"] == "sapiens2-seg-5b")

        palette = {p["class_name"]: p for p in seg["palette"]}
        check("팔레트에 4개 부위", len(seg["palette"]) == 4, f"got={len(seg['palette'])}")
        check(
            "비교 대상엔 색이 있다",
            palette["Torso"]["color_hex"] == "#4C6EF5",
            palette["Torso"]["color_hex"],
        )
        check(
            "옷은 색이 없다 (칠하지 않음)",
            palette["Upper_Clothing"]["color_hex"] is None,
        )
        check("한글 이름 포함", palette["Torso"]["name_ko"] == "몸통")
        check(
            "label_value가 공식 매핑과 일치",
            palette["Torso"]["label_value"] == label_value_of("Torso"),
            f"Torso={palette['Torso']['label_value']}",
        )
        check(
            "display_order로 정렬됨",
            [p["class_name"] for p in seg["palette"]][:2] == ["Torso", "Left_Upper_Arm"],
            str([p["class_name"] for p in seg["palette"]]),
        )
        check("bbox는 맵 좌표계", palette["Torso"]["bbox"]["w"] == 50)

        print("\nF06 비교 부위 교집합")
        r = client.get(f"{API}/sessions/{session_id}/segmentation", headers=H)
        check("조회 → 200", r.status_code == 200)
        body = r.json()
        comp = body["comparable"]

        check("양쪽 다 내려옴", body["reference"] is not None and body["user"] is not None)
        check(
            "교집합은 몸통·왼팔 2개",
            comp["class_names"] == ["Left_Upper_Arm", "Torso"],
            str(comp["class_names"]),
        )
        check("3개 미만이라 sufficient=false", comp["sufficient"] is False)
        check("min_required 노출", comp["min_required"] == settings.min_comparable_parts)
        check(
            "레퍼런스에만 있는 부위",
            comp["reference_only"] == ["Right_Upper_Arm"],
            str(comp["reference_only"]),
        )
        check("사용자에만 있는 부위 없음", comp["user_only"] == [])

        excluded = {e["class_name"]: e for e in comp["excluded"]}
        check(
            "한쪽에서만 빠진 부위가 excluded에 있다",
            "Right_Upper_Arm" in excluded,
            str(list(excluded)),
        )
        if "Right_Upper_Arm" in excluded:
            e = excluded["Right_Upper_Arm"]
            check("어느 쪽 문제인지 표시", e["side"] == "USER", e["side"])
            check("사유 표시", e["reason"] == "TOO_SMALL", str(e["reason"]))
            check(
                "사용자에게 보여줄 문구 포함",
                "오른팔 상완" in e["message"],
                e["message"],
            )
        check(
            "비교 대상 아닌 옷은 excluded에 없다",
            "Upper_Clothing" not in excluded,
        )

        print("\nF14 signed URL 배치")
        seg_row = db.get_segmentation(UUID(str(ref_photo["photo_id"])))
        good = {"bucket": settings.bucket_segmentations, "path": seg_row["map_path"]}

        r = client.post(f"{API}/storage/signed-urls", headers=H, json={"items": [good]})
        check("정상 발급 → 200", r.status_code == 200, r.text[:160] if r.status_code != 200 else "")
        if r.status_code == 200:
            check("URL 반환", r.json()["items"][0]["url"].startswith("http"))

        r = client.post(
            f"{API}/storage/signed-urls",
            headers=H,
            json={"items": [{**good, "path": f"{user_id}/없는파일.png"}]},
        )
        check("DB에 없는 경로 → 400", r.status_code == 400, f"status={r.status_code}")

        r = client.post(
            f"{API}/storage/signed-urls",
            headers=H,
            json={"items": [{**good, "path": "00000000-0000-0000-0000-000000000000/map.png"}]},
        )
        check("남의 경로 → 400", r.status_code == 400, f"status={r.status_code}")

        r = client.post(
            f"{API}/storage/signed-urls",
            headers=H,
            json={"items": [{"bucket": settings.bucket_inbody_temp, "path": f"{user_id}/x.jpg"}]},
        )
        check("서버 내부용 버킷 → 400", r.status_code == 400, f"status={r.status_code}")

        r = client.post(f"{API}/storage/signed-urls", headers=H, json={"items": []})
        check("빈 목록 → 400", r.status_code == 400)

        print("\nF15 데이터 삭제")
        before = {b: len(storage.list_prefix(b, user_id)) for b in storage.USER_BUCKETS}
        check("삭제 전 파일 있음", sum(before.values()) > 0, str(before))

        r = client.delete(f"{API}/users/me", headers=H)
        check("DELETE /users/me → 204", r.status_code == 204, f"status={r.status_code}")

        after = {b: len(storage.list_prefix(b, user_id)) for b in storage.USER_BUCKETS}
        check("Storage 전부 비었음", sum(after.values()) == 0, str(after))
        check("유저 행 삭제됨", db.get_user(UUID(user_id)) is None)
        check(
            "세션도 CASCADE로 삭제됨",
            db.get_session(UUID(session_id)) is None,
        )
        check("삭제 후 요청은 404", client.get(f"{API}/users/me", headers=H).status_code == 404)

    finally:
        # DELETE 가 실패했을 경우를 대비한 정리
        if db.get_user(UUID(user_id)) is not None:
            storage.delete_user_files(UUID(user_id))
            db.delete_user(UUID(user_id))
            print(f"\n  (정리) 유저 삭제: {user_id}")

    print("\n" + "=" * 68)
    print(f"통과 {len(passed)} / 실패 {len(failed)}")
    for f in failed:
        print(f"  [X] {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
