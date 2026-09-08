"""두 세션의 진단 결과를 나란히 비교한다 — 얼굴 가림(services/face_mask) 실측용.

    # 1) 실제 랜드마크 준비 (한 번)
    python scripts/pose_landmarks.py photos/123.jpg --out out/landmarks/123.json
    python scripts/pose_landmarks.py photos/456.jpg --out out/landmarks/456.json
    # 2) 팟을 POD_FACE_MASK=false 로 띄우고 → 스모크 --keep  (A: 원본)
    # 3) 팟을 POD_FACE_MASK=true  로 띄우고 → 스모크 --keep  (B: 얼굴 가림)
    #    (둘 다 --ref-landmarks/--user-landmarks 로 같은 랜드마크 파일을 준다)
    # 4)
    python scripts/compare_diagnosis.py --a <세션A> --b <세션B>
    # 5) 끝나면 두 유저를 지운다: DELETE /users/me (X-User-Id)

무엇을 보나
    · 부위 진단 9행: gap_level · priority · confidence 가 같은가, assessment 문장 유사도
    · 종합 진단: similarity_score · priority_parts · silhouette · summary 유사도
    · 세그 수치(is_valid, 부위 면적비)는 가림과 무관해야 한다 — 세그는 안 가린 가공본을 쓴다

판정 기준 (GPT 는 temperature=0 이어도 문장이 조금씩 달라진다)
    같음 : 등급·우선순위·점수가 전부 같고 문장 유사도 ≥ 0.6
    다름 : 등급·우선순위·점수 중 하나라도 다르면 — 그때는 가림 박스를 줄여 다시 잰다

읽기 전용 — 아무것도 바꾸지 않는다. 서비스 키로 DB 를 직접 본다.
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

from app.services import db  # noqa: E402


def sim(a: str | None, b: str | None) -> float:
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def parts(session_id: UUID) -> dict[str, dict]:
    rows = db.rows_for_session(
        "part_diagnosis", session_id, "class_name,gap_level,priority,confidence,assessment,status"
    )
    return {r["class_name"]: r for r in rows}


def overall(session_id: UUID) -> dict | None:
    rows = db.rows_for_session(
        "overall_diagnosis",
        session_id,
        "similarity_score,priority_parts,silhouette,summary,confidence,status",
    )
    return rows[0] if rows else None


def segments(session_id: UUID) -> dict[str, dict]:
    photos = db.rows_for_session("photo", session_id, "photo_id,kind")
    out: dict[str, dict] = {}
    for p in photos:
        seg = db.get_segmentation(UUID(p["photo_id"]))
        if seg:
            out[p["kind"]] = seg
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="세션 A (원본, POD_FACE_MASK=false)")
    ap.add_argument("--b", required=True, help="세션 B (얼굴 가림)")
    ap.add_argument("--min-sim", type=float, default=0.6)
    args = ap.parse_args()
    a, b = UUID(args.a), UUID(args.b)
    diff = 0

    print("== 세그 (가림과 무관해야 함) ==")
    sa, sb = segments(a), segments(b)
    for kind in sorted(set(sa) | set(sb)):
        x, y = sa.get(kind), sb.get(kind)
        if not x or not y:
            print(f"  {kind}: 한쪽에 세그 없음")
            continue
        same_valid = x.get("is_valid") == y.get("is_valid")
        print(
            f"  {kind}: is_valid {x.get('is_valid')} vs {y.get('is_valid')}  {'같음' if same_valid else '다름'}"
        )
        if not same_valid:
            diff += 1

    print("\n== 부위 진단 ==")
    pa, pb = parts(a), parts(b)
    for name in sorted(set(pa) | set(pb)):
        x, y = pa.get(name), pb.get(name)
        if not x or not y:
            print(f"  {name}: 한쪽에 없음")
            diff += 1
            continue
        same = all(x.get(k) == y.get(k) for k in ("gap_level", "priority", "confidence", "status"))
        s = sim(x.get("assessment"), y.get("assessment"))
        flag = "같음" if same and s >= args.min_sim else "다름"
        if flag == "다름":
            diff += 1
        print(
            f"  {name:14s} {x.get('gap_level')}/{x.get('priority')}/{x.get('confidence')}"
            f"  vs  {y.get('gap_level')}/{y.get('priority')}/{y.get('confidence')}"
            f"  문장 유사도 {s:.2f}  {flag}"
        )
        if flag == "다름":
            print(f"      A: {x.get('assessment')}")
            print(f"      B: {y.get('assessment')}")

    print("\n== 종합 진단 ==")
    oa, ob = overall(a), overall(b)
    if not oa or not ob:
        print("  한쪽에 종합 진단 없음")
        diff += 1
    else:
        same_score = oa.get("similarity_score") == ob.get("similarity_score")
        same_prio = json.dumps(oa.get("priority_parts"), sort_keys=True) == json.dumps(
            ob.get("priority_parts"), sort_keys=True
        )
        s_sum = sim(oa.get("summary"), ob.get("summary"))
        s_sil = sim(oa.get("silhouette"), ob.get("silhouette"))
        print(
            f"  점수 {oa.get('similarity_score')} vs {ob.get('similarity_score')}  {'같음' if same_score else '다름'}"
        )
        print(
            f"  우선순위 {oa.get('priority_parts')} vs {ob.get('priority_parts')}  {'같음' if same_prio else '다름'}"
        )
        print(f"  summary 유사도 {s_sum:.2f} / silhouette 유사도 {s_sil:.2f}")
        if not (same_score and same_prio and s_sum >= args.min_sim):
            diff += 1
            print(f"      A: {oa.get('summary')}")
            print(f"      B: {ob.get('summary')}")

    print()
    print("판정: 같음" if diff == 0 else f"판정: 다름 ({diff}곳)")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
