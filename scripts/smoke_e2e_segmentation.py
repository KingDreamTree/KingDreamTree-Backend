"""전 구간 E2E — 사진 업로드 → 실제 Sapiens2 추론 → 맵 저장 → 조회.

지금까지의 스모크는 워커가 만들었을 행을 **직접 넣어** 조회 로직만 봤다.
이 스크립트는 **진짜 추론을 거친 결과**를 확인한다.

사용법:
    # 터미널 1 — 워커 (GPU 있는 곳)
    python -m app.worker.run --kinds SEG_REFERENCE,SEG_USER

    # 터미널 2 — 이 스크립트
    python scripts/smoke_e2e_segmentation.py --image /workspace/사람사진.jpg

    # 결과를 눈으로 보려면 (맵을 색칠해 저장)
    python scripts/smoke_e2e_segmentation.py --image p.jpg --out out/e2e --keep

⚠️ **진짜 DB와 Storage에 쓴다.** 기본적으로 끝나면 만든 유저를 통째로 지운다.
   --keep 을 주면 남긴다 (Supabase 콘솔에서 직접 보고 싶을 때).

⚠️ 워커가 안 돌고 있으면 잡이 PENDING 에서 안 넘어가고 타임아웃 난다.
   그건 실패가 아니라 "워커를 안 켰다"는 뜻이다.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
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
    print(f"  [{'O' if cond else 'X'}] {label}" + (f"  — {note}" if note else ""))


def landmarks_json() -> str:
    """⚠️ 세그멘테이션 검증이 목적이라 랜드마크는 형식만 맞춘 더미다.

    포즈 판정은 프론트 몫이고 서버는 임계값만 보므로, 통과하는 값을 넣는다.
    """
    lms = [{"index": i, "x": 0.5, "y": i / 100, "z": 0.0, "visibility": 0.9} for i in range(33)]
    lms[11].update(x=0.70, y=0.30)
    lms[12].update(x=0.30, y=0.35)
    return json.dumps(lms)


def wait_for_job(job_id: str, headers: dict, timeout: int) -> dict:
    """잡이 끝날 때까지 폴링한다. 진행 상태를 한 줄로 갱신해 보여준다."""
    started = time.time()
    last = ""
    while time.time() - started < timeout:
        body = client.get(f"{API}/jobs/{job_id}", headers=headers).json()
        status = body["status"]
        if status != last:
            print(f"      {status} ({time.time() - started:.0f}s)")
            last = status
        if status in ("DONE", "FAILED"):
            return body
        time.sleep(1.5)
    return {"status": "TIMEOUT", "error": f"{timeout}초 안에 끝나지 않음"}


def inspect_map_png(raw: bytes) -> tuple[Image.Image, list[str]]:
    """⚠️ 맵 파일 포맷 규칙 — 어기면 에러 없이 값이 바뀐다.

    docs/db-design-v4.md §1.3 의 규칙을 파일 자체로 확인한다.
    """
    img = Image.open(io.BytesIO(raw))
    problems: list[str] = []

    if img.format != "PNG":
        problems.append(f"PNG이 아님 ({img.format}) — 손실 압축이 인접 라벨을 섞는다")
    if img.mode != "L":
        problems.append(f"8-bit 그레이스케일이 아님 (mode={img.mode})")
    if "transparency" in img.info or img.mode.endswith("A"):
        problems.append("알파 채널이 있음 — 브라우저가 프리멀티플라이하며 값을 바꾼다")
    if img.info.get("icc_profile"):
        problems.append("ICC 프로파일이 있음 — 브라우저 색 관리가 픽셀 값을 보정한다")

    return img, problems


def main() -> int:
    ap = argparse.ArgumentParser(description="세그멘테이션 전 구간 E2E")
    ap.add_argument("--image", required=True, help="사람이 찍힌 사진 (정면·전신 권장)")
    ap.add_argument("--timeout", type=int, default=300, help="잡 하나당 대기 상한(초)")
    ap.add_argument("--out", default=None, help="맵을 색칠해 저장할 폴더")
    ap.add_argument("--keep", action="store_true", help="끝나고 데이터를 지우지 않음")
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"[X] 사진을 찾을 수 없습니다: {image_path}")
        return 1
    raw = image_path.read_bytes()

    print("=" * 68)
    print("전 구간 E2E — 업로드 → 실제 추론 → 맵 저장 → 조회")
    print("=" * 68)
    print(f"  입력: {image_path}  ({len(raw) / 1024:.0f}KB)")

    user_id = client.post(f"{API}/users").json()["user_id"]
    H = {"X-User-Id": user_id}
    created = [user_id]

    try:
        session_id = client.post(f"{API}/sessions", headers=H).json()["session_id"]
        SP = f"{API}/sessions/{session_id}/photos"
        base = {"pose_landmarks": landmarks_json(), "pose_scale_basis": "TORSO"}

        print("\n업로드")
        r = client.post(
            f"{SP}/reference",
            headers=H,
            data=base,
            files={"file": (image_path.name, raw, "image/jpeg")},
        )
        check("레퍼런스 업로드 → 201", r.status_code == 201, r.text[:200])
        if r.status_code != 201:
            return 1
        ref = r.json()
        print(f"      photo_id={ref['photo_id']}  {ref['width']}x{ref['height']}")

        print("\n워커 대기 (안 켰으면 여기서 타임아웃)")
        job = wait_for_job(ref["job_id"], H, args.timeout)
        check("SEG_REFERENCE 완료", job["status"] == "DONE", str(job.get("error") or "")[:200])
        if job["status"] != "DONE":
            return 1

        result = job.get("result") or {}
        print(
            f"      검출 {result.get('detected')}클래스 / "
            f"비교가능 {result.get('valid_comparable')} / "
            f"추론 {result.get('inference_ms')}ms / {result.get('model_version')}"
        )
        check("추론 결과 요약이 잡에 남음", "segmentation_id" in result)
        check(
            "모델 버전이 검증한 것과 일치",
            result.get("model_version") == "sapiens2-seg-5b",
            str(result.get("model_version")),
        )

        print("\n조회 API")
        r = client.get(f"{API}/photos/{ref['photo_id']}/segmentation", headers=H)
        check("세그멘테이션 조회 → 200", r.status_code == 200, r.text[:200])
        if r.status_code != 200:
            return 1
        seg = r.json()

        print(
            f"      맵 {seg['map_width']}x{seg['map_height']} / "
            f"인물 비율 {seg['person_area_ratio'] * 100:.1f}% / "
            f"팔레트 {len(seg['palette'])}개"
        )

        check("맵 크기가 기록됨", seg["map_width"] > 0 and seg["map_height"] > 0)
        check(
            "사람이 검출됨",
            seg["person_area_ratio"] > 0.01,
            f"{seg['person_area_ratio'] * 100:.1f}%",
        )

        comparable = [p for p in seg["palette"] if p["is_comparable"]]
        valid = [p for p in comparable if p["is_valid"]]
        print("\n      비교 대상 부위:")
        for p in sorted(comparable, key=lambda x: -x["pixel_count"]):
            mark = "O" if p["is_valid"] else "X"
            reason = f"  ({p['invalid_reason']})" if p["invalid_reason"] else ""
            print(
                f"        [{mark}] {p['name_ko']:<12} {p['pixel_count']:>8,}px "
                f"{p['area_ratio'] * 100:>5.1f}%{reason}"
            )

        check("비교 대상이 하나 이상 유효", len(valid) > 0, f"{len(valid)}개")
        check("색이 채워져 있음", all(p["color_hex"] for p in comparable))
        check(
            "bbox가 맵 범위 안",
            all(
                0 <= p["bbox"]["x"]
                and p["bbox"]["x"] + p["bbox"]["w"] <= seg["map_width"]
                and 0 <= p["bbox"]["y"]
                and p["bbox"]["y"] + p["bbox"]["h"] <= seg["map_height"]
                for p in seg["palette"]
            ),
        )

        print("\n맵 파일 포맷 (어기면 에러 없이 값이 바뀐다)")
        seg_row = db.get_segmentation(UUID(ref["photo_id"]))
        map_bytes = storage.download(seg_row["storage_bucket"], seg_row["map_path"])
        img, problems = inspect_map_png(map_bytes)
        print(
            f"      {img.format} / mode={img.mode} / {img.size[0]}x{img.size[1]} / "
            f"{len(map_bytes) / 1024:.0f}KB"
        )
        check("포맷 규칙 준수", not problems, " · ".join(problems))
        check(
            "맵 크기가 DB 기록과 일치",
            img.size == (seg["map_width"], seg["map_height"]),
            f"파일 {img.size} vs DB ({seg['map_width']}, {seg['map_height']})",
        )

        from app.config import settings as _s

        check(
            "맵 긴 변이 상한 이하",
            max(img.size) <= _s.map_max_side,
            f"{max(img.size)} / 상한 {_s.map_max_side}",
        )

        values = {p["label_value"] for p in seg["palette"]}
        pixels = set(img.getdata())
        unexpected = pixels - values - {0}
        check("맵에 팔레트 밖의 값이 없음", not unexpected, f"예상 밖: {sorted(unexpected)[:10]}")

        if args.out:
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)

            overlay = render(image_path, img, seg["palette"])
            overlay.save(out_dir / "e2e_overlay.png")
            print(
                f"\n      오버레이: {out_dir / 'e2e_overlay.png'}"
                "  ← 좌우가 안 뒤집혔는지 눈으로 확인하세요"
            )

            # ⚠️ 담당 B에게 넘길 실물 샘플. 하이라이트 생성 로직을 이걸로 검증한다.
            #    맵은 **원본 그대로** 저장한다 — 다시 인코딩하면 검증 의미가 없다.
            (out_dir / "map.png").write_bytes(map_bytes)
            (out_dir / "label_map.json").write_text(
                json.dumps(seg_row["label_map"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / "segmentation.json").write_text(
                json.dumps(seg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"      B 전달용: {out_dir / 'map.png'} · label_map.json · segmentation.json")

    finally:
        if args.keep:
            print(f"\n  --keep 이므로 남겨둡니다. user_id={user_id}")
        else:
            print("\n정리")
            for uid in created:
                storage.delete_user_files(UUID(uid))
                db.delete_user(UUID(uid))
                print(f"  유저 삭제: {uid}")

    print("\n" + "=" * 68)
    print(f"통과 {len(passed)} / 실패 {len(failed)}")
    for f in failed:
        print(f"  [X] {f}")
    return 1 if failed else 0


def render(image_path: Path, label_img: Image.Image, palette: list[dict]) -> Image.Image:
    """원본 위에 부위별 색칠 + 이름. ⚠️ 좌우 반전 확인용이다."""
    from PIL import ImageDraw, ImageFont

    w, h = label_img.size
    base = Image.open(image_path).convert("RGB").resize((w, h), Image.LANCZOS)
    overlay = base.copy()

    lut = {}
    for p in palette:
        if p["color_hex"]:
            c = p["color_hex"]
            lut[p["label_value"]] = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))

    px = overlay.load()
    src = label_img.load()
    for y in range(h):
        for x in range(w):
            rgb = lut.get(src[x, y])
            if rgb:
                r, g, b = px[x, y]
                px[x, y] = (
                    int(r * 0.45 + rgb[0] * 0.55),
                    int(g * 0.45 + rgb[1] * 0.55),
                    int(b * 0.45 + rgb[2] * 0.55),
                )

    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    for p in palette:
        if not p["color_hex"] or p["pixel_count"] < 400:
            continue
        cx = p["bbox"]["x"] + p["bbox"]["w"] // 2
        cy = p["bbox"]["y"] + p["bbox"]["h"] // 2
        # ⚠️ 한글 폰트가 없는 환경이 많아 class_name(영문)으로 적는다.
        text = p["class_name"]
        box = draw.textbbox((cx, cy), text, font=font, anchor="mm")
        draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=(0, 0, 0))
        draw.text((cx, cy), text, font=font, fill=(255, 255, 255), anchor="mm")

    return overlay


if __name__ == "__main__":
    sys.exit(main())
