"""B 개발용 더미 픽스처 — A의 세그멘테이션 없이 VLM·루틴 전체를 테스트한다.

사용법:
    python scripts/seed_test_data.py           # 생성
    python scripts/seed_test_data.py --clean   # 삭제
    python scripts/seed_test_data.py --check   # 상태 확인

전제 조건:
    1) seed_body_parts.py 가 먼저 적용돼 있어야 한다.
    2) Supabase Storage 버킷 4개가 있어야 한다.
       (photos / segmentations / body-parts / inbody-temp)

⚠️ label_map 의 픽셀 인덱스(1~9)는 임시값이다.
   A가 RunPod 실측값을 공유하면 COMPARABLE_PARTS 의 pixel_value 컬럼만 교체한다.
"""

import io
import os
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

# 이 UUID 로 test user 를 식별한다. --clean 시 이 user 만 지운다.
TEST_USER_ID = uuid.UUID("00000000-dead-beef-cafe-000000000001")

MAP_W, MAP_H = 400, 600  # seg_map PNG 해상도
PHOTO_W, PHOTO_H = 400, 600  # 더미 사진 해상도

# (class_name, pixel_value, bbox_x, bbox_y, bbox_w, bbox_h)
#
# pixel_value 는 Sapiens2 alpha 순서의 **실측 확정값**이다 (PR #7, 2026-08-13).
# alpha = goliath 28개의 index 2 자리에 Eyeglasses 가 끼어들어 그 이후가 +1 밀린 것.
#   → app/services/sapiens_labels.py 의 VERIFIED_ORDER 가 유일한 출처
#
# ⚠️ 여기가 라벨 값을 하드코딩하는 **유일한 곳**이다. 런타임 코드는 절대 이러지
#    않는다 — segmentation.label_map 을 DB 에서 읽는다. 더미 픽스처는 DB 에 넣을
#    label_map 을 스스로 만들어야 해서 어쩔 수 없이 값을 안다.
#
# ⚠️ 좌/우가 화면 좌표와 반대인 것이 정상이다. 정면 사진에서 피사체의 왼쪽은
#    화면 오른쪽(x 가 큼)에 온다. bbox 도 그 규칙에 맞춰 배치했다.
COMPARABLE_PARTS = [
    ("Torso", 22, 160, 150, 80, 200),
    ("Left_Upper_Arm", 11, 245, 155, 60, 100),
    ("Left_Lower_Arm", 7, 265, 260, 50, 90),
    ("Right_Upper_Arm", 20, 95, 155, 60, 100),
    ("Right_Lower_Arm", 16, 85, 260, 50, 90),
    ("Left_Upper_Leg", 12, 200, 355, 55, 120),
    ("Left_Lower_Leg", 8, 210, 480, 50, 100),
    ("Right_Upper_Leg", 21, 145, 355, 55, 120),
    ("Right_Lower_Leg", 17, 140, 480, 50, 100),
]

INBODY_SEGMENTS = [
    ("LEFT_ARM", 3.1, 0.9),
    ("RIGHT_ARM", 3.0, 0.8),
    ("TRUNK", 28.4, 5.2),
    ("LEFT_LEG", 9.2, 2.1),
    ("RIGHT_LEG", 9.0, 2.0),
]

MODEL_NAME = "sapiens2"
#: ⚠️ 실제 체크포인트명(sapiens2-seg-5b)을 쓰지 않는다. 더미 행이 실추론 결과로
#:    오인되면 안 된다 — 이 세그멘테이션은 사각형 몇 개일 뿐이다.
MODEL_VERSION = "dummy-seed-v1"


# ─────────────────────────────────────────────────────────────────────────────
# Supabase 클라이언트 (앱 패키지 import 없이 독립 실행)
# ─────────────────────────────────────────────────────────────────────────────


def _get_client():
    from supabase import create_client

    env: dict[str, str] = {}
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    url = os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("[X] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 없습니다.")
        sys.exit(1)
    return create_client(url, key)


# ─────────────────────────────────────────────────────────────────────────────
# 이미지 생성
# ─────────────────────────────────────────────────────────────────────────────


def _make_photo(bg_color: tuple[int, int, int]) -> bytes:
    """단색 JPEG. 실제 VLM 품질 테스트는 A 완성 후, 지금은 플로우 테스트용."""
    img = Image.new("RGB", (PHOTO_W, PHOTO_H), color=bg_color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_seg_map() -> tuple[bytes, dict[str, int]]:
    """8-bit 그레이스케일 PNG (픽셀값 = pixel_value) + 부위별 픽셀 수.

    ⚠️ 반드시 PNG 로 저장. JPEG 는 손실 압축으로 라벨 픽셀값이 뭉개진다.
    """
    img = Image.new("L", (MAP_W, MAP_H), 0)  # 0 = Background
    draw = ImageDraw.Draw(img)
    pixel_counts: dict[str, int] = {}
    for class_name, pv, bx, by, bw, bh in COMPARABLE_PARTS:
        draw.rectangle([bx, by, bx + bw - 1, by + bh - 1], fill=pv)
        pixel_counts[class_name] = bw * bh

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), pixel_counts


