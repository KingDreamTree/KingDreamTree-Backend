"""운동명 한글화 배치 — data/exercise_catalog.json 의 name_ko 를 채운다.

    python scripts/localize_exercises.py          # name_ko 가 빈 항목만 번역
    python scripts/localize_exercises.py --force  # 전부 다시 번역

LLM 1회성 배치다 (D7). 요청 시점 번역은 하지 않는다 — 200개 × 매 사용자면
비용도 지연도 낭비고, 같은 운동이 사용자마다 다른 이름으로 보이게 된다.

⚠️ USE_MOCK 과 무관하게 실제 OpenAI 를 호출한다. 이 스크립트는 서비스 런타임이
   아니라 개발자용 배치 도구다 (fetch_exercisedb.py 와 같은 취급).

번역 원칙 (프롬프트에 강제):
    * 한국 헬스장에서 통용되는 표기 우선 — Bench Press → "벤치프레스",
      Lat Pulldown → "랫풀다운". 직역("가슴 누르기") 금지.
    * 통용 표기가 없는 동작만 뜻이 통하게 옮긴다.
    * 좌우/자세 수식어는 한국어로 — Single Leg → "한 다리", Seated → "시티드"
      처럼 관용 표기가 있으면 그것을 따른다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

CATALOG = Path(__file__).resolve().parent.parent / "data" / "exercise_catalog.json"

#: 한 호출에 넣을 이름 수. 200개를 통으로 넣으면 출력이 길어져 잘림 위험이 있다.
BATCH_SIZE = 60

SYSTEM = """당신은 한국 피트니스 콘텐츠 번역가입니다. 운동 이름을 한국어로 옮깁니다.

규칙:
1. 한국 헬스장에서 통용되는 표기를 최우선으로 씁니다.
   Bench Press → 벤치프레스 / Lat Pulldown → 랫풀다운 / Deadlift → 데드리프트
2. 통용 표기는 외래어 그대로, 붙여 씁니다 (벤치 프레스 X, 벤치프레스 O).
3. 통용 표기가 없는 동작만 뜻이 통하게 옮깁니다 (예: Bridge → 브릿지).
4. 수식어 관용 표기: Dumbbell → 덤벨, Barbell → 바벨, Cable → 케이블,
   Seated → 시티드, Standing → 스탠딩, Incline → 인클라인, Lever/Machine → 머신,
   Single Leg → 싱글 레그, One Arm → 원암, Alternate → 얼터네이트
5. 운동을 아는 사람이 보면 바로 그 운동인 줄 알아야 합니다. 창작하지 마세요.

입력은 {"id": "영문명"} 목록이고, 출력은 JSON 하나:
{"names": {"<id>": "<한국어명>", ...}}  — 모든 id 를 빠짐없이 포함하세요."""


def _has_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


async def _translate(batch: dict[str, str]) -> dict[str, str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        temperature=0,  # 같은 이름은 언제나 같은 번역
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
    )
    parsed = json.loads(response.choices[0].message.content or "{}")
    usage = response.usage
    print(f"    usage: in={usage.prompt_tokens} out={usage.completion_tokens}")
    return parsed.get("names") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="이미 번역된 것도 다시")
    args = ap.parse_args()

    if not settings.openai_api_key:
        print("[X] OPENAI_API_KEY 가 .env 에 없습니다.")
        return 1
    if not CATALOG.exists():
        print("[X] 카탈로그가 없습니다. fetch_exercisedb.py fetch 를 먼저 실행하세요.")
        return 1

    doc = json.loads(CATALOG.read_text(encoding="utf-8"))
    exercises = doc["exercises"]

    todo = {
        e["exercise_ref"]: e["name_en"].strip()
        for e in exercises
        if args.force or not _has_korean(e.get("name_ko") or "")
    }
    if not todo:
        print("번역할 항목이 없습니다 (전부 완료됨).")
        return 0

    print(f"번역 대상 {len(todo)}개 (배치 {BATCH_SIZE}개씩)")

    names: dict[str, str] = {}
    refs = list(todo)
    for i in range(0, len(refs), BATCH_SIZE):
        chunk = {r: todo[r] for r in refs[i : i + BATCH_SIZE]}
        print(f"  배치 {i // BATCH_SIZE + 1}: {len(chunk)}개")
        names.update(asyncio.run(_translate(chunk)))

    # 검증: 전부 왔는지 + 실제로 한국어인지. 못 받은 항목은 None 으로 남긴다 —
    # 억지로 영문을 복사해 넣으면 "번역 안 됨"이 조용히 숨는다.
    applied, missing, not_korean = 0, [], []
    for e in exercises:
        ref = e["exercise_ref"]
        if ref not in todo:
            continue
        value = (names.get(ref) or "").strip()
        if not value:
            missing.append(todo[ref])
        elif not _has_korean(value):
            not_korean.append(f"{todo[ref]} -> {value}")
        else:
            e["name_ko"] = value
            applied += 1

    CATALOG.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n적용 {applied}개 / 누락 {len(missing)}개 / 비한국어 {len(not_korean)}개")
    for name in missing[:5]:
        print(f"  [누락] {name}")
    for pair in not_korean[:5]:
        print(f"  [비한국어] {pair}")
    print(f"저장: {CATALOG}")
    return 0 if not missing and not not_korean else 1


if __name__ == "__main__":
    sys.exit(main())
