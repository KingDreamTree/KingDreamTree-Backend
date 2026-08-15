"""부위 진단을 같은 입력으로 N회 돌려 **규칙 위반을 센다.**

    python scripts/eval_diagnosis.py --session <session_id> --runs 5

━━ 왜 필요한가 ━━

프롬프트를 고칠 때마다 **1회 돌려보고 판단해왔다.** LLM 출력은 같은 입력에도
흔들리므로, 1회 비교로는 "좋아졌다"와 "이번엔 운이 좋았다"를 구분할 수 없다.
실제로 2026-08-15 하루에만 같은 규칙을 두고 결론이 세 번 뒤집혔다.

이 스크립트는 **입력을 한 번만 조립하고 진단만 N회 반복한다** (세그멘테이션·
업로드를 반복하지 않으므로 비용은 VLM 호출 N회뿐). 위반 건수의 중앙값과
범위를 같이 찍어, 변동 폭 안의 차이인지 실제 개선인지 보이게 한다.

⚠️ **DB 에 쓰지 않는다.** 진단 결과를 저장하지 않고 세지기만 한다.
   세션의 기존 진단은 그대로 남는다.

━━ 위반 검사의 한계 ━━

⚠️ 처방 문장 탐지는 **휴리스틱**이다 (운동 이름 + 권유형 어미). 한국어 종결형을
   완벽히 가르지는 못하므로 절대 수치가 아니라 **같은 잣대로 잰 상대 비교**로 쓸 것.
   프롬프트 A 와 B 를 같은 검사로 재는 한 방향은 신뢰할 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.enums import PhotoKind  # noqa: E402
from app.services import diagnosis_repo, segmap, vlm  # noqa: E402
from app.services.vlm import _norm_ko  # noqa: E402
from app.worker.handlers.vlm import _inbody_for, _load_side  # noqa: E402

#: 문장 수 상한 — 초과하면 위반. §길이 (프롬프트) 와 같은 표.
_MAX_SENTENCES = {"NONE": 1, "SLIGHT": 1, "MODERATE": 2, "SIGNIFICANT": 3}

#: 처방으로 보는 신호. 운동 이름이 나오거나 권유형으로 끝나면 처방으로 센다.
_EXERCISE_WORDS = ("스쿼트", "런지", "플랭크", "팔굽혀펴기", "덤벨", "컬", "운동")
_ADVICE_TAIL = re.compile(r"(세요|봅시다|하면 됩니다|적합합니다|좋습니다|권합니다)\.?$")

#: 프롬프트가 이름으로 금지한 표현 (§반복 표현).
_BANNED = (
    "목표 체형보다 부족합니다",
    "목표 체형보다 가늘어 보입니다",
    "운동을 해보세요",
    "강화해보세요",
    "현재 운동량을 유지하세요",
)

#: 두 부위 문장이 같다고 볼 유사도.
_DUP_RATIO = 0.75


def _is_pair(a: str, b: str) -> bool:
    """좌우 쌍인가. (Left_Upper_Arm ↔ Right_Upper_Arm)"""
    for x, y in ((a, b), (b, a)):
        if x.startswith("Left_") and y == "Right_" + x[len("Left_") :]:
            return True
    return False


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _has_advice(text: str) -> bool:
    for s in _sentences(text):
        if _ADVICE_TAIL.search(s.strip()) and any(w in s for w in _EXERCISE_WORDS):
            return True
    return False


def audit(parts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """규칙 위반을 모은다. {검사 이름: [위반 설명...]}"""
    out: dict[str, list[str]] = {
        k: [] for k in ("길이", "처방", "인바디인용", "중복문장", "좌우불일치", "금지표현")
    }

    for p in parts:
        name, gap, text = p["class_name"], p.get("gap_level"), p.get("assessment") or ""
        n = len(_sentences(text))
        cap = _MAX_SENTENCES.get(gap or "")
        if cap and n > cap:
            out["길이"].append(f"{name} {gap} {n}문장 (상한 {cap})")

        # 처방은 priority 1·2 부위에만
        pr = p.get("priority")
        if _has_advice(text) and (pr is None or pr > 2):
            out["처방"].append(f"{name} p{pr}")

        for b in _BANNED:
            if b in text:
                out["금지표현"].append(f"{name}: {b}")

    # ⚠️ 옷에 가려 시각 판단이 불가한 부위(blocked)는 **인바디가 유일한 근거**라
    #    인용이 정당하다 — 프롬프트가 명시한 예외다. 상한 2곳에서 빼고 센다.
    #    (이 예외를 빼먹었더니 정상 동작을 위반으로 세고 있었다. 2026-08-15)
    cited = [
        p["class_name"]
        for p in parts
        if "%" in (p.get("assessment") or "") and not p.get("blocked_reason")
    ]
    if len(cited) > 2:
        out["인바디인용"].append(f"{len(cited)}곳: {', '.join(cited)}")

    # ⚠️ 좌우 쌍이 같은 것은 **의도한 동작**이다 (vlm._unify_pairs). 위반이 아니다.
    #    쌍이 아닌 두 부위가 겹치는 것만 센다.
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            if _is_pair(a["class_name"], b["class_name"]):
                continue
            ta, tb = a.get("assessment") or "", b.get("assessment") or ""
            if not ta or not tb:
                continue
            if SequenceMatcher(None, _norm_ko(ta), _norm_ko(tb)).ratio() >= _DUP_RATIO:
                out["중복문장"].append(f"{a['class_name']} ≈ {b['class_name']}")

    # 좌우 쌍인데 **문장이 갈라진** 경우 — 이제는 이쪽이 위반이다.
    for a in parts:
        if not a["class_name"].startswith("Left_"):
            continue
        right = "Right_" + a["class_name"][len("Left_") :]
        b = next((p for p in parts if p["class_name"] == right), None)
        if b is None or a.get("gap_level") != b.get("gap_level"):
            continue  # 등급이 다르면 문장도 달라야 한다
        ta, tb = a.get("assessment") or "", b.get("assessment") or ""
        if ta and tb and ta != tb:
            out["좌우불일치"].append(f"{a['class_name']} ≠ {right}")

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="사진·세그멘테이션이 끝난 세션")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--show", action="store_true", help="매 회 문장도 출력")
    args = ap.parse_args()

    session_id = UUID(args.session)

    # ── 입력 조립은 한 번만 (핸들러와 같은 경로를 그대로 쓴다) ──────────────
    context = diagnosis_repo.build_comparison_context(session_id)
    if not context["ready"]:
        print("세그멘테이션이 끝나지 않은 세션입니다.")
        return 1

    ref_photo, ref_overlay, ref_painted = _load_side(context, PhotoKind.REFERENCE)
    user_photo, user_overlay, user_painted = _load_side(context, PhotoKind.USER)
    painted = set(ref_painted) & set(user_painted)
    parts = [p for p in context["parts"] if p["class_name"] in painted]
    names = [p["class_name"] for p in parts]

    ref_seg = context["segments"][str(PhotoKind.REFERENCE)]
    user_seg = context["segments"][str(PhotoKind.USER)]
    person = context["person_classes"]
    metrics = segmap.compare_parts(ref_seg, user_seg, names, person)
    inbody, inbody_source = _inbody_for(session_id)

    print(f"부위 {len(names)}개 · 인바디 {inbody_source} · {args.runs}회 반복\n")

    totals: dict[str, list[int]] = {}
    for run in range(1, args.runs + 1):
        result = asyncio.run(
            vlm.diagnose_parts(
                reference_photo=ref_photo,
                reference_overlay=ref_overlay,
                user_photo=user_photo,
                user_overlay=user_overlay,
                parts=parts,
                metrics=metrics,
                reference_symmetry=segmap.symmetry(ref_seg, names, person),
                user_symmetry=segmap.symmetry(user_seg, names, person),
                inbody=inbody,
            )
        )
        found = audit(result["results"])
        counts = {k: len(v) for k, v in found.items()}
        for k, c in counts.items():
            totals.setdefault(k, []).append(c)

        total = sum(counts.values())
        print(
            f"[{run}/{args.runs}] 위반 {total}건  "
            + " · ".join(f"{k} {c}" for k, c in counts.items())
        )
        for k, items in found.items():
            for it in items:
                print(f"        {k}: {it}")
        if args.show:
            for p in result["results"]:
                print(f"        ■ {p['class_name']} [{p.get('gap_level')}/p{p.get('priority')}]")
                print(f"          {p.get('assessment')}")
        print()

    print("=" * 66)
    print(f"{'검사':<12}{'중앙값':>8}{'최소':>6}{'최대':>6}")
    print("-" * 66)
    grand = []
    for k, v in totals.items():
        print(f"{k:<12}{statistics.median(v):>8.1f}{min(v):>6}{max(v):>6}")
        grand.append(sum(v))
    per_run = [sum(totals[k][i] for k in totals) for i in range(args.runs)]
    print("-" * 66)
    print(f"{'합계':<12}{statistics.median(per_run):>8.1f}{min(per_run):>6}{max(per_run):>6}")
    print()
    print("⚠️ 중앙값끼리 비교하세요. 최소~최대 폭 안의 차이는 프롬프트 효과가 아니라")
    print("   그냥 흔들림입니다 — 그 폭보다 크게 줄어야 개선입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
