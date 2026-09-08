"""팟 업로드 관문 + API pod 모드 라우트 검증 — **네트워크·GPU 없이** 돈다.

    python scripts/verify_pod_upload.py

Supabase·OpenAI·세그를 전부 가짜로 바꾸고, 관문의 순서와 응답 코드만 본다:

  팟 POST /upload
    토큰 없음/위조/재사용 → 401 · 크기 초과 → 413 · 사진 행 없음 → 409
    스크리닝 반려 → 422 · 스크리닝 불가 → 503 · 대기열 초과 → 503 · 정상 → 202(crop_box 포함)
    IP 속도 제한 → 429
  API (PHOTO_PIPELINE=pod)
    기준/사용자 사진: 파일 없이 랜드마크만 → 201, 파일을 보내면 → 409 PHOTO_PIPELINE_POD
    자세 미달 → 422 · POST /analysis → 409 · upload-token: 행 없음 409 / 정상 200(검증 통과)
    조회 응답의 signed_url 은 null, crop_box 는 행 값 그대로 · 사진 버킷 signed URL → 400

⚠️ 여기서 통과해도 실제 세그·GPT·DB 는 안 본 것이다. 그건 smoke_pod_pipeline.py.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ⚠️ settings 는 import 시점에 굳는다 — 앱을 import 하기 **전에** 환경을 정한다.
os.environ["PHOTO_PIPELINE"] = "pod"
os.environ["POD_UPLOAD_SECRET"] = "verify-secret"
os.environ["USE_MOCK"] = "true"
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "dummy")
os.environ["POD_UPLOAD_RATE_LIMIT"] = "50"
os.environ["POD_QUEUE_MAX"] = "2"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.main as api_main  # noqa: E402
import app.pod.pipeline as pipeline  # noqa: E402
import app.pod.server as pod_server  # noqa: E402
from app.schemas.enums import JobStatus  # noqa: E402
from app.services import db, diagnosis_repo, photo_screening, rate_limit, storage  # noqa: E402
from app.services import upload_token  # noqa: E402
from app.worker import queue  # noqa: E402

FAILS: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    print(("[O] " if cond else "[X] ") + label + (f"  — {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def jpeg(w: int = 600, h: int = 800, color=(120, 100, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def landmarks_json() -> str:
    return json.dumps(
        [{"index": i, "x": 0.5, "y": 0.2 + i * 0.02, "z": 0.0, "visibility": 0.95} for i in range(33)]
    )


# --------------------------------------------------------------------------- #
# 가짜 DB
# --------------------------------------------------------------------------- #

USER_ID = str(uuid4())
SESSION_ID = str(uuid4())
SESSION = {"session_id": SESSION_ID, "user_id": USER_ID, "status": "ACTIVE"}
PHOTOS: dict[str, dict] = {}


def fake_get_session(session_id):
    return dict(SESSION) if str(session_id) == SESSION_ID else None


def fake_get_photo(session_id, kind):
    return PHOTOS.get(str(kind)) if str(session_id) == SESSION_ID else None


def fake_create_photo(row):
    row = {**row, "photo_id": str(uuid4()), "created_at": "2026-09-09T00:00:00+00:00"}
    row.setdefault("width", None)
    row.setdefault("height", None)
    row.setdefault("crop_box", None)
    PHOTOS[row["kind"]] = row
    return row


def fake_delete_photo(photo_id):
    for k, v in list(PHOTOS.items()):
        if v["photo_id"] == str(photo_id):
            PHOTOS.pop(k)


def fake_update_photo(photo_id, patch):
    for v in PHOTOS.values():
        if v["photo_id"] == str(photo_id):
            v.update(patch)
            return v
    return None


def _no_db():
    raise RuntimeError("이 검증은 DB 를 쓰지 않는다")


db.get_client = _no_db
db.get_session = fake_get_session
db.get_photo = fake_get_photo
db.create_photo = fake_create_photo
db.delete_photo = fake_delete_photo
db.update_photo = fake_update_photo
db.get_segmentation = lambda photo_id: None
db.get_user = lambda user_id: {"user_id": USER_ID}
queue.cancel_open_for_photo = lambda session_id, photo_id: 0
queue.list_jobs = lambda session_id, kind=None, status=None: []
diagnosis_repo.clear_diagnoses = lambda session_id: None
storage.delete_prefix = lambda bucket, prefix: 0
storage.remove = lambda bucket, paths: None

# --------------------------------------------------------------------------- #
# API (pod 모드)
# --------------------------------------------------------------------------- #

api = TestClient(api_main.app, raise_server_exceptions=False)
H = {"X-User-Id": USER_ID}
BASE = f"/api/v1/sessions/{SESSION_ID}"

print("== API — PHOTO_PIPELINE=pod ==")
r = api.post(
    f"{BASE}/photos/reference",
    headers=H,
    data={"pose_landmarks": landmarks_json(), "pose_scale_basis": "TORSO"},
    files={"file": ("p.jpg", jpeg(), "image/jpeg")},
)
check("기준 사진에 파일을 보내면 409 PHOTO_PIPELINE_POD", r.status_code == 409 and r.json()["error"]["code"] == "PHOTO_PIPELINE_POD", str(r.status_code))

r = api.post(
    f"{BASE}/photos/reference",
    headers=H,
    data={"pose_landmarks": landmarks_json(), "pose_scale_basis": "TORSO", "is_mirrored": "true"},
)
check("기준 사진 랜드마크만 → 201", r.status_code == 201, r.text[:200])
check("  storage_path 없음 · signed_url null", PHOTOS["REFERENCE"]["storage_path"] is None and r.json()["signed_url"] is None)
check("  was_mirrored 저장", PHOTOS["REFERENCE"]["was_mirrored"] is True)

r = api.post(
    f"{BASE}/upload-token",
    headers=H,
)
check("사용자 사진 전이면 upload-token 409", r.status_code == 409 and "USER" in json.dumps(r.json()), str(r.status_code))

r = api.post(
    f"{BASE}/photos/user",
    headers=H,
    data={
        "capture_source": "UPLOAD",
        "pose_landmarks": landmarks_json(),
        "pose_similarity": "95",
        "framing_score": "0.1",
        "pose_scale_basis": "TORSO",
    },
)
check("사용자 사진 자세(거리) 미달 → 422 POSE_MISMATCH", r.status_code == 422 and r.json()["error"]["code"] == "POSE_MISMATCH", str(r.status_code))

r = api.post(
    f"{BASE}/photos/user",
    headers=H,
    data={
        "capture_source": "UPLOAD",
        "pose_landmarks": landmarks_json(),
        "pose_similarity": "95",
        "framing_score": "1.0",
        "pose_scale_basis": "TORSO",
    },
)
check("사용자 사진 랜드마크만 → 201", r.status_code == 201, r.text[:200])
check("  storage_path 없음 · job_id null", PHOTOS["USER"]["storage_path"] is None and r.json()["job_id"] is None)

r = api.post(f"{BASE}/analysis", headers=H)
check("POST /analysis → 409 PHOTO_PIPELINE_POD", r.status_code == 409 and r.json()["error"]["code"] == "PHOTO_PIPELINE_POD", str(r.status_code))

r = api.post(f"{BASE}/upload-token", headers=H)
check("upload-token 발급 200", r.status_code == 200, r.text[:200])
token = r.json().get("token", "")
payload = upload_token.verify(token, consume=False)
check("  토큰이 세션·사용자를 담고 검증됨", payload["session_id"] == SESSION_ID and payload["user_id"] == USER_ID)
check("  응답에 팟 주소가 없음", "pod" not in json.dumps(r.json()).lower() or "url" not in json.dumps(r.json()).lower())

PHOTOS["USER"]["crop_box"] = {"x": 1, "y": 2, "w": 3, "h": 4, "source_width": 9, "source_height": 9, "flipped": False}
r = api.get(f"{BASE}/photos/user", headers=H)
check("GET /photos/user → signed_url null, crop_box 그대로", r.status_code == 200 and r.json()["signed_url"] is None and r.json()["crop_box"]["w"] == 3, r.text[:200])

r = api.post("/api/v1/storage/signed-urls", headers=H, json={"items": [{"bucket": "photos", "path": f"{USER_ID}/x.jpg"}]})
check("사진 버킷 signed URL 발급 → 400", r.status_code == 400, str(r.status_code))

# --------------------------------------------------------------------------- #
# 팟 POST /upload
# --------------------------------------------------------------------------- #

print("\n== 팟 POST /upload ==")
pod = TestClient(pod_server.app, raise_server_exceptions=False)

# 진짜 pipeline.submit 을 쓴다 (메모리 등록·행 갱신·잡 생성·대기열). DB 잡 생성만 가짜로.
OPENED: list[dict] = []
FAILED_JOBS: list[str] = []


def fake_open_processing(session_id, kind, payload=None):
    row = {"job_id": f"j-{len(OPENED) + 1}", "session_id": str(session_id), "kind": str(kind), "payload": payload, "status": "PROCESSING"}
    OPENED.append(row)
    return row


queue.open_processing = fake_open_processing
queue.cancel_open_by_kind = lambda session_id, kinds, reason: 0
queue.fail = lambda job_id, error, retryable=True: FAILED_JOBS.append(str(job_id))
pipeline._worker = None  # 처리 스레드는 띄우지 않는다 — 대기열에 쌓이기만 한다

from app.services import photo_source  # noqa: E402


def files():
    return {"reference": ("r.jpg", jpeg(), "image/jpeg"), "user": ("u.jpg", jpeg(900, 700), "image/jpeg")}


def fresh_token() -> str:
    return upload_token.issue(USER_ID, SESSION_ID)["token"]


rate_limit._hits.clear()
r = pod.post("/upload", files=files())
check("토큰 없음 → 401", r.status_code == 401 and r.json()["error"]["code"] == "INVALID_UPLOAD_TOKEN", str(r.status_code))

r = pod.post("/upload", files=files(), headers={"X-Upload-Token": fresh_token()[:-2] + "zz"})
check("위조 토큰 → 401", r.status_code == 401, str(r.status_code))

r = pod.post("/upload", files=files(), headers={"X-Upload-Token": fresh_token(), "Content-Length": str(10**9)})
check("Content-Length 초과 → 413 (본문 전)", r.status_code == 413, str(r.status_code))

# 스크리닝 반려
async def screen_reject(user_image, reference_image):
    return photo_screening.ScreenResult(suitable=False, reason="LOOSE_CLOTHING", message="옷이 헐렁합니다", confidence="HIGH")


photo_screening.screen = screen_reject
r = pod.post("/upload", files=files(), headers={"X-Upload-Token": fresh_token()})
check("스크리닝 반려 → 422 UNSUITABLE_PHOTO", r.status_code == 422 and r.json()["error"]["code"] == "UNSUITABLE_PHOTO", r.text[:200])
check("  반려 시 파이프라인에 넘기지 않음 (잡 0 · 메모리 사진 0)", not OPENED and photo_source.held() == 0)


async def screen_down(user_image, reference_image):
    raise photo_screening.ScreeningUnavailable("timeout")


photo_screening.screen = screen_down
r = pod.post("/upload", files=files(), headers={"X-Upload-Token": fresh_token()})
check("스크리닝 불가 → 503 SCREENING_UNAVAILABLE", r.status_code == 503 and r.json()["error"]["code"] == "SCREENING_UNAVAILABLE", str(r.status_code))


async def screen_pass(user_image, reference_image):
    return photo_screening.ScreenResult(suitable=True)


photo_screening.screen = screen_pass
tok = fresh_token()
r = pod.post("/upload", files=files(), headers={"X-Upload-Token": tok})
check("정상 → 202", r.status_code == 202, r.text[:300])
body = r.json() if r.status_code == 202 else {}
check("  잡 3개(SEG×2, VLM_PART)가 PROCESSING 으로, source=pod", [j["kind"] for j in OPENED] == ["SEG_REFERENCE", "SEG_USER", "VLM_PART"] and all(j["payload"].get("source") == "pod" for j in OPENED), str([j["kind"] for j in OPENED]))
check("  jobs 반환", body.get("jobs", {}).get("VLM_PART") == "j-3", str(body.get("jobs")))
cb = body.get("crop_box", {}).get("USER") or {}
check("  crop_box: 900x700 가로 사진은 3:4 로 좌우가 잘림", cb.get("w") == 525 and cb.get("h") == 700 and cb.get("source_width") == 900, json.dumps(cb))
check("  기준 사진(600x800, 이미 3:4)은 크롭 없음", (body.get("crop_box", {}).get("REFERENCE") or {}).get("w") == 600)
check("  가공본 크기·crop_box 가 행에 기록됨", PHOTOS["USER"].get("width") == 525 and PHOTOS["USER"].get("height") == 700 and PHOTOS["USER"].get("crop_box", {}).get("w") == 525, str((PHOTOS["USER"].get("width"), PHOTOS["USER"].get("height"))))
check("  두 장이 메모리에 등록됨 (held_photos 2)", photo_source.held() == 2, str(photo_source.held()))
check("  메모리의 가공본이 JPEG", (photo_source.get(SESSION_ID, "USER") or b"")[:3] == b"\xff\xd8\xff")

# 얼굴 가림 — OpenAI 행 복사본만 가려지고 세그용 가공본은 그대로
from app.services import face_mask  # noqa: E402

check("  응답 face_masked 두 장 true", body.get("face_masked") == {"REFERENCE": True, "USER": True}, str(body.get("face_masked")))
seg_copy = photo_source.get(SESSION_ID, "USER") or b""
vlm_copy = photo_source.get(SESSION_ID, "USER", for_vlm=True) or b""
check("  GPT 행 복사본이 세그용과 다른 바이트", vlm_copy and vlm_copy != seg_copy)
if vlm_copy and seg_copy:
    seg_img = Image.open(io.BytesIO(seg_copy)).convert("RGB")
    vlm_img = Image.open(io.BytesIO(vlm_copy)).convert("RGB")
    lms = PHOTOS["USER"]["pose_landmarks"]
    box = face_mask.face_box(lms, seg_img.size)
    cx, cy = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2) if box else (0, 0)
    check("  얼굴 박스가 잡힘", box is not None, str(box))
    check("  복사본의 얼굴 박스 안이 회색", box is not None and vlm_img.getpixel((cx, cy)) == face_mask.FILL, str(vlm_img.getpixel((cx, cy))))
    check("  세그용 가공본의 같은 자리는 원래 색", box is not None and seg_img.getpixel((cx, cy)) != face_mask.FILL, str(seg_img.getpixel((cx, cy))))
    out_x, out_y = 5, seg_img.height - 5
    check("  박스 밖은 두 벌이 같음", vlm_img.getpixel((out_x, out_y)) == seg_img.getpixel((out_x, out_y)))
    check("  두 벌 크기 같음", vlm_img.size == seg_img.size, f"{vlm_img.size} vs {seg_img.size}")
check("  storage 경로(가린 복사본 없음)는 for_vlm 도 가공본을 줌", photo_source.get("no-such", "USER", for_vlm=True) is None)
# 얼굴 점이 전부 안 보이면 가리지 않는다 (뒷모습)
hidden = [{"index": i, "x": 0.5, "y": 0.2 + i * 0.02, "z": 0.0, "visibility": 0.1 if i < 11 else 0.95} for i in range(33)]
check("  얼굴 점 visibility 낮음 → 박스 없음", face_mask.face_box(hidden, (600, 800)) is None)
check("  랜드마크 없음 → 박스 없음", face_mask.face_box(None, (600, 800)) is None)

r = pod.post("/upload", files=files(), headers={"X-Upload-Token": tok})
check("같은 토큰 재사용 → 401", r.status_code == 401, str(r.status_code))

r = pod.post("/upload", files=files(), data={"pipeline": "quick"}, headers={"X-Upload-Token": fresh_token()})
check("pipeline=quick → 202, 세그 잡 없이 VLM_PART(mode=quick)만", r.status_code == 202 and OPENED[-1]["kind"] == "VLM_PART" and OPENED[-1]["payload"].get("mode") == "quick" and len(OPENED) == 4, str([j["kind"] for j in OPENED]))

# 대기열 상한 2 — 위 두 건이 차 있다 (처리 스레드 없음) → 세 번째는 잡을 만들지 않고 503
r = pod.post("/upload", files=files(), headers={"X-Upload-Token": fresh_token()})
check("대기열 초과 → 503 POD_BUSY", r.status_code == 503 and r.json()["error"]["code"] == "POD_BUSY", str(r.status_code))
check("  초과 시 잡을 만들지 않음", len(OPENED) == 4, str(len(OPENED)))

PHOTOS.pop("USER")
r = pod.post("/upload", files=files(), headers={"X-Upload-Token": fresh_token()})
check("사용자 사진 행 없음 → 409", r.status_code == 409, str(r.status_code))

rate_limit._hits.clear()
codes = [pod.post("/upload", headers={"X-Upload-Token": "x.y"}).status_code for _ in range(51)]
check("IP 속도 제한 → 51번째 429", codes[-1] == 429 and codes[0] == 401, str(codes[-3:]))

r = pod.get("/health")
check("GET /health 응답", r.status_code == 200 and "pipeline" in r.json(), r.text[:200])

print()
if FAILS:
    print(f"실패 {len(FAILS)}건: " + " / ".join(FAILS))
    sys.exit(1)
print("전부 통과")
