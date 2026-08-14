"""자세가 얼마나 틀어지면 굵기 측정이 망가지는지 잰다.

무엇을 답하는가
    "자세 점수 몇 점부터 통과시켜야 하는가"를 **취향이 아니라 측정으로** 정한다.

    지금 THRESHOLD=70 은 "TOL=45 기준 평균 오차 13.5°" 라는 계산에서 나온
    그럴듯한 출발점일 뿐이다. 사람이 실제로 자세를 틀었을 때 **부위 굵기 측정이
    얼마나 흔들리는지**는 재본 적이 없다.

어떻게 재는가
    같은 사람이 같은 옷·같은 거리에서 자세만 바꿔 찍는다.
    **진짜 굵기는 안 변했으므로, 숫자가 흔들린 만큼이 전부 측정 오차다.**

    자세 점수 100점짜리를 기준으로 삼고, 점수가 내려갈수록 오차가 얼마나
    커지는지 본다. 오차가 "우리가 잡아내려는 차이"의 절반을 넘는 지점이
    임계값이다.

⚠️ 이 스크립트가 답하지 **못하는** 것
    * 다른 체형·다른 옷에서도 같은 곡선인가 (n=1 이다)
    * 사람이 실제로 그 점수를 낼 수 있는가
      → 실험 결과가 "80점 필요"인데 아무도 80점을 못 내면, 임계값이 아니라
        레퍼런스 자세나 촬영 안내를 고쳐야 한다. 그건 데모로 확인한다.

준비
    1) web/pose-demo.html 로 자세 점수를 보면서 사진을 찍는다.
       점수가 대략 100 / 90 / 80 / 70 / 60 이 되는 순간에 한 장씩.
       ⚠️ 옷 갈아입지 말고, 서 있는 자리도 옮기지 말 것. **자세만** 바꾼다.

    2) 파일명에 그때의 자세 점수를 넣는다. 숫자만 읽는다.

        tolerance/
          100.jpg      또는  pose-100.jpg
          92.jpg
          81.jpg
          70.jpg
          58.jpg

    3) GPU 가 있는 곳에서 (RunPod):
        python scripts/measure_pose_tolerance.py --dir tolerance

⚠️ 옷 병합은 끄고 잰다. 켜두면 옷에서 흡수한 픽셀이 섞여
   "자세 때문에 변한 것"과 "병합 때문에 변한 것"을 구분할 수 없다.
   (--keep-merge 로 켠 채 잴 수도 있다. 병합이 오차를 키우는지 보고 싶을 때)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services import db  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _score_of(path: Path) -> float | None:
    """파일명에서 자세 점수를 읽는다. 없으면 None.

    ⚠️ **0~100 인 숫자 중 마지막 것**을 쓴다. 첫 숫자를 그냥 집으면
       `2026-08-14_70.jpg` 에서 2026 을 점수로 읽는다. 카메라·스마트폰이
       날짜를 파일명에 넣는 게 흔해서 실제로 밟게 된다.
    """
    candidates = [float(m) for m in re.findall(r"\d+(?:\.\d+)?", path.stem)]
    usable = [c for c in candidates if 0 <= c <= 100]
    return usable[-1] if usable else None


def _collect(root: Path) -> list[tuple[float, Path]]:
    if not root.is_dir():
        raise SystemExit(f"폴더가 없습니다: {root}\n   docstring 의 '준비' 참고")

    found: list[tuple[float, Path]] = []
    for p in sorted(root.iterdir()):
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        s = _score_of(p)
        if s is None:
            print(f"  건너뜀 (파일명에 점수가 없음): {p.name}")
            continue
        found.append((s, p))

    if len(found) < 3:
        raise SystemExit(
            f"사진이 {len(found)}장뿐입니다. 최소 3장은 있어야 곡선이 보입니다.\n"
            "   점수가 높은 것부터 낮은 것까지 골고루 찍으세요."
        )
    return sorted(found, key=lambda t: -t[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="tolerance", help="사진 폴더")
    ap.add_argument(
        "--max-error",
        type=float,
        default=7.0,
        help="허용할 측정 오차(%%). 잡아내려는 차이의 절반 정도로 잡는다 (기본 7)",
    )
    ap.add_argument(
        "--keep-merge",
        action="store_true",
        help="옷 병합을 켠 채로 잰다 (기본은 끈다 — 원인을 섞지 않기 위해)",
    )
    args = ap.parse_args()

    # ⚠️ 병합을 켜두면 "자세 때문에 변한 것"과 "병합 때문에 변한 것"이 섞인다.
    if not args.keep_merge:
        settings.seg_merge_clothing = False

    shots = _collect(Path(args.dir))

    from app.services import segmenter  # torch 를 늦게 끌어온다

    comparable = set(db.comparable_class_names())
    if not comparable:
        raise SystemExit("body_part 마스터가 비어 있습니다. scripts/seed_body_parts.py 먼저.")
    master = db.master_class_names()

    print(f"환경     : {segmenter.describe_environment()}")
    print(f"옷 병합  : {'켬' if settings.seg_merge_clothing else '끔'}")
    print(f"사진     : {len(shots)}장\n")

    # ── 측정 ────────────────────────────────────────────────────────────
    rows: list[tuple[float, dict[str, float], int]] = []
    for score, path in shots:
        result = segmenter.segment(
            path.read_bytes(), comparable=comparable, master_class_names=master
        )
        ratios = {p.class_name: p.area_ratio for p in result.parts if p.class_name in comparable}
        valid = sum(1 for p in result.parts if p.is_valid and p.class_name in comparable)
        rows.append((score, ratios, valid))
        print(f"  {path.name:20} 자세 {score:5.1f}점  유효 부위 {valid}개  {result.inference_ms}ms")

    base_score, base, base_valid = rows[0]
    print(f"\n기준: 자세 {base_score:.1f}점 사진 (여기서의 굵기를 '참값'으로 본다)")
    print("⚠️ 같은 사람·같은 옷·같은 거리이므로, 이 뒤의 변화는 전부 측정 오차다.\n")

    # ── 부위별 표 ───────────────────────────────────────────────────────
    parts = [c for c in sorted(base) if base[c] > 0]
    head = "  자세점수 | " + " | ".join(f"{c[:11]:>11}" for c in parts) + " |   평균오차"
    print(head)
    print("  " + "-" * (len(head) - 2))

    drift_by_score: list[tuple[float, float]] = []
    for score, ratios, valid in rows:
        cells, errs = [], []
        for c in parts:
            cur = ratios.get(c)
            if cur is None or base[c] <= 0:
                cells.append(f"{'없음':>11}")
                continue
            e = (cur - base[c]) / base[c] * 100
            errs.append(abs(e))
            cells.append(f"{e:>+10.1f}%")
        mean_err = sum(errs) / len(errs) if errs else float("nan")
        drift_by_score.append((score, mean_err))
        flag = "  ← 유효 부위 감소" if valid < base_valid else ""
        print(f"  {score:8.1f} | " + " | ".join(cells) + f" | {mean_err:8.1f}%{flag}")

    # ── 결론 ────────────────────────────────────────────────────────────
    print(f"\n{'=' * 78}")
    print(f"허용 오차 {args.max_error:.1f}% 를 넘지 않는 가장 낮은 자세 점수를 찾는다.")

    ok = [s for s, e in drift_by_score if e <= args.max_error]
    over = [(s, e) for s, e in drift_by_score if e > args.max_error]

    if not over:
        print(f"\n  ⚠️ 찍은 사진 전부가 오차 {args.max_error:.1f}% 안에 들어옵니다.")
        print(f"     가장 낮은 점수가 {min(ok):.0f}점인데도 멀쩡하다는 뜻이니,")
        print(f"     **더 크게 틀어진 사진을 찍어서 다시 재세요.** 경계가 안 보입니다.")
        print(
            f"     (지금 임계값 {settings.pose_threshold:.0f}점은 필요 이상으로 깐깐할 수 있습니다)"
        )
        return 0

    if not ok:
        print(f"\n  ⚠️ 기준 사진조차 오차가 큽니다. 측정이 불안정합니다.")
        print(f"     같은 자세로 두 장 찍어 둘을 비교해보세요 — 그 차이가 바닥 노이즈입니다.")
        return 1

    lowest_ok = min(ok)
    highest_bad = max(s for s, _ in over)
    suggested = round((lowest_ok + highest_bad) / 2)

    print(f"\n  오차 {args.max_error:.1f}% 안         : {lowest_ok:.0f}점까지")
    print(f"  오차 {args.max_error:.1f}% 초과 시작    : {highest_bad:.0f}점부터")
    print(f"\n  → 제안 THRESHOLD = {suggested}     (지금 값 {settings.pose_threshold:.0f})")
    print(f"\n  이제 이렇게 말할 수 있습니다:")
    print(
        f'    "{suggested}점 아래로 내려가면 부위 굵기 측정이 {args.max_error:.0f}% 넘게 틀어져서"'
    )
    print(f"\n  ⚠️ n=1 입니다. '검증됨'이 아니라 '근거 있는 값'입니다.")
    print(f"     체형이 다른 사람 2~3명을 더 재면 훨씬 단단해집니다.")
    print(f"  ⚠️ 이 값으로 바꾸기 전에 **사람이 실제로 그 점수를 낼 수 있는지** 확인하세요.")
    print(f"     web/pose-demo.html 로 몇 번 찍어보면 본인이 몇 점대인지 바로 나옵니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
