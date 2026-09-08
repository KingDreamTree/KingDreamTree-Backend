"""두 실행의 진단 결과를 나란히 비교한다 — 얼굴 가림(services/face_mask) 실측용.

    # 1) 실제 랜드마크 준비 (한 번) — 프론트와 같은 JS MediaPipe
    node scripts/pose_landmarks_web.mjs photos/123.jpg out/landmarks/123.json photos/456.jpg out/landmarks/456.json
    # 2) 팟을 POD_FACE_MASK=false 로 띄우고 → 스모크 --keep --ref-landmarks ... --user-landmarks ...  (원본)
    # 3) 팟을 POD_FACE_MASK=true  로 띄우고 → 같은 명령                                              (가림)
    # 4) 세션을 **파일로 먼저 저장**하고 (유저를 지우면 DB 에서 사라진다)
    python scripts/compare_diagnosis.py --dump <세션> out/diagnosis-runs/original-1.json
    python scripts/compare_diagnosis.py --dump <세션> out/diagnosis-runs/blur-1.json
    # 5) 비교 — --a/--b 는 세션 id 또는 저장한 JSON 파일
    python scripts/compare_diagnosis.py --a out/diagnosis-runs/original-1.json --b out/diagnosis-runs/blur-1.json
    # 6) 끝나면 유저를 지운다: DELETE /users/me (X-User-Id)

무엇을 보나
    · 세그 행(식별자·경로·시간 제외) — 가림은 세그에 안 닿으므로 완전히 같아야 한다
    · 부위 진단 9행: gap_level · priority · confidence 가 같은가, assessment 문장 유사도
    · 종합 진단: similarity_score · priority_parts · summary 유사도

판정 기준 (GPT 는 temperature=0 이어도 실행마다 조금씩 다르다 — 2026-09-09 실측에서
원본끼리도 상완 SLIGHT↔MODERATE·점수 69↔62 가 흔들렸다)
    같음 : 등급·우선순위·점수가 전부 같고 문장 유사도 ≥ 0.6 (문장이 없으면 등급만)
    다름 : 그 외 — 같은 조건을 두 번 돌려 흔들림 기준선을 먼저 재고, 그 이상일 때만 "가림 때문"으로 본다

읽기 전용 — DB 를 바꾸지 않는다. 서비스 키로 직접 본다.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_SEG_SKIP = {
    "segmentation_id",
    "photo_id",
    "created_at",
    "map_path",
    "storage_bucket",
    "session_id",
    "inference_ms",
}


def sim(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return difflib.SequenceMatcher(None, a, b).ratio()


def snapshot(session_id: UUID) -> dict:
    """세션의 세그·부위·종합을 한 덩어리로. 유저 삭제 뒤에도 비교할 수 있게 파일로 남긴다."""
    from app.services import db

    parts = db.rows_for_session(
        "part_diagnosis", session_id, "class_name,gap_level,priority,confidence,assessment,status"
    )
    overall = db.rows_for_session("overall_diagnosis", session_id, "*")
    photos = db.rows_for_session("photo", session_id, "photo_id,kind")
    segs: dict[str, dict] = {}
    for p in photos:
        seg = db.get_segmentation(UUID(p["photo_id"]))
        if seg:
            segs[p["kind"]] = {k: v for k, v in seg.items() if k not in _SEG_SKIP}
    return {
        "session_id": str(session_id),
        "segments": segs,
        "parts": {r["class_name"]: r for r in parts},
        "overall": overall[0] if overall else None,
    }


def load(ref: str) -> dict:
    p = Path(ref)
    if p.suffix == ".json" and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return snapshot(UUID(ref))


def compare(a: dict, b: dict, min_sim: float) -> int:
    diff = 0
    print("== 세그 (가림과 무관해야 함) ==")
    sa, sb = a.get("segments") or {}, b.get("segments") or {}
    if not sa or not sb:
        print("  (한쪽에 세그 기록 없음 — 건너뜀)")
    for kind in sorted(set(sa) & set(sb)):
        x, y = sa[kind], sb[kind]
        same = json.dumps(x, sort_keys=True, default=str) == json.dumps(
            y, sort_keys=True, default=str
        )
        print(f"  {kind}: 세그 행 {'같음' if same else '다름'} (비교 키 {len(x)}개)")
        if not same:
            diff += 1
            for k in sorted(set(x) | set(y)):
                if x.get(k) != y.get(k):
                    print(f"      {k}: {str(x.get(k))[:80]}  vs  {str(y.get(k))[:80]}")

    print("\n== 부위 진단 ==")
    pa, pb = a["parts"], b["parts"]
    for name in sorted(set(pa) | set(pb)):
        x, y = pa.get(name), pb.get(name)
        if not x or not y:
            print(f"  {name}: 한쪽에 없음")
            diff += 1
            continue
        same = all(x.get(k) == y.get(k) for k in ("gap_level", "priority", "confidence"))
        s = sim(x.get("assessment"), y.get("assessment"))
        flag = "같음" if same and (s is None or s >= min_sim) else "다름"
        if flag == "다름":
            diff += 1
        s_txt = f"{s:.2f}" if s is not None else "-"
        print(
            f"  {name:14s} {x.get('gap_level')}/{x.get('priority')}/{x.get('confidence')}"
            f"  vs  {y.get('gap_level')}/{y.get('priority')}/{y.get('confidence')}"
            f"  문장 유사도 {s_txt}  {flag}"
        )
        if flag == "다름" and x.get("assessment") and y.get("assessment"):
            print(f"      A: {x.get('assessment')}")
            print(f"      B: {y.get('assessment')}")

    print("\n== 종합 진단 ==")
    oa, ob = a.get("overall"), b.get("overall")
    if not oa or not ob:
        print("  한쪽에 종합 진단 없음")
        diff += 1
    else:
        same_score = oa.get("similarity_score") == ob.get("similarity_score")
        same_prio = json.dumps(oa.get("priority_parts"), sort_keys=True) == json.dumps(
            ob.get("priority_parts"), sort_keys=True
        )
        s_sum = sim(oa.get("summary"), ob.get("summary"))
        print(
            f"  점수 {oa.get('similarity_score')} vs {ob.get('similarity_score')}"
            f"  {'같음' if same_score else '다름'}"
        )
        print(
            f"  우선순위 {oa.get('priority_parts')} vs {ob.get('priority_parts')}"
            f"  {'같음' if same_prio else '다름'}"
        )
        if s_sum is not None:
            print(f"  summary 유사도 {s_sum:.2f}")
        if not (same_score and same_prio and (s_sum is None or s_sum >= min_sim)):
            diff += 1
            if oa.get("summary") and ob.get("summary"):
                print(f"      A: {oa.get('summary')}")
                print(f"      B: {ob.get('summary')}")

    print()
    print("판정: 같음" if diff == 0 else f"판정: 다름 ({diff}곳)")
    return diff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", nargs=2, metavar=("SESSION", "FILE"), help="세션을 JSON 으로 저장")
    ap.add_argument("--a", help="세션 id 또는 저장한 JSON (기준)")
    ap.add_argument("--b", help="세션 id 또는 저장한 JSON (비교)")
    ap.add_argument("--min-sim", type=float, default=0.6)
    args = ap.parse_args()

    if args.dump:
        session, out = args.dump
        snap = snapshot(UUID(session))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(snap, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        print(
            f"저장: {out}  (부위 {len(snap['parts'])}, 종합 {'있음' if snap['overall'] else '없음'})"
        )
        return 0
    if not (args.a and args.b):
        ap.error("--a/--b 또는 --dump 가 필요하다")
    return 0 if compare(load(args.a), load(args.b), args.min_sim) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