# ─────────────────────────────────────────────────────────────────────────────
# Storage 경로 (docs/db-design-v4.md §19)
# ─────────────────────────────────────────────────────────────────────────────


def _photo_path(session_id: uuid.UUID, kind: str) -> str:
    return f"{TEST_USER_ID}/{session_id}/{kind.lower()}.jpg"


def _map_path(session_id: uuid.UUID, kind: str) -> str:
    return f"{TEST_USER_ID}/{session_id}/{kind.lower()}/map.png"


def _upload(client, bucket: str, path: str, data: bytes, content_type: str) -> None:
    client.storage.from_(bucket).upload(
        path, data, {"content-type": content_type, "upsert": "true"}
    )


# ─────────────────────────────────────────────────────────────────────────────
# 재귀 Storage 삭제 (storage.py 의 delete_prefix 와 동일 로직)
# ─────────────────────────────────────────────────────────────────────────────


def _delete_prefix(client, bucket: str, prefix: str) -> int:
    paths: list[str] = []
    stack = [prefix.rstrip("/")]
    while stack:
        cur = stack.pop()
        try:
            entries = client.storage.from_(bucket).list(cur)
        except Exception:
            continue
        for e in entries or []:
            name = e.get("name")
            if not name:
                continue
            child = f"{cur}/{name}"
            if e.get("id") is None:
                stack.append(child)
            else:
                paths.append(child)
    if paths:
        client.storage.from_(bucket).remove(paths)
    return len(paths)


# ─────────────────────────────────────────────────────────────────────────────
# 생성
# ─────────────────────────────────────────────────────────────────────────────


def seed() -> None:
    client = _get_client()

    # 사전 조건: body_part seed 확인
    bp_count = len(client.table("body_part").select("class_name").execute().data)
    if bp_count < 29:
        print(f"[X] body_part {bp_count}개. seed_body_parts.py 를 먼저 실행하세요.")
        sys.exit(1)

    # ── 0. test user (이미 있으면 재사용)
    existing = (
        client.table("users").select("user_id").eq("user_id", str(TEST_USER_ID)).execute().data
    )
    if not existing:
        client.table("users").insert({"user_id": str(TEST_USER_ID)}).execute()
        print(f"[+] user 생성: {TEST_USER_ID}")
    else:
        print(f"[=] user 재사용: {TEST_USER_ID}")

    # 이미 ACTIVE 세션이 있으면 중단 (멱등 보호)
    active = (
        client.table("analysis_session")
        .select("session_id")
        .eq("user_id", str(TEST_USER_ID))
        .eq("status", "ACTIVE")
        .execute()
        .data
    )
    if active:
        print(f"[=] ACTIVE 세션 이미 있음: {active[0]['session_id']}")
        print("    다시 생성하려면 먼저 --clean 을 실행하세요.")
        return

    # ── 1. analysis_session
    session = (
        client.table("analysis_session")
        .insert(
            {
                "user_id": str(TEST_USER_ID),
                "contraindications": ["__test_seed__"],  # --check / --clean 식별 마커
            }
        )
        .execute()
        .data[0]
    )
    session_id = uuid.UUID(session["session_id"])
    print(f"[+] session: {session_id}")

    # seg_map 은 레퍼런스·유저가 같은 더미를 씀 (픽셀 구조 동일)
    seg_bytes, pixel_counts = _make_seg_map()
    person_pixel_count = sum(pixel_counts.values())
    label_map = {str(pv): cn for cn, pv, *_ in COMPARABLE_PARTS}

    for kind, photo_color in [("REFERENCE", (160, 140, 120)), ("USER", (140, 155, 165))]:
        print(f"\n  [{kind}]")

        # ── 2. photo 업로드 + 행 삽입
        p_path = _photo_path(session_id, kind)
        _upload(client, "photos", p_path, _make_photo(photo_color), "image/jpeg")
        photo_row = (
            client.table("photo")
            .insert(
                {
                    "session_id": str(session_id),
                    "kind": kind,
                    "storage_bucket": "photos",
                    "storage_path": p_path,
                    "width": PHOTO_W,
                    "height": PHOTO_H,
                }
            )
            .execute()
            .data[0]
        )
        photo_id = photo_row["photo_id"]
        print(f"    [+] photo: {photo_id}")

        # ── 3. segmentation 업로드 + 행 삽입
        m_path = _map_path(session_id, kind)
        _upload(client, "segmentations", m_path, seg_bytes, "image/png")
        seg_row = (
            client.table("segmentation")
            .insert(
                {
                    "photo_id": photo_id,
                    "storage_bucket": "segmentations",
                    "map_path": m_path,
                    "map_width": MAP_W,
                    "map_height": MAP_H,
                    "label_map": label_map,
                    "model_name": MODEL_NAME,
                    "model_version": MODEL_VERSION,
                    "person_pixel_count": person_pixel_count,
                    "person_area_ratio": round(person_pixel_count / (MAP_W * MAP_H), 4),
                    "detected_class_count": len(COMPARABLE_PARTS),
                }
            )
            .execute()
            .data[0]
        )
        seg_id = seg_row["segmentation_id"]
        print(f"    [+] segmentation: {seg_id}")

        # ── 4. body_part_segment (9개 comparable, is_valid=True)
        bps_rows = []
        for class_name, pv, bx, by, bw, bh in COMPARABLE_PARTS:
            pc = pixel_counts[class_name]
            bps_rows.append(
                {
                    "segmentation_id": seg_id,
                    "class_name": class_name,
                    "label_value": pv,
                    "pixel_count": pc,
                    "area_ratio": round(pc / person_pixel_count, 4),
                    "bbox_x": bx,
                    "bbox_y": by,
                    "bbox_w": bw,
                    "bbox_h": bh,
                    "is_truncated": False,
                    "is_valid": True,
                    "invalid_reason": None,
                    "crop_bucket": None,
                    "crop_path": None,
                }
            )
        client.table("body_part_segment").insert(bps_rows).execute()
        print(f"    [+] body_part_segment: {len(bps_rows)}개")

    # ── 5. inbody (OCR 건너뛰고 DONE 으로 직접 삽입)
    inbody_row = (
        client.table("inbody")
        .insert(
            {
                "session_id": str(session_id),
                "device_type": "InBody570",
                "age": 27,
                "gender": "MALE",
                "height": 175.0,
                "weight": 72.4,
                "bmi": 23.6,
                "body_fat_mass": 14.2,
                "body_fat_percentage": 19.6,
                "skeletal_muscle_mass": 33.1,
                "fat_free_mass": 58.2,
                "bmr_kcal": 1642,
                "status": "DONE",
                "validation": {"overall": {"level": "ok"}},
            }
        )
        .execute()
        .data[0]
    )
    inbody_id = inbody_row["inbody_id"]
    print(f"\n[+] inbody: {inbody_id}")

    # ── 6. inbody_segment (5개)
    client.table("inbody_segment").insert(
        [
            {"inbody_id": inbody_id, "segment": seg, "lean_mass": lm, "fat_mass": fm}
            for seg, lm, fm in INBODY_SEGMENTS
        ]
    ).execute()
    print(f"[+] inbody_segment: {len(INBODY_SEGMENTS)}개")

    print(f"""
✅ seed 완료
   X-User-Id  : {TEST_USER_ID}
   session_id : {session_id}

개발 시작:
   uvicorn app.main:app --reload
   http://localhost:8000/docs
""")


