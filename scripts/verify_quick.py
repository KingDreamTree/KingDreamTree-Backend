"""퀵 파이프라인(웹캠) 검증 — 프롬프트 계약 + 실제 DB 왕복 (mock LLM).

    python scripts/verify_quick.py

━━ 무엇을 지키려는 검사인가 ━━

퀵 파이프라인은 기존 사진 파이프라인과 **한 테이블·한 조회 경로**를 공유한다
(overall_diagnosis · GET /analysis). 그래서 어긋나면 에러가 아니라 "화면이
반쯤 이상한 상태"로 나타난다:

  · 퀵인데 세그 잡이 걸리면 → GPU 없는 배포에서 PENDING 이 쌓이고 stalled 경고
  · 부위 카드를 흉내내면 → 한 프레임 인상이 F08 정밀 판정과 같은 무게로 노출
  · 점수를 만들면 → "웹캠 60점 vs 사진 55점" 같은 비교 불가능한 숫자가 공존
  · 기존 경로가 깨지면 → 최우선 원칙 위반 (이 검사의 §4 가 회귀를 잡는다)
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
from app.prompts.quick_diagnosis import SYSTEM_PROMPT as QUICK_SYSTEM  # noqa: E402
from app.prompts.quick_diagnosis import build_quick_prompt  # noqa: E402
from app.schemas.enums import JobKind  # noqa: E402
from app.services.db import get_client  # noqa: E402
from app.services.scoring import decide_direction_quick  # noqa: E402
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
    print("1. 프롬프트 계약")
    check("측정 금지 절", "«측정»하는 모델이 아닙니다" in QUICK_SYSTEM)
    check("부위별 판정 금지 절", "부위별 판정을 만들지 마세요" in QUICK_SYSTEM)
    check(
        "안 보이는 부위 언급 금지",
        "언급 자체를 하지 마세요" in QUICK_SYSTEM,
    )
    check("인바디 기준선 분리", "일반인 평균" in QUICK_SYSTEM)
    check("레퍼런스 보장 금지", "도달 보장 대상" in QUICK_SYSTEM)
    check("점수 생성 금지", "점수를 만들지 마세요" in QUICK_SYSTEM)
    # 원칙 ⑥ — 완성 예문이 없어야 한다 (자석 효과). 자리표시 «» 만 허용.
    check(
        "완성 예문 없음 (자리표시만)",
        "예)" not in QUICK_SYSTEM and "«A»" in QUICK_SYSTEM,
    )

    direction = decide_direction_quick({"mode": "BALANCE", "basis": "NO_INBODY"})
    prompt = build_quick_prompt(inbody=None, direction=direction, cut_notice=None)
    check("방향 규칙 주입", "규칙이 이미 정했습니다" in prompt and "STRENGTH_FIRST" in prompt)
    check("부위 목록이 없다 (세그 없는 모드)", "부위 범례" not in prompt)

    # 방향 규칙 — 결정론 + 퀵 전용 (LIMITED 를 내지 않는다)
    for mi, want in [
        ({"mode": "CUT", "basis": "BODY_FAT_MEASURED"}, "FAT_LOSS_FIRST"),
        ({"mode": "BALANCE", "basis": "BODY_FAT_MEASURED"}, "STRENGTH_FIRST"),
        ({"mode": "BALANCE", "basis": "NO_INBODY"}, "STRENGTH_FIRST"),
    ]:
        got = decide_direction_quick(mi)
        check(f"방향({mi['basis']}/{mi['mode']}) = {want}", got["priority"] == want)
    check(
        "인바디 없으면 감량을 단정하지 않는다",
        "판단하지 않고"
        in decide_direction_quick({"mode": "BALANCE", "basis": "NO_INBODY"})["reason"],
    )


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
    print("\n2. 실제 왕복 — 업로드(quick) → 진단 → 조회 → 루틴")
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
        check(
            "part 잡 없음 + overall 잡 발급",
            body["part_job_id"] is None and body["overall_job_id"] is not None,
        )

        # 중복 요청은 기존 잡 재사용 (요금 2배 방지 — 풀 모드와 같은 계약)
        again = client.post(f"{API}/sessions/{sid}/analysis?mode=quick", headers=H).json()
        check("재요청은 reused", again["reused"] is True)

        job = queue.find_open(UUID(sid), JobKind.VLM_OVERALL)
        check("잡 payload 에 mode=quick", (job.get("payload") or {}).get("mode") == "quick")
        result = vlm_handler._diagnose_overall(job)
        queue.complete(UUID(str(job["job_id"])), result)
        check("핸들러가 quick 으로 분기", result.get("mode") == "quick", str(result))

        p = client.get(f"{API}/sessions/{sid}/analysis/progress", headers=H).json()
        check("progress 완료 (유령 PENDING 없음)", p["completed"] is True, str(p)[:100])

        a = client.get(f"{API}/sessions/{sid}/analysis", headers=H)
        check("GET /analysis 200 (세그 없이도)", a.status_code == 200, a.text[:150])
        d = a.json()
        o = d["overall"]
        check("부위 카드 없음", d["parts"] == [])
        check(
            "점수 없음 + 사유 명시",
            o["similarity_score"] is None and "퀵" in (o["score_rationale"] or ""),
        )
        check("우선 부위 비움 (억지 부위 진단 없음)", o["priority_parts"] == [])
        check(
            "전체 형태 필드는 채워짐",
            bool(o["silhouette"]) and bool(o["key_differences"]) and bool(o["summary"]),
        )
        check(
            "방향은 규칙 값",
            (o.get("realistic_direction") or {}).get("priority") == "STRENGTH_FIRST",
        )

        # 루틴 — 퀵 진단 결과로도 기존 경로 그대로
        rr = client.post(
            f"{API}/sessions/{sid}/routines",
            headers=H,
            json={"exercise_days_per_week": 3},
        )
        check("루틴 생성 202", rr.status_code == 202, rr.text[:120])
        gen_job = queue.find_open(UUID(sid), JobKind.ROUTINE_GEN)
        gen = routine_handler._generate(gen_job)
        queue.complete(UUID(str(gen_job["job_id"])), gen)
        check("루틴 완성 (전신 기본 볼륨)", gen.get("days") == 3, str(gen)[:100])
        check("가중 없음 (priority 비었으므로)", not gen.get("boosts"), str(gen.get("boosts")))

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
