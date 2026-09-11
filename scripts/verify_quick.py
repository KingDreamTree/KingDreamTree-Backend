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

    # ⚠️ 2026-09-11 전면 개편 — 부위 카드 규칙은 part_rules 한 벌을 두 경로가 **통째로**
    #    이어 붙인다. 그래서 «두 경로에 같은 문구가 있나» 를 일곱 개씩 대조하던 동등성
    #    검사를 지우고, 공통 블록이 양쪽에 그대로 들어갔는지를 본다 — 한쪽만 고칠
    #    방법 자체가 없어졌으므로 이게 동등성의 전부다.
    from app.prompts.part_diagnosis import SYSTEM_PROMPT as PART_SEG_SYSTEM
    from app.prompts.part_rules import PART_OUTPUT, PART_RULES

    for label, system in (("라이브", PART_CMP_SYSTEM), ("사진", PART_SEG_SYSTEM)):
        check(f"{label}: 공통 규칙이 통째로", PART_RULES in system)
        check(f"{label}: 공통 출력 형식이 통째로", PART_OUTPUT in system)

    # 공통 블록에 반드시 있어야 하는 것 — 코드로 막을 수 없고 실측 사고가 있었던 규칙
    # ⚠️ 2026-09-12 압축 — 규칙이 길수록 모델이 덜 지킨다. «왜» 는 part_rules 주석에만 둔다.
    check("공통 규칙이 짧다 (2,000자 이하)", len(PART_RULES) <= 2000)
    for label, needle in (
        ("사진으로 재지 않는다", "치수·근육량·체지방률·골격을 사진으로 재지 마세요"),
        ("인바디 기준선 분리", "일반인 평균과의 비교"),
        ("보이는 부위 등급은 이미지가", "gap_level 은 언제나 이미지가 정합니다"),
        ("옷이 덮는 범위 (반팔=전완 드러남)", "반팔은 전완을"),
        ("형태를 읽을 수 있는가", "형태를 읽을 수 있는지"),
        ("두 장 모두 읽혀야 비교", "두 사진 모두에서 읽혀야"),
        ("프레임 밖 ≠ 차이 없음", "프레임 밖 부위는 «차이 없음»이 아니라 못 본 것"),
        ("못 본 부위 differences 빈 배열", "반드시 빈 배열"),
        ("가려진 부위는 인바디로", "인바디가 있으면 그 부위의 인바디 수치로 매기고"),
        ("판정 대신 형태 묘사", "그 부위의 선과 면이 실제로"),
        ("목표와 비교 필수", "«목표»를 문장에 넣어"),
        ("차이가 크면 두 문장", "두 문장: 지금 모습 / 목표 사진의 같은 자리와 다른 점"),
        ("배정된 문장 방식", "«문장 방식» 틀로 시작"),
        ("옷이 아니라 몸", "비교할 것은 옷이 아니라 몸"),
        ("정면이라 뒤쪽 금지", "정면 사진이라 뒤쪽"),
        ("운동 방향 금지", "부위 카드에는 운동 방향을 쓰지 않습니다"),
        ("인용 부위만 수치", "[인용] 표시가 붙은 부위"),
        ("어투 — 진단체", "단정을 피한 전문가 어투"),
        ("좌우: 주어는 그 카드", "문장의 주어는 «양쪽» 이 아니라 그 카드의 부위"),
        ("좌우: 없는 차이 금지", "없는 차이를 지어내지 마세요"),
    ):
        check(label, needle in PART_RULES)

    # 폐기한 것이 되살아나지 않았는지 — 틀·예문·고정 문장은 모델이 복사한다 (part_rules 주석)
    for label, needle in (
        ("안내조 어투 블록 없음", "~하면 좋아요"),
        ("판단 불가 고정 문장 없음", "충분히 보이지 않아서"),
        ("«» 골격 없음", "«부위»"),
        ("상황별 틀 없음", "① 차이가 있을 때"),
        ("«좌우 같은 문장» 지시 없음", "같은 문장"),
    ):
        check(label, needle not in PART_CMP_SYSTEM and needle not in PART_SEG_SYSTEM)

    # ⚠️ 처방 금지는 **프롬프트에서 코드로 옮겼다** (2026-09-10). 산문으로 두 곳에서
    #    금지해도 계속 나왔기 때문이다 — vlm._strip_prescription / _strip_exercise_names 가
    #    해당 문장을 통째로 걷어낸다. 그래서 검사도 그 코드가 살아 있는지를 본다.
    from app.services.vlm import _strip_exercise_names, _strip_prescription

    check("처방 문장 제거기 동작", _strip_prescription("A 입니다. 스쿼트를 하세요") == "A 입니다.")
    check(
        "종목 이름 제거기 동작", _strip_exercise_names("A 입니다. 덤벨 컬이 좋아요") == "A 입니다."
    )

    parts = [p for p in list_body_parts() if p.get("is_comparable")]
    prompt = build_part_comparison_prompt(parts=parts, inbody=None)
    check("부위 목록이 이름으로 주어짐", "`Torso`" in prompt and "`Left_Upper_Arm`" in prompt)
    check("해부학적 위치 설명 포함", "인물 자신의 왼쪽" in prompt)
    check("부위마다 «볼 것» 지점", "볼 것:" in prompt)
    # ⚠️ 다양성은 코드가 책임진다 (part_rules.assign_frames) — 카드마다 다른 시작 틀이 붙어야 한다
    frames = [ln for ln in prompt.splitlines() if "문장 방식:" in ln]
    check("부위마다 문장 방식 배정", len(frames) == len(parts))
    check("시작 틀이 부위끼리 겹치지 않음", len({f.split("«")[1] for f in frames}) == len(frames))
    check("전 부위 응답 강제", f"위 {len(parts)}개 부위를 전부 담아" in prompt)
    check("색 범례 없음 (오버레이 미사용)", "부위를 색으로 칠한 그림은 없습니다" in PART_CMP_SYSTEM)


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