# ─────────────────────────────────────────────────────────────────────────────
# 삭제
# ─────────────────────────────────────────────────────────────────────────────


def clean() -> None:
    client = _get_client()
    prefix = str(TEST_USER_ID)

    for bucket in ("photos", "segmentations", "body-parts", "inbody-temp"):
        deleted = _delete_prefix(client, bucket, prefix)
        if deleted:
            print(f"[+] Storage {bucket}: {deleted}개 삭제")

    # users 삭제 → analysis_session, photo, segmentation, body_part_segment,
    #              inbody, inbody_segment, job 전부 CASCADE
    client.table("users").delete().eq("user_id", str(TEST_USER_ID)).execute()
    print(f"[+] user {TEST_USER_ID} 삭제 완료 (DB CASCADE)")


# ─────────────────────────────────────────────────────────────────────────────
# 상태 확인
# ─────────────────────────────────────────────────────────────────────────────


def check() -> int:
    client = _get_client()

    user = client.table("users").select("user_id").eq("user_id", str(TEST_USER_ID)).execute().data
    if not user:
        print("[X] test user 없음 — seed 를 실행하세요")
        return 1

    sessions = (
        client.table("analysis_session")
        .select("session_id,status")
        .eq("user_id", str(TEST_USER_ID))
        .execute()
        .data
    )
    print(f"[O] user: {TEST_USER_ID}  (session {len(sessions)}개)")

    for s in sessions:
        sid = s["session_id"]
        photos = client.table("photo").select("photo_id,kind").eq("session_id", sid).execute().data
        segs = (
            client.table("segmentation")
            .select("segmentation_id")
            .in_("photo_id", [p["photo_id"] for p in photos])
            .execute()
            .data
            if photos
            else []
        )
        bps = (
            client.table("body_part_segment")
            .select("segment_id")
            .in_("segmentation_id", [s["segmentation_id"] for s in segs])
            .execute()
            .data
            if segs
            else []
        )
        inbody = (
            client.table("inbody").select("inbody_id,status").eq("session_id", sid).execute().data
        )

        print(
            f"    session {sid} ({s['status']})"
            f" | photo {len(photos)}"
            f" | segmentation {len(segs)}"
            f" | body_part_segment {len(bps)}"
            f" | inbody {len(inbody)}"
        )
        expected_bps = len(COMPARABLE_PARTS) * len(photos)
        if len(bps) < expected_bps:
            print(f"    [!] body_part_segment 부족 ({len(bps)}/{expected_bps})")

    return 0


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    elif "--check" in sys.argv:
        sys.exit(check())
    else:
        seed()
