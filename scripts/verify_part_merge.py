"""옷 병합이 **덮을 수 없는 부위로 번지지 않는가.**

    python scripts/verify_part_merge.py

⚠️ 이 모듈은 두 가지를 동시에 지켜야 한다. 한쪽만 보면 반대쪽이 깨진다.

    살려야 할 것   긴팔에 가린 팔 — 소매가 팔로 흡수돼야 부위가 살아난다
    막아야 할 것   덮을 부위가 없는 옷 — 엉뚱한 부위로 번지면 면적이 부푼다

실측(2026-08-16) — 상의 없이 반바지만 입고 **허벅지가 프레임 밖**인 사진에서
반바지 92,443px 이 몸통으로 올라갔다 (몸통 147,178 → 239,621, 63% 부풀림).
그 면적으로 크기·비율을 비교하면 전부 틀어진다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import part_merge  # noqa: E402

PASS, FAIL = "[OK]", "[X]"
_failed: list[str] = []

#: 라벨 인덱스 (값 자체는 의미 없다 — label_map 으로만 해석된다)
LM = {
    "1": "Torso",
    "2": "Left_Upper_Arm",
    "3": "Upper_Clothing",
    "4": "Lower_Clothing",
    "5": "Left_Upper_Leg",
}
TARGETS = {"Torso", "Left_Upper_Arm", "Left_Upper_Leg"}


def check(label: str, cond: bool, note: str = "") -> None:
    print(f"  {PASS if cond else FAIL} {label}{(' — ' + note) if note else ''}")
    if not cond:
        _failed.append(label)


def main() -> int:
    print("옷 병합 — 덮을 수 있는 부위로만 번지는가\n")

    # ── 1. 긴팔: 소매가 팔로 흡수돼야 한다 (이 기능의 존재 이유) ────────────
    a = np.zeros((100, 60), dtype=np.uint8)
    a[0:20, 20:40] = 2  # 드러난 상완
    a[20:80, 20:40] = 3  # 그 아래는 소매(Upper_Clothing)
    _, contrib = part_merge.merge_clothing(a, LM, TARGETS)
    check(
        "긴팔 소매가 팔로 흡수된다",
        contrib.get("Left_Upper_Arm", 0) > 0,
        f"{contrib.get('Left_Upper_Arm', 0):,}px",
    )

    # ── 2. 반바지 + 다리 있음: 다리로 가야 한다 ───────────────────────────
    b = np.zeros((100, 60), dtype=np.uint8)
    b[0:40, 20:40] = 1  # 몸통
    b[40:70, 20:40] = 4  # 반바지
    b[70:100, 20:40] = 5  # 허벅지
    _, contrib = part_merge.merge_clothing(b, LM, TARGETS)
    check("반바지는 다리로 간다", contrib.get("Left_Upper_Leg", 0) > 0, str(contrib))
    check("반바지가 몸통으로 가지 않는다", "Torso" not in contrib, str(contrib))

    # ── 3. 반바지 + 다리 없음(프레임 밖): 옷으로 남아야 한다 ───────────────
    #     ⚠️ 실제로 터졌던 케이스다. 종전에는 전부 몸통으로 올라갔다.
    c = np.zeros((100, 60), dtype=np.uint8)
    c[0:60, 20:40] = 1  # 몸통
    c[60:100, 20:40] = 4  # 반바지 — 아래에 다리가 없다
    merged, contrib = part_merge.merge_clothing(c, LM, TARGETS)
    torso_before = int((c == 1).sum())
    torso_after = int((merged == 1).sum())
    check(
        "다리가 없으면 반바지는 몸통으로 안 간다",
        contrib.get("Torso", 0) == 0,
        f"흡수 {contrib.get('Torso', 0):,}px",
    )
    check(
        "몸통 면적이 그대로다",
        torso_after == torso_before,
        f"{torso_before:,} → {torso_after:,}",
    )
    check(
        "흡수 안 된 옷은 옷으로 남는다",
        int((merged == 4).sum()) == int((c == 4).sum()),
    )

    print()
    if _failed:
        print(f"실패 {len(_failed)}건: {', '.join(_failed)}")
        return 1
    print("문제 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
