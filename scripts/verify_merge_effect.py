"""옷 병합이 실제로 작동했는지 — **병합 전/후를 갈라서** 본다.

    python scripts/verify_merge_effect.py --photo <photo_id>
    python scripts/verify_merge_effect.py --latest

왜 필요한가
    `clothing_pixel_count = 0` 하나로는 해석이 안 갈린다.

      Upper_Clothing 0px  + count 0  + 상완 valid  → Sapiens2 가 옷을 몸으로
                                                     흡수했다. **병합이 필요 없었다**
      Upper_Clothing >0px + count >0 + 상완 valid  → ⭐ **병합이 실제로 작동**
      Upper_Clothing >0px + count 0                → 🔴 병합이 안 돌았거나
                                                     번짐이 그 부위에 안 닿았다
      count NULL                                   → 🔴 병합 코드를 아예 안 탔다

    앞의 둘은 결과가 같아 보이는데 뜻이 정반대다. 붙는 옷은 Sapiens2 가 몸으로
    흡수해서 **병합 경로를 아예 안 탄다.**

⚠️ **GPU 도 재추론도 필요 없다.** 저장되는 맵은 **병합 전 원본**이라
   (docs/db-design-v4.md §1) 거기서 옷 픽셀을 그대로 셀 수 있다.
   병합 결과는 body_part_segment 행에 이미 들어 있다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from app.services import db, segmap, storage  # noqa: E402
from app.services.part_merge import CLOTHING_CLASSES  # noqa: E402

#: 옷에 가려 죽기 쉬운 부위. 여기가 살아났는지가 오늘의 관심사다.
WATCH = ("Left_Upper_Arm", "Right_Upper_Arm", "Left_Lower_Arm", "Right_Lower_Arm")


def latest_segmentation() -> dict | None:
    rows = (
        db.get_client()
        .table("segmentation")
        .select("*")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", help="photo_id")
    ap.add_argument("--latest", action="store_true", help="가장 최근 세그멘테이션")
    args = ap.parse_args()

    c = db.get_client()
    if args.latest or not args.photo:
        seg = latest_segmentation()
    else:
        rows = c.table("segmentation").select("*").eq("photo_id", args.photo).execute().data
        seg = rows[0] if rows else None

    if seg is None:
        print("세그멘테이션 결과가 없습니다.")
        return 1

    print("=" * 72)
    print("옷 병합 검증 — 병합 전(맵 원본) vs 병합 후(저장된 통계)")
    print("=" * 72)
    print(f"  photo_id   {seg['photo_id']}")
    print(f"  맵         {seg['map_width']}x{seg['map_height']}  {seg['map_path']}")
    print(f"  인물 픽셀   {seg['person_pixel_count']:,}")

    # ── 병합 전: 맵 원본에서 직접 센다 ──────────────────────────────────
    raw = storage.download(seg["storage_bucket"], seg["map_path"])
    labels = np.array(segmap.load_map(raw))
    label_map: dict[str, str] = seg["label_map"]

    counts = Counter(labels.flatten().tolist())
    pre = {label_map[str(v)]: n for v, n in counts.items() if str(v) in label_map}

    # ── 병합 후: 저장된 부위 통계 ───────────────────────────────────────
    parts = (
        c.table("body_part_segment")
        .select("*")
        .eq("segmentation_id", seg["segmentation_id"])
        .execute()
        .data
    )
    post = {p["class_name"]: p for p in parts}

    print("\n[1] 병합 전 — 옷 클래스가 얼마나 잡혔나")
    cloth_total = 0
    for name in CLOTHING_CLASSES:
        n = pre.get(name, 0)
        cloth_total += n
        print(f"      {name:18} {n:>9,} px")
    print(f"      {'합계':18} {cloth_total:>9,} px")

    # ⚠️ **흡수량은 전 부위에서 합산한다.** 예전에는 팔 4개만 더해서 "흡수 0" 이라
    #    결론냈는데, 실제로는 몸통이 40,372px 를 흡수하고 있었다. 검출 안 된 부위만
    #    보면 **병합이 살려낸 부위를 통째로 놓친다.**
    absorbed_rows = [r for r in parts if (r.get("clothing_pixel_count") or 0) > 0]
    absorbed_all = sum(r["clothing_pixel_count"] for r in absorbed_rows)

    print("\n[2] 옷을 흡수한 부위 (전체)")
    if not absorbed_rows:
        print("      없음")
    for r in sorted(absorbed_rows, key=lambda x: -x["clothing_pixel_count"]):
        pct = r["clothing_pixel_count"] / r["pixel_count"] * 100
        flag = "  ⚠️ 사실상 옷만 보고 있음" if pct >= 90 else ""
        print(
            f"      {r['class_name']:18}{r['pixel_count']:>9,}px  "
            f"흡수 {r['clothing_pixel_count']:>9,}px  {pct:5.1f}%"
            f"  {'유효' if r['is_valid'] else r['invalid_reason']}{flag}"
        )

    print("\n[3] 팔 — 병합 전 → 병합 후")
    print(f"      {'부위':20} {'병합전':>9} {'병합후':>9} {'옷흡수':>9}  판정")
    for name in WATCH:
        before = pre.get(name, 0)
        row = post.get(name)
        after = row["pixel_count"] if row else 0
        cloth = row.get("clothing_pixel_count") if row else None
        valid = row and row["is_valid"]
        mark = "유효" if valid else (row["invalid_reason"] if row else "행 없음")
        cs = "NULL" if cloth is None else f"{cloth:,}"
        print(f"      {name:20} {before:>9,} {after:>9,} {cs:>9}  {mark}")

    # ── 판정 ────────────────────────────────────────────────────────────
    any_null = any(
        (post.get(n) or {}).get("clothing_pixel_count") is None for n in WATCH if post.get(n)
    )
    arm_valid = any((post.get(n) or {}).get("is_valid") for n in WATCH)

    print("\n[3] 결론")
    if any_null:
        print("      🔴 clothing_pixel_count 가 NULL — **병합 코드를 안 탔다.**")
        print("         seg_merge_clothing 설정을 확인할 것.")
    elif cloth_total == 0 and absorbed_all == 0:
        print("      ⚪ 옷 픽셀이 0 — Sapiens2 가 옷을 몸으로 흡수했다.")
        print("         **병합이 필요 없었다.** 병합이 작동하는지는 이 사진으로 확인 못 한다.")
        print("         → 더 헐렁한 옷 사진으로 다시 볼 것.")
    elif cloth_total > 0 and absorbed_all == 0:
        print("      🔴 옷 픽셀은 있는데 흡수가 0 — **병합이 안 돌았거나 번짐이 안 닿았다.**")
        print("         옷이 어떤 비교 대상 부위와도 인접하지 않으면 이렇게 된다")
        print("         (맨살이 하나도 안 보이면 번져나갈 씨앗이 없다).")
    elif cloth_total > 0 and absorbed_all > 0:
        print(f"      ⭐ 옷 {cloth_total:,}px 중 {absorbed_all:,}px 를 부위가 흡수했다.")
        print(f"         **병합이 실제로 작동했다.** 팔 유효: {'예' if arm_valid else '아니오'}")
        if not arm_valid:
            print("         ⚠️ 다만 흡수하고도 팔이 유효 기준에 못 미쳤다.")
    heavy = [r for r in absorbed_rows if r["clothing_pixel_count"] / r["pixel_count"] >= 0.9]
    if heavy:
        print()
        print("      ⚠️ 흡수율 90% 이상인 부위가 있다:")
        for r in heavy:
            print(
                f"         {r['class_name']} {r['clothing_pixel_count']/r['pixel_count']*100:.1f}%"
            )
        print("         이건 **맨살이 거의 안 보인다**는 뜻이다. 병합이 부위를 살려내긴 했지만")
        print("         살아난 픽셀이 옷 위 실루엣이라 근육 윤곽 근거로는 못 쓴다.")
        print("         진단 쪽에서 판단 불가(confidence LOW / blocked)를 선언해야 한다.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
