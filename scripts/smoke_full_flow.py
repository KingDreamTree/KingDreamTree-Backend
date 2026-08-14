"""전 구간 통합 스모크 — 사용자 생성부터 피드백 반영까지 한 번에 관통한다.

    python scripts/smoke_full_flow.py               # mock LLM (무료·빠름)
    python scripts/smoke_full_flow.py --live-llm    # 실제 LLM 호출까지
    python scripts/smoke_full_flow.py --no-inbody   # 인바디 없는 경로만

work-b.md §7 완료 체크리스트를 **실측으로** 채우는 것이 목적이다.
조각별 검증(verify_*.py)은 이미 있지만, 단계 사이의 계약이 어긋나는 사고는
전 구간을 관통해야만 드러난다.

━━ A 파이프라인을 흉내내는 부분 ━━

세그멘테이션(Sapiens2)은 GPU 가 필요해 여기서 돌리지 않는다. 대신 **A 가
저장하는 것과 같은 모양의 행 + 실제 맵 PNG** 를 넣는다.
⚠️ 이건 A 코드를 검증하지 않는다 — B 가 그 데이터를 제대로 소비하는지만 본다.
   실제 연동 확인은 Phase 6 에서 A 와 함께 한다.

⚠️ **진단 단계는 Storage 에서 사진·맵을 실제로 내려받는다.** 행만 넣고 파일을
   안 올리면 404 로 죽는다 — DB 와 Storage 가 따로 논다는 걸 이 스모크가
   처음 잡아냈다. 그래서 샘플 파일을 실제로 업로드한 뒤 진행한다.

⚠️ 무료 티어 공유 DB 다. 만든 것은 finally 에서 전부 지운다 (Storage 포함).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings  # noqa: E402

PASS, FAIL, WARN = "[OK]", "[X]", "[!]"
_failures: list[str] = []
_warnings: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)
    return bool(condition)


def warn(label: str, detail: str = "") -> None:
    print(f"  {WARN} {label}{(' — ' + detail) if detail else ''}")
    _warnings.append(label)


# ── A 가 만드는 세그멘테이션 데이터 흉내 ──────────────────────────────────────

#: 비교 대상 9부위. label_value 는 **샘플 맵 PNG 의 실제 값**이다.
#: ⚠️ 임의 값을 쓰면 오버레이 생성이 "그 라벨이 맵에 없다"로 조용히 실패한다.
_PARTS: list[tuple[str, int, int]] = [
    # (class_name, label_value, pixel_count)
    ("Torso", 22, 48000),
    ("Left_Upper_Arm", 11, 9000),
    ("Right_Upper_Arm", 20, 9200),
    ("Left_Lower_Arm", 7, 6000),
    ("Right_Lower_Arm", 16, 6100),
    ("Left_Upper_Leg", 12, 20000),
    ("Right_Upper_Leg", 21, 20500),
    ("Left_Lower_Leg", 8, 11000),
    ("Right_Lower_Leg", 17, 11200),
]


#: 진단 단계가 실제로 내려받는 파일들. 없으면 그 단계를 건너뛴다.
_SAMPLE_PHOTO = Path(__file__).resolve().parent.parent / "전신 사진.jpg"
_SAMPLE_MAP = Path(__file__).resolve().parent.parent.parent / "map" / "map.png"


def _seg_rows(scale: float = 1.0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """segmentation 행 + body_part_segment 행들. scale 로 사용자 쪽을 줄인다."""
    parts = []
    total = 0
    for i, (name, label, px) in enumerate(_PARTS):
        # 팔만 눈에 띄게 줄여 "상체 약점" 진단이 나오게 한다
        s = scale if "Arm" in name else 1.0
        count = int(px * s)
        total += count
        parts.append(
            {
                "class_name": name,
                "label_value": label,
                "pixel_count": count,
                "area_ratio": round(count / 300000, 4),
                "bbox_x": 100 + i * 10,
                "bbox_y": 100 + i * 20,
                "bbox_w": max(1, int(60 * s)),
                "bbox_h": 200,
                "is_truncated": False,
                "is_valid": True,
            }
        )
    segmentation = {
        "storage_bucket": "segmentations",
        "map_path": "smoke/fake-map.png",
        "map_width": 768,
        "map_height": 1024,
        "label_map": {str(label): name for name, label, _ in _PARTS},
        "model_name": "sapiens2",
        "model_version": "smoke-fake",
        "person_pixel_count": total,
        "person_area_ratio": round(total / 300000, 4),
        "detected_class_count": len(_PARTS),
    }
    return segmentation, parts


def main() -> int:  # noqa: C901 — 단계별 시나리오라 한 줄기로 읽는 게 낫다
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-llm", action="store_true")
    ap.add_argument("--no-inbody", action="store_true", help="인바디 없는 경로 검증")
    args = ap.parse_args()
    settings.use_mock = not args.live_llm

    from app.schemas.enums import DomainStatus, JobKind, PhotoKind
    from app.services import db, diagnosis_repo, inbody_repo, routine_repo
    from app.services.db import get_client
    from app.worker import queue
    from app.worker.handlers import routine as routine_handler
    from app.worker.handlers import vlm as vlm_handler

    client = get_client()
    mode_label = "실호출" if args.live_llm else "mock"
    print(f"전 구간 스모크 (LLM {mode_label}, 인바디 {'없음' if args.no_inbody else '있음'})\n")

    user_id = session_id = None
    try:
        # ── 1. 사용자 · 세션 ────────────────────────────────────────────────
        print("1. 사용자 · 세션")
        user = client.table("users").insert({}).execute().data[0]
        user_id = UUID(str(user["user_id"]))
        session = (
            client.table("analysis_session").insert({"user_id": str(user_id)}).execute().data[0]
        )
        session_id = UUID(str(session["session_id"]))
        check("사용자 생성", user_id is not None)
        check("세션 생성", session["status"] == "ACTIVE")

        # ── 2. 사진 + 세그멘테이션 (A 파이프라인 흉내) ──────────────────────
        print("\n2. 사진 · 세그멘테이션 (A 데이터 형태)")
        from app.services import storage

        has_files = _SAMPLE_PHOTO.exists() and _SAMPLE_MAP.exists()
        if not has_files:
            warn(
                "샘플 파일 없음 — 진단 단계를 건너뜁니다",
                f"{_SAMPLE_PHOTO.name} / {_SAMPLE_MAP.name}",
            )
        photo_bytes = _SAMPLE_PHOTO.read_bytes() if has_files else b""
        map_bytes = _SAMPLE_MAP.read_bytes() if has_files else b""

        for kind, scale in ((PhotoKind.REFERENCE, 1.0), (PhotoKind.USER, 0.72)):
            photo_path = f"{user_id}/{session_id}/{kind.lower()}.jpg"
            map_path = f"{user_id}/{session_id}/{kind.lower()}/map.png"
            if has_files:
                # ⚠️ DB 행만 넣고 파일을 안 올리면 진단이 404 로 죽는다.
                storage.upload("photos", photo_path, photo_bytes, "image/jpeg")
                storage.upload("segmentations", map_path, map_bytes, "image/png")

            photo = (
                client.table("photo")
                .insert(
                    {
                        "session_id": str(session_id),
                        "kind": str(kind),
                        "storage_bucket": "photos",
                        "storage_path": photo_path,
                        "width": 768,
                        "height": 1024,
                    }
                )
                .execute()
                .data[0]
            )
            seg, parts = _seg_rows(scale)
            seg["map_path"] = map_path
            db.replace_segmentation(UUID(str(photo["photo_id"])), seg, parts)

        ctx = diagnosis_repo.build_comparison_context(session_id)
        check("비교 대상 산출", ctx["ready"] and len(ctx["parts"]) == 9, f"{len(ctx['parts'])}부위")

        # ── 3. 인바디 ───────────────────────────────────────────────────────
        print("\n3. 인바디")
        if args.no_inbody:
            warn("인바디 건너뜀 (--no-inbody)", "이 경로는 BALANCE 가 나와야 한다")
        else:
            inbody = inbody_repo.create_inbody(session_id, "InBody570")
            inbody_id = UUID(str(inbody["inbody_id"]))
            inbody_repo.update_inbody(
                inbody_id,
                {
                    "gender": "MALE",
                    "height": 175,
                    "weight": 82.0,
                    "bmi": 26.8,
                    "body_fat_percentage": 27.5,  # ≥25 → CUT 이어야 한다
                    "body_fat_mass": 22.5,
                    "skeletal_muscle_mass": 32.0,
                    "status": str(DomainStatus.DONE),
                },
            )
            inbody_repo.replace_segments(
                inbody_id,
                [
                    {"segment": "LEFT_ARM", "lean_mass": 2.4, "fat_mass": 1.2},
                    {"segment": "RIGHT_ARM", "lean_mass": 2.5, "fat_mass": 1.2},
                    {"segment": "TRUNK", "lean_mass": 24.0, "fat_mass": 12.0},
                    {"segment": "LEFT_LEG", "lean_mass": 8.8, "fat_mass": 3.5},
                    {"segment": "RIGHT_LEG", "lean_mass": 8.9, "fat_mass": 3.5},
                ],
            )
            latest = inbody_repo.latest_done(session_id)
            check("최신 DONE 인바디 조회", latest is not None)
            check(
                "raw_ocr 없이도 프롬프트 변환", "segments" in inbody_repo.to_prompt_payload(latest)
            )

        # ── 4. 진단 (F08 → F09) ─────────────────────────────────────────────
        print("\n4. 진단")
        part_job = queue.enqueue(session_id, JobKind.VLM_PART)
        part_result = vlm_handler._diagnose_parts(part_job)
        # ⚠️ 워커 루프(run.py)가 하는 일을 여기서 대신 해준다. 안 하면 잡이
        #    PENDING 으로 남아 "중복 호출 가드" 검사가 실제와 다르게 나온다.
        queue.complete(UUID(str(part_job["job_id"])), part_result)
        check("부위 진단 완료", part_result["done"] >= 3, str(part_result.get("done")))
        check("인바디 반영 여부 기록", "inbody" in part_result, part_result.get("inbody"))

        overall_job = queue.find_open(session_id, JobKind.VLM_OVERALL)
        check("종합 잡이 부위 진단 뒤에 등록됨", overall_job is not None)
        if overall_job:
            overall_result = vlm_handler._diagnose_overall(overall_job)
            queue.complete(UUID(str(overall_job["job_id"])), overall_result)
        overall = diagnosis_repo.get_overall(session_id)
        check("종합 진단 저장", overall is not None and overall["status"] == "DONE")
        if overall:
            check(
                "유사도 점수는 규칙 산출 (RULE)",
                overall.get("score_source") == "RULE",
                f"{overall.get('similarity_score')}점 / {overall.get('score_source')}",
            )
            check(
                "우선 개선 부위 존재",
                bool(overall.get("priority_parts")),
                str(overall.get("priority_parts")),
            )

        # ── 5. 루틴 생성 (F10) ──────────────────────────────────────────────
        print("\n5. 루틴 생성")
        created = routine_repo.create_routine(session_id, days_per_week=3)
        month_routine_id = UUID(str(created["month_routine_id"]))
        gen_job = queue.enqueue(
            session_id,
            JobKind.ROUTINE_GEN,
            {"month_routine_id": str(month_routine_id), "days_per_week": 3},
        )
        gen = routine_handler._generate(gen_job)
        queue.complete(UUID(str(gen_job["job_id"])), gen)
        check("루틴 생성 성공", gen.get("days") == 3, json.dumps(gen, ensure_ascii=False)[:160])

        expected_mode = "BALANCE" if args.no_inbody else "CUT"
        check(f"모드 = {expected_mode}", gen["mode"] == expected_mode, gen["mode_basis"])

        days = routine_repo.list_days(month_routine_id)
        cardio = [e for d in days for e in d["exercises"] if e["exercise_kind"] == "CARDIO"]
        if args.no_inbody:
            check("BALANCE 면 유산소 없음", not cardio)
        else:
            check("CUT 이면 근력일마다 유산소", len(cardio) == 3, f"{len(cardio)}개")

        check("진단 가중 반영", bool(gen.get("boosts")), str(gen.get("boosts")))
        boosted = [e for d in days for e in d["exercises"] if e.get("boosted_by")]
        check("가중 근거가 행에 남음", bool(boosted), f"{len(boosted)}개 슬롯")
        # 진단이 안 된 부위도 기본 볼륨을 받아야 한다 (D10)
        groups = {e["muscle_group"] for d in days for e in d["exercises"] if e["muscle_group"]}
        check("하체도 기본 볼륨 (D10)", {"대퇴사두", "햄스트링·둔근"} & groups, str(sorted(groups)))

        active = routine_repo.get_active(session_id)
        check("DONE 후 활성화", active and str(active["month_routine_id"]) == str(month_routine_id))

        # ── 6. 오늘의 루틴 (F11) ────────────────────────────────────────────
        print("\n6. 오늘의 루틴")
        p0 = routine_repo.progress(month_routine_id, 3)
        check("시작 위치 = 1주기 Day 1", p0["cycle_no"] == 1 and p0["next_day_order"] == 1)
        today = routine_repo.get_day(month_routine_id, p0["next_day_order"])
        check("Day 상세 조회", today is not None and bool(today["exercises"]))

        # ── 7. 수행 기록 + 피드백 (F12) ─────────────────────────────────────
        print("\n7. 수행 기록 · 피드백")
        log = routine_repo.create_log(
            session_id=session_id,
            month_routine_id=month_routine_id,
            routine_day_id=UUID(str(days[0]["routine_day_id"])),
            cycle_no=1,
            feedback_text="스쿼트 할 때 무릎이 아팠어요",
        )
        patch_job = queue.enqueue(
            session_id,
            JobKind.ROUTINE_PATCH,
            {
                "month_routine_id": str(month_routine_id),
                "workout_log_id": str(log["workout_log_id"]),
            },
        )
        patch = routine_handler._patch(patch_job)
        queue.complete(UUID(str(patch_job["job_id"])), patch)
        check("피드백 해석", patch.get("changes", 0) > 0 or patch.get("skipped"), str(patch))

        revisions = routine_repo.list_revisions(session_id)
        check("변경 이력 저장", bool(revisions), f"{len(revisions)}건")
        if revisions:
            check("원본 피드백을 조인해 옴", bool(revisions[0].get("feedback_text")))

        sess = (
            client.table("analysis_session")
            .select("contraindications")
            .eq("session_id", str(session_id))
            .execute()
            .data[0]
        )
        contra = sess.get("contraindications") or []
        # 통증을 명시한 피드백이므로 금기가 반드시 잡혀야 한다 (work-b.md §6 안전 처리)
        check("통증 피드백 → 금기 등록", patch.get("contraindications_added", 0) > 0, str(patch))
        check("금기가 세션에 누적", bool(contra), str(contra))

        # 2026-08-14: 해석만 하던 것을 **실제 적용**으로 바꿨다 (F12-b 와 같은 경로).
        patched_id = UUID(str(patch["month_routine_id"]))
        if patch.get("no_change"):
            warn("적용할 루틴 변경 없음", "해석·금기만 기록됨")
        else:
            check(
                "새 FEEDBACK 버전 생성",
                patched_id != month_routine_id,
                f"v{patch.get('version')}",
            )
            new_active = routine_repo.get_active(session_id)
            check(
                "새 버전이 활성",
                bool(new_active) and str(new_active["month_routine_id"]) == str(patched_id),
            )
            check(
                "이전 버전 Day 보존",
                len(routine_repo.list_days(month_routine_id)) == 3,
            )
            check(
                "새 버전 Day 수 동일",
                len(routine_repo.list_days(patched_id)) == 3,
            )

        # ── 8. 실패 시 이전 버전 유지 ───────────────────────────────────────
        print("\n8. 생성 실패 시 이전 버전 보호")
        # ⚠️ 기준은 "처음 만든 루틴"이 아니라 **지금 활성인 버전**이다.
        #    피드백이 새 버전을 활성화하므로 month_routine_id 로 비교하면 틀린다.
        active_before = routine_repo.get_active(session_id)
        broken = routine_repo.create_routine(session_id, days_per_week=4)
        broken_id = UUID(str(broken["month_routine_id"]))
        routine_repo.update_routine(broken_id, {"status": str(DomainStatus.FAILED)})
        still = routine_repo.get_active(session_id)
        check(
            "FAILED 버전은 활성이 안 됨",
            bool(still)
            and str(still["month_routine_id"]) == str(active_before["month_routine_id"]),
            f"활성 v{still['version'] if still else '?'}",
        )
        check("수행 기록 보존", routine_repo.count_logs(month_routine_id) == 1)

        # ── 9. 중복 호출 가드 ───────────────────────────────────────────────
        print("\n9. 중복 호출 가드")
        again = queue.find_open(session_id, JobKind.VLM_PART)
        check("완료된 진단은 열린 잡이 없음", again is None)
        dup1 = queue.enqueue_once(
            session_id,
            JobKind.ROUTINE_GEN,
            {"days_per_week": 3},
            payload_match={"days_per_week": 3},
        )
        dup2 = queue.enqueue_once(
            session_id,
            JobKind.ROUTINE_GEN,
            {"days_per_week": 3},
            payload_match={"days_per_week": 3},
        )
        check("같은 일수 재요청은 기존 잡 반환", dup1[0]["job_id"] == dup2[0]["job_id"])

    finally:
        # ⚠️ Storage 를 **DB 보다 먼저** 지운다. DB 를 먼저 지우면 어느 경로를
        #    지워야 하는지 알 수 없게 된다 (users.py 규약).
        if user_id:
            try:
                from app.services import storage as _storage

                removed = _storage.delete_user_files(user_id)
                print(f"\nStorage 정리: {removed}")
            except Exception as e:  # noqa: BLE001 — 정리 실패가 결과를 가리면 안 된다
                print(f"\n[!] Storage 정리 실패: {type(e).__name__}")
        if session_id:
            client.table("analysis_session").delete().eq("session_id", str(session_id)).execute()
        if user_id:
            client.table("users").delete().eq("user_id", str(user_id)).execute()
        print("DB 정리 완료 (세션·사용자 삭제 — CASCADE)")

    print()
    for w in _warnings:
        print(f"{WARN} {w}")
    if _failures:
        print(f"\n{FAIL} 실패 {len(_failures)}건:")
        for f in _failures:
            print(f"   - {f}")
        return 1
    print(f"\n{PASS} 전 구간 통과 (경고 {len(_warnings)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
