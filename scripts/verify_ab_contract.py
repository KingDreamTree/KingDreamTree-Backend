"""A↔B 계약 검증 — A 가 쓰는 필드와 B 가 읽는 필드가 어긋나지 않는지 본다.

    python scripts/verify_ab_contract.py

⚠️ **A 를 기다리지 않고 지금 할 수 있는 통합 검증이다.**

    Phase 6(실제 Sapiens2 연동)에서 깨질 확률이 가장 높은 곳은 모델이 아니라
    **두 사람이 합의한 데이터 모양**이다. A 가 컬럼 하나를 빼거나 이름을 바꾸면
    B 는 KeyError 로 죽는 게 아니라 `.get()` 이 None 을 돌려주며 **조용히**
    이상한 진단을 낸다. 실연동 당일에 알면 늦다.

    그래서 코드에서 직접 뽑아 대조한다:
        A 가 쓰는 것    app/worker/handlers/seg.py 의 replace_segmentation(...) 인자
        B 가 읽는 것    diagnosis_repo · segmap · handlers/vlm 이 참조하는 키
        DB 가 받는 것   db/schema.sql 의 컬럼
        실제 샘플       A 가 준 segmentation.json (있으면)

⚠️ 이건 A 의 **추론 결과가 맞는지**는 검증하지 않는다. 모양만 본다.
   실제 연동은 A 와 함께 사진 한 장을 끝까지 돌려봐야 한다.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PASS, FAIL, WARN = "[OK]", "[X]", "[!]"
_failures: list[str] = []
_warnings: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        _failures.append(label)


def warn(label: str, detail: str = "") -> None:
    print(f"  {WARN} {label}{(' — ' + detail) if detail else ''}")
    _warnings.append(label)


# --------------------------------------------------------------------------- #
# A 가 쓰는 필드 — seg.py 의 replace_segmentation 호출을 AST 로 읽는다
# --------------------------------------------------------------------------- #


def _writer_fields() -> tuple[set[str], set[str]]:
    """(segmentation 필드, body_part_segment 필드).

    ⚠️ 문자열 검색이 아니라 AST 로 읽는다. 주석이나 문서에 적힌 이름을
       실제 코드로 착각하면 검증이 거짓 통과한다.
    """
    src = (ROOT / "app" / "worker" / "handlers" / "seg.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    seg_fields: set[str] = set()
    part_fields: set[str] = set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "replace_segmentation":
            continue
        for kw in node.keywords:
            if kw.arg == "segmentation" and isinstance(kw.value, ast.Dict):
                seg_fields = {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
            elif kw.arg == "parts":
                # parts=[ {…} for p in … ] 형태
                for inner in ast.walk(kw.value):
                    if isinstance(inner, ast.Dict):
                        part_fields |= {k.value for k in inner.keys if isinstance(k, ast.Constant)}
    return seg_fields, part_fields


# --------------------------------------------------------------------------- #
# B 가 읽는 필드
# --------------------------------------------------------------------------- #

#: B 가 A 데이터에서 실제로 읽는 키. 코드에서 뽑되, **여기 적힌 것이 계약이다** —
#: 새로 읽기 시작하면 이 목록에 추가하고 A 에게 알린다.
B_READS_SEGMENTATION = {
    "segmentation_id",
    "storage_bucket",
    "map_path",
    "label_map",
}
B_READS_PART = {
    "class_name",
    "segment_id",
    "pixel_count",
    "area_ratio",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "is_truncated",
    "is_valid",
    "invalid_reason",
    "clothing_pixel_count",
}


def _schema_columns(table: str) -> set[str]:
    """schema.sql 에서 테이블 컬럼 이름을 뽑는다."""
    src = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", src, re.S)
    if not m:
        return set()
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith(("--", "CONSTRAINT", "REFERENCES", "CHECK")):
            continue
        token = line.split()[0]
        if re.fullmatch(r"[a-z_]+", token):
            cols.add(token)
    return cols


def main() -> int:
    print("A↔B 계약 검증\n")

    seg_written, part_written = _writer_fields()
    check(
        "A 의 write 호출을 찾음",
        bool(seg_written and part_written),
        f"segmentation {len(seg_written)}필드 / part {len(part_written)}필드",
    )
    if not (seg_written and part_written):
        print("\nseg.py 의 replace_segmentation 호출을 못 찾았다. 구조가 바뀌었는지 확인할 것.")
        return 1

    # ── 1. B 가 읽는 것을 A 가 다 쓰는가 ────────────────────────────────────
    print("\n1. B 가 읽는 필드를 A 가 쓰는가")
    # segmentation_id 는 DB 가 만들고, segment_id 도 마찬가지다 (writer 는 안 씀)
    db_generated = {"segmentation_id", "segment_id"}
    missing_seg = B_READS_SEGMENTATION - seg_written - db_generated
    missing_part = B_READS_PART - part_written - db_generated
    check("segmentation 누락 없음", not missing_seg, str(sorted(missing_seg)))
    check("body_part_segment 누락 없음", not missing_part, str(sorted(missing_part)))

    # ── 2. A 가 쓰는 것을 DB 가 받는가 ──────────────────────────────────────
    print("\n2. A 가 쓰는 필드를 DB 가 받는가")
    seg_cols = _schema_columns("segmentation")
    part_cols = _schema_columns("body_part_segment")
    check(
        "schema.sql 파싱",
        bool(seg_cols and part_cols),
        f"segmentation {len(seg_cols)}컬럼 / part {len(part_cols)}컬럼",
    )
    unknown_seg = seg_written - seg_cols - {"photo_id"}
    unknown_part = part_written - part_cols - {"segmentation_id"}
    check("segmentation 미정의 컬럼 없음", not unknown_seg, str(sorted(unknown_seg)))
    check("body_part_segment 미정의 컬럼 없음", not unknown_part, str(sorted(unknown_part)))

    # ── 3. B 가 읽는 것을 DB 가 가지는가 ────────────────────────────────────
    print("\n3. B 가 읽는 필드가 DB 에 있는가")
    ghost_seg = B_READS_SEGMENTATION - seg_cols
    ghost_part = B_READS_PART - part_cols
    check("segmentation 유령 필드 없음", not ghost_seg, str(sorted(ghost_seg)))
    check("body_part_segment 유령 필드 없음", not ghost_part, str(sorted(ghost_part)))

    # ── 4. 실제 샘플과 대조 ─────────────────────────────────────────────────
    print("\n4. A 가 준 실제 샘플과 대조")
    # ⚠️ 예전 경로(ROOT.parent / "map")는 리포 **바깥**이라 만든 사람 컴퓨터에서만
    #    존재했고, 이 검사가 어디서나 조용히 skip되고 있었다 (2026-08-17 발견).
    #    샘플은 스모크 산출물로 만든다: smoke_e2e_segmentation.py --out out/e2e --keep
    sample = ROOT / "out" / "e2e" / "segmentation.json"
    if not sample.exists():
        warn(
            "샘플 없음 — 이 검사는 건너뜀 "
            "(scripts/smoke_e2e_segmentation.py --out out/e2e 로 생성)",
            str(sample),
        )
    else:
        data = json.loads(sample.read_text(encoding="utf-8"))
        palette = data.get("palette") or []
        check("샘플에 palette 존재", bool(palette), f"{len(palette)}부위")
        if palette:
            sample_keys = set(palette[0])
            # 샘플은 API 응답 형태라 컬럼명과 완전히 같지는 않다.
            # ⚠️ 그래도 **비교의 근거가 되는 것들**은 반드시 있어야 한다.
            essential = {"class_name", "is_valid", "is_comparable", "pixel_count"}
            missing = essential - sample_keys
            check("판정 근거 필드 존재", not missing, str(sorted(missing)))
            check(
                "label_value 는 있지만 조인 키로 쓰지 않음",
                "label_value" in sample_keys,
                "조인은 항상 class_name (모델 버전마다 숫자가 재배열됨)",
            )

    # ── 5. 스모크가 지어내는 데이터가 계약과 같은가 ─────────────────────────
    print("\n5. 전 구간 스모크의 가짜 데이터가 계약과 일치하는가")
    smoke = (ROOT / "scripts" / "smoke_full_flow.py").read_text(encoding="utf-8")
    fake_fields = set(re.findall(r'"([a-z_]+)":', smoke.split("_seg_rows")[1][:2000]))
    # 스모크가 A 보다 **적게** 쓰는 건 괜찮다(선택 컬럼). 없는 걸 쓰면 문제다.
    invented = fake_fields - part_cols - seg_cols - {"class_name"}
    check("스모크가 없는 컬럼을 만들지 않음", not invented, str(sorted(invented)))
    required_for_diag = {"class_name", "pixel_count", "is_valid", "bbox_x", "bbox_w"}
    check(
        "스모크가 진단에 필요한 필드를 채움",
        required_for_diag <= fake_fields,
        str(sorted(required_for_diag - fake_fields)),
    )

    # ── 6. 옷 병합 소비 ─────────────────────────────────────────────────────
    print("\n6. 옷 병합 결과(clothing_pixel_count) 소비")
    segmap_src = (ROOT / "app" / "services" / "segmap.py").read_text(encoding="utf-8")
    check(
        "B 가 clothing_pixel_count 를 읽음",
        "clothing_pixel_count" in segmap_src,
        "옷 위 실루엣을 근육 근거로 쓰지 않기 위한 신호",
    )
    check(
        "NULL(병합 미적용)과 0(흡수 없음)을 구분",
        "is None" in segmap_src or "None" in segmap_src,
        "NOT NULL DEFAULT 0 이면 둘을 못 가른다",
    )

    print()
    for w in _warnings:
        print(f"{WARN} {w}")
    if _failures:
        print(f"\n{FAIL} 실패 {len(_failures)}건:")
        for f in _failures:
            print(f"   - {f}")
        return 1
    print(f"\n{PASS} 계약 일치 (경고 {len(_warnings)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
