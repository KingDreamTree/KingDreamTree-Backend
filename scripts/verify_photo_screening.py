"""2차 검사(사진 적합성)를 **실제 GPT-4o 로** 돌려본다.

왜 필요한가
    2차 검사는 사용자의 업로드를 **막는** 기능이다. 여기서 잘못 반려하면
    사용자는 멀쩡한 사진을 들고 아무리 다시 찍어도 진행을 못 한다.
    ⚠️ **놓치는 것보다 잘못 막는 게 훨씬 나쁘다.** 이 스크립트는 그걸 본다.

쓰는 법
    1) photos/ 폴더를 만들고 레퍼런스 1장 + 시험할 사진들을 넣는다.
       레퍼런스는 파일명이 reference 로 시작하게 둔다.

        photos/
          reference.jpg          ← 기준 사진
          ok_tight_1.jpg         ← 통과해야 하는 것 (몸에 붙는 옷)
          ok_tight_2.jpg
          ng_loose_1.jpg         ← 반려돼야 하는 것 (헐렁한 옷)
          ng_loose_2.jpg
          ng_far.jpg             ← 반려돼야 하는 것 (촬영 거리 다름)

       ⚠️ 파일명이 ok_ 로 시작하면 "통과 기대", ng_ 면 "반려 기대"로 보고
          기대와 다르면 표시한다. 접두어가 없으면 결과만 보여준다.

    2) python scripts/verify_photo_screening.py

옵션
    --dir <경로>     사진 폴더 (기본: photos)
    --repeat <N>     같은 사진을 N번 돌린다. 판정이 매번 흔들리는지 본다.

⚠️ 실제 API 요금이 나간다. 사진 1장당 호출 1번 (레퍼런스와 같이 2장 전송).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services import photo_screening  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _find_photos(root: Path) -> tuple[Path, list[Path]]:
    if not root.is_dir():
        raise SystemExit(f"사진 폴더가 없습니다: {root}\n   docstring 의 '쓰는 법' 참고")

    files = sorted(p for p in root.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    refs = [p for p in files if p.stem.lower().startswith("reference")]
    if not refs:
        raise SystemExit(f"레퍼런스가 없습니다. {root} 안에 reference.jpg 를 두세요.")

    reference = refs[0]
    return reference, [p for p in files if p != reference]


def _expected(path: Path) -> bool | None:
    """파일명 접두어에서 기대 결과를 읽는다. 없으면 None."""
    stem = path.stem.lower()
    if stem.startswith("ok"):
        return True
    if stem.startswith("ng"):
        return False
    return None


async def _run_one(reference: bytes, path: Path) -> tuple[photo_screening.ScreenResult, float]:
    started = time.perf_counter()
    result = await photo_screening.screen(path.read_bytes(), reference)
    return result, time.perf_counter() - started


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="photos")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    if not settings.openai_api_key:
        raise SystemExit(
            "OPENAI_API_KEY 가 없습니다.\n"
            "   .env 에 OPENAI_API_KEY 와 VLM_PROVIDER=openai 를 넣으세요."
        )

    if settings.use_mock:
        # ⚠️ 이 스크립트만 예외다. 목적이 "실제 모델이 어떻게 판정하는가"를 보는 것이라
        #    가짜 응답을 받으면 스크립트 자체가 무의미하다. .env 는 건드리지 않는다.
        print("⚠️ USE_MOCK=true 지만 이 스크립트는 실제 GPT-4o 를 호출합니다 (요금 발생).")
        settings.use_mock = False

    reference_path, targets = _find_photos(Path(args.dir))
    reference = reference_path.read_bytes()

    print(f"레퍼런스 : {reference_path.name}")
    print(f"시험 대상: {len(targets)}장 × {args.repeat}회 = 호출 {len(targets) * args.repeat}번")
    print(f"전송 크기: 긴 변 {settings.photo_screening_max_side}px 로 축소")
    print("=" * 78)

    mismatches: list[str] = []
    unstable: list[str] = []
    skipped = 0

    for path in targets:
        expected = _expected(path)
        verdicts: list[bool] = []

        for i in range(args.repeat):
            try:
                result, elapsed = await _run_one(reference, path)
            except photo_screening.ScreeningUnavailable as e:
                skipped += 1
                print(f"\n{path.name}\n  ⚠️ 판정 불가({e}) — 실서비스라면 503 재시도 안내")
                continue

            if result.skipped:
                skipped += 1
                print(f"\n{path.name}\n  ⚠️ 판정 건너뜀 (설정·레퍼런스 문제). {elapsed:.1f}초")
                continue

            verdicts.append(result.suitable)
            mark = "통과" if result.suitable else "반려"

            if i == 0:
                exp_txt = "" if expected is None else f"   (기대: {'통과' if expected else '반려'})"
                print(f"\n{path.name}{exp_txt}")

            print(f"  [{i + 1}] {mark}  {elapsed:.1f}초  reason={result.reason or '-'}", end="")
            print(f"  rule={result.rule or '-'}  confidence={result.confidence or '-'}")
            if result.message:
                print(f"      문구: {result.message}")

            if expected is not None and result.suitable != expected:
                # ⚠️ 통과해야 할 사진을 막은 쪽이 훨씬 심각하다.
                severity = "🔴 잘못 막음" if expected else "🟡 놓침"
                mismatches.append(f"{severity}  {path.name}  reason={result.reason or '-'}")

        if len(set(verdicts)) > 1:
            unstable.append(path.name)

    print("\n" + "=" * 78)
    if skipped:
        print(f"⚠️ 판정을 못 한 호출 {skipped}건 — API 오류/타임아웃. 위 로그 확인.")
    if unstable:
        print(f"⚠️ 같은 사진인데 판정이 갈린 것: {', '.join(unstable)}")
        print("   → 경계선에 있는 사진이거나 프롬프트가 모호하다는 뜻이다.")

    if not mismatches:
        print("기대와 다른 판정 없음 ✅")
        return 0

    print(f"기대와 다른 판정 {len(mismatches)}건:")
    for line in mismatches:
        print(f"  {line}")
    print("\n🔴 는 프롬프트를 반드시 고쳐야 한다 — 멀쩡한 사진을 막으면 진행이 불가능하다.")
    print("🟡 는 2차 검사가 놓친 것. 세그 결과가 나빠지지만 서비스는 돈다.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
