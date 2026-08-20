"""퀵 파이프라인(웹캠) 검증 — 프롬프트 계약 + 실제 DB 왕복 (mock LLM).

    python scripts/verify_quick.py

━━ 무엇을 지키려는 검사인가 ━━

퀵 파이프라인은 세그멘테이션(Sapiens2)을 쓰지 않을 뿐, **사진 파이프라인과
같은 결과 구조**를 만들어야 한다 (2026-08-20 개정). 부위 카드·유사도 점수·
우선 부위가 전부 나와야 하고, 저장 테이블·조회 경로·루틴 소비도 같다.

그래서 어긋나면 에러가 아니라 "화면이 반쯤 이상한 상태"로 나타난다:

  · 퀵인데 세그 잡이 걸리면 → GPU 없는 배포에서 PENDING 이 쌓이고 stalled 경고
  · 부위 카드가 안 나오면 → 웹캠 사용자만 반쪽 화면 (개정 전의 실제 상태)
  · 판단 불가 부위에 등급이 붙으면 → 옷에 가려 못 본 부위가 점수에 들어간다
  · 기존 경로가 깨지면 → 최우선 원칙 위반 (이 검사의 §3 이 회귀를 잡는다)

━━ 개정 전(~2026-08-19)과 무엇이 달라졌나 ━━

종전 퀵은 VLM_OVERALL 하나만 걸어 **전체 형태만** 비교했고, 부위 카드·점수·
우선 부위가 전부 없었다. 이 검사도 "부위 카드 없음"·"점수 없음"을 **계약으로
고정**하고 있었다. 세그 없이도 부위별 비교가 가능해지면서(prompts/
part_comparison.py) 그 계약이 뒤집혔고, 검사도 같이 뒤집는다.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings  # noqa: E402

settings.use_mock = True  # LLM·스크리닝 없이 전 구간

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.prompts.part_comparison import SYSTEM_PROMPT as PART_CMP_SYSTEM  # noqa: E402
from app.prompts.part_comparison import build_part_comparison_prompt  # noqa: E402
from app.schemas.enums import JobKind  # noqa: E402
from app.services.db import get_client, list_body_parts  # noqa: E402
from app.worker import queue  # noqa: E402
from app.worker.handlers import routine as routine_handler  # noqa: E402
from app.worker.handlers import vlm as vlm_handler  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failures: list[str] = []
API = "/api/v1"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(label)


def contract_prompt() -> None:
    print("1. 프롬프트 계약 — 세그 없는 부위별 비교")

    # 공유 규칙 (body_rules) 이 실제로 실려 나가는가 — 한쪽만 고쳐지는 사고 방지
    check("측정 금지 절", "«측정»하는 모델이 아닙니다" in PART_CMP_SYSTEM)
    check("인바디 기준선 분리", "일반인 평균" in PART_CMP_SYSTEM)
    check("레퍼런스 보장 금지", "도달 보장 대상" in PART_CMP_SYSTEM)
    check("용어 대체표 (레퍼런스→목표 체형)", "| 레퍼런스 | 목표 체형 |" in PART_CMP_SYSTEM)
    check("유형 분류 금지 (중간 체형 등)", "등급 딱지" in PART_CMP_SYSTEM)

    # 이 프롬프트의 존재 이유 — 옷/각도로 못 보는 부위를 억지로 판단하지 않는 것
    check("0단계 = 관찰과 판단을 분리", "관찰만** 하세요" in PART_CMP_SYSTEM)
    check("1단계 = 볼 수 있는가 판단", "«볼 수 있는가»를 판단" in PART_CMP_SYSTEM)
    check("옷이 덮는 범위 표 (반팔=팔뚝 드러남)", "덮지 못하는 곳" in PART_CMP_SYSTEM)
    check("옷을 비교하지 말라는 명시 규칙", "옷을 비교하지 마세요" in PART_CMP_SYSTEM)
    check("판단 불가 → gap_level null", "gap_level      : null" in PART_CMP_SYSTEM)
    check("한쪽만 보여도 비교 불가", "한쪽 사진에서만 보여도" in PART_CMP_SYSTEM)
    check("모순 금지 (못 봤는데 관찰 적기)", "모순" in PART_CMP_SYSTEM)

    # 사용자 요구 — 현재 설명이 아니라 «레퍼런스 대비 차이» 중심
    check("판단 순서 명시 (현재→목표→차이)", "① 사용자 사진에서" in PART_CMP_SYSTEM)
    check("차이 중심 (현재만 설명 금지)", "레퍼런스 대비 차이**가 본체" in PART_CMP_SYSTEM)
    check("옷 위에서도 읽는 것 열거", "외곽선 굴곡" in PART_CMP_SYSTEM)
    check("좌우 차이는 실제일 때만", "없는 차이를 지어내지 마세요" in PART_CMP_SYSTEM)

    # 부위 카드에 처방을 넣지 않는다는 기존 불변 (코드로도 막지만 프롬프트에도 있어야)
    check("부위 카드에 운동 처방 금지", "운동 처방이 아닙니다" in PART_CMP_SYSTEM)

    parts = [p for p in list_body_parts() if p.get("is_comparable")]
    prompt = build_part_comparison_prompt(parts=parts, inbody=None)
    check("부위 목록이 이름으로 주어짐", "`Torso`" in prompt and "`Left_Upper_Arm`" in prompt)
    check("해부학적 위치 설명 포함", "인물 자신의 왼쪽" in prompt)
    check("전 부위 응답 강제", "전부** 출력 배열에" in prompt)
    check("색 범례 없음 (오버레이 미사용)", "색으로 칠해진 그림은 없습니다" in prompt)


def _upload(client: TestClient, H: dict, sid: str, path: str, extra: dict) -> dict:
    jpeg = (PROJECT_ROOT / "tests/fixtures/sample-photo.jpg").read_bytes()
    lm = json.dumps(
        [{"index": i, "x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.95} for i in range(33)]
    )
    r = client.post(
        f"{API}/sessions/{sid}/photos/{path}",
        headers=H,
        data={"pose_landmarks": lm, "pose_scale_basis": "TORSO", "pipeline": "quick", **extra},
        files={"file": ("p.jpg", io.BytesIO(jpeg), "image/jpeg")},
    )
    assert r.status_code == 201, r.text[:200]
    return r.json()


def roundtrip() -> None:
    print("\n2. 실제 왕복 — 업로드(quick) → 부위 진단 → 종합 → 조회 → 루틴")
    client = TestClient(app)
    db = get_client()
    user = db.table("users").insert({}).execute().data[0]
    uid = user["user_id"]
    H = {"X-User-Id": uid}

    try:
        sid = client.post(f"{API}/sessions", headers=H).json()["session_id"]

        ref = _upload(client, H, sid, "reference", {})
        usr = _upload(
            client,
            H,
            sid,
            "user",
            {
                "capture_source": "CAPTURE",
                "pose_similarity": "95",
                "framing_score": "1.0",
                "facing_delta": "0.0",
            },
        )
        check("quick 업로드는 job_id 가 null", ref["job_id"] is None and usr["job_id"] is None)

        seg_jobs = (
            db.table("job")
            .select("kind")
            .eq("session_id", sid)
            .in_("kind", ["SEG_REFERENCE", "SEG_USER"])
            .execute()
            .data
        )
        check("세그 잡이 하나도 안 걸림 (Sapiens2 미사용)", not seg_jobs, str(seg_jobs))

        r = client.post(f"{API}/sessions/{sid}/analysis?mode=quick", headers=H)
        check("퀵 진단 202", r.status_code == 202, r.text[:120])
        body = r.json()
        check("part 잡 발급 (부위별 진단을 건너뛰지 않는다)", body["part_job_id"] is not None)

        # 중복 요청은 기존 잡 재사용 (요금 2배 방지 — 풀 모드와 같은 계약)
        again = client.post(f"{API}/sessions/{sid}/analysis?mode=quick", headers=H).json()
        check("재요청은 reused", again["reused"] is True)

        # ── 부위별 진단 (세그 없이) ────────────────────────────────────────
        job = queue.find_open(UUID(sid), JobKind.VLM_PART)
        check("잡 payload 에 mode=quick", (job.get("payload") or {}).get("mode") == "quick")
        result = vlm_handler._diagnose_parts(job)
        queue.complete(UUID(str(job["job_id"])), result)
        check("핸들러가 quick 으로 분기", result.get("mode") == "quick", str(result)[:120])
        check("부위 행이 실제로 저장됨", result.get("part_count", 0) > 0, str(result)[:120])

        # ── 종합 (사진 경로와 **같은 핸들러**) ─────────────────────────────
        ov_job = queue.find_open(UUID(sid), JobKind.VLM_OVERALL)
        check("부위 진단 후 종합 잡 등록", ov_job is not None)
        ov = vlm_handler._diagnose_overall(ov_job)
        queue.complete(UUID(str(ov_job["job_id"])), ov)
        check("종합도 사진 2장을 봤다", ov.get("photos") == "2", str(ov)[:120])

        p = client.get(f"{API}/sessions/{sid}/analysis/progress", headers=H).json()
        check("progress 완료 (유령 PENDING 없음)", p["completed"] is True, str(p)[:100])

        a = client.get(f"{API}/sessions/{sid}/analysis", headers=H)
        check("GET /analysis 200 (세그 없이도)", a.status_code == 200, a.text[:150])
        d = a.json()
        o = d["overall"]

        # ── 핵심: 사진 경로와 같은 UX 가 나오는가 ──────────────────────────
        check("부위 카드가 나온다 (세그 없이)", len(d["parts"]) > 0, f"{len(d['parts'])}건")
        check(
            "부위 카드에 등급·확신도가 있다",
            all("gap_level" in c and "confidence" in c for c in d["parts"]),
        )
        check("유사도 점수 계산됨", isinstance(o["similarity_score"], int), str(o["similarity_score"]))
        check("점수 출처는 규칙", o["score_source"] == "RULE")
        check("우선 부위 산출됨", len(o["priority_parts"]) > 0, str(o["priority_parts"]))
        check(
            "전체 형태 필드도 채워짐",
            bool(o["silhouette"]) and bool(o["key_differences"]) and bool(o["summary"]),
        )
        check("방향은 규칙 값", bool((o.get("realistic_direction") or {}).get("priority")))

        # 세그가 없으므로 segment_id 는 비어 있어야 한다 (FK 는 nullable)
        rows = db.table("part_diagnosis").select("*").eq("session_id", sid).execute().data
        check(
            "segment_id 없이 저장됨 (세그 미사용 증거)",
            all(r["reference_segment_id"] is None and r["user_segment_id"] is None for r in rows),
        )

        # 루틴 — 부위 진단이 생겼으므로 가중까지 기존 경로 그대로
        rr = client.post(
            f"{API}/sessions/{sid}/routines",
            headers=H,
            json={"exercise_days_per_week": 3},
        )
        check("루틴 생성 202", rr.status_code == 202, rr.text[:120])
        gen_job = queue.find_open(UUID(sid), JobKind.ROUTINE_GEN)
        gen = routine_handler._generate(gen_job)
        queue.complete(UUID(str(gen_job["job_id"])), gen)
        check("루틴 완성", gen.get("days") == 3, str(gen)[:100])

        active = client.get(f"{API}/sessions/{sid}/routines/active", headers=H).json()
        check("활성 루틴 조회", active["status"] == "DONE" and len(active["days"]) == 3)

    finally:
        db.table("analysis_session").delete().eq("user_id", uid).execute()
        db.table("users").delete().eq("user_id", uid).execute()
        print("  정리 완료")


def full_pipeline_untouched() -> None:
    print("\n3. 기존 사진 파이프라인 불변 (최우선 원칙)")
    client = TestClient(app)
    db = get_client()
    user = db.table("users").insert({}).execute().data[0]
    uid = user["user_id"]
    H = {"X-User-Id": uid}
    try:
        sid = client.post(f"{API}/sessions", headers=H).json()["session_id"]
        jpeg = (PROJECT_ROOT / "tests/fixtures/sample-photo.jpg").read_bytes()
        lm = json.dumps(
            [{"index": i, "x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.95} for i in range(33)]
        )
        # pipeline 을 **주지 않는다** — 기본값이 full 이어야 한다
        r = client.post(
            f"{API}/sessions/{sid}/photos/reference",
            headers=H,
            data={"pose_landmarks": lm, "pose_scale_basis": "TORSO"},
            files={"file": ("p.jpg", io.BytesIO(jpeg), "image/jpeg")},
        )
        check("기본 업로드 201", r.status_code == 201, r.text[:120])
        check("기본값은 세그 잡 등록 (job_id 반환)", r.json()["job_id"] is not None)
        seg = (
            (db.table("job").select("kind").eq("session_id", sid).eq("kind", "SEG_REFERENCE"))
            .execute()
            .data
        )
        check("SEG_REFERENCE 잡 존재", len(seg) == 1)
    finally:
        db.table("analysis_session").delete().eq("user_id", uid).execute()
        db.table("users").delete().eq("user_id", uid).execute()
        print("  정리 완료")


def main() -> int:
    print("퀵 파이프라인 검증 (mock)\n")
    contract_prompt()
    roundtrip()
    full_pipeline_untouched()

    print()
    if _failures:
        print(f"{FAIL} 실패 {len(_failures)}건: {', '.join(_failures)}")
        return 1
    print(f"{PASS} 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
