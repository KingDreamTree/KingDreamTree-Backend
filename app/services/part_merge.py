"""옷 픽셀을 가장 가까운 비교 대상 부위로 흡수하는 병합 규칙.

⚠️ **이 모듈이 병합 로직의 유일한 구현이다.** 담당 A(통계·is_valid)와
   담당 B(하이라이트 마스크)가 **같은 함수를 호출**한다. 두 벌로 구현하면
   통계와 마스크가 어긋나고, 어긋나도 에러가 안 난다.

   numpy 외에는 아무것도 끌어오지 않는다 — 담당 B가 torch 없이 import 할 수 있어야 한다.

왜 필요한가
    옷이 부위를 가리면 노출 픽셀이 줄어 TOO_SMALL 로 무효 처리된다.
    긴팔이면 상완이 1,216px 까지 떨어지는데 기준이 1,500px 이라 죽는다.
    ⚠️ **병합은 is_valid 판정보다 먼저** 일어나야 한다. 나중에 병합하면
       이미 무효로 찍힌 부위를 되살릴 수 없다.

왜 맵 자체를 덮어쓰지 않는가
    맵은 추론 결과의 원본이다 (docs/db-design-v4.md §1). 덮어쓰면
      * label_map 에는 있는 클래스가 맵에는 없는 상태가 된다
      * 병합 규칙을 바꾸려면 Sapiens2 를 다시 돌려야 한다
      * 프론트 오버레이에서 셔츠가 몸통 색으로 칠해진다
    그래서 저장은 원본으로 하고, 읽는 쪽에서 이 함수를 태운다.

왜 하드코딩 매핑이 아닌가
    `Upper_Clothing → Torso` 같은 고정 규칙은 긴팔에서 반대로 망가진다.
    소매 픽셀이 전부 몸통으로 들어가 **정작 살리려던 팔은 여전히 0** 이고
    몸통만 부푼다. 긴바지도 마찬가지로 종아리가 허벅지로 들어간다.

    대신 **가장 가까운 부위로 번져나가게** 한다. 소매는 팔로, 반바지는
    허벅지로, 긴바지는 허벅지와 종아리로 자연스럽게 나뉘고 좌우도 자동이다.
    규칙을 손으로 적지 않으므로 옷 종류가 바뀌어도 안 깨진다.
"""

from __future__ import annotations

import numpy as np

#: 몸을 가리는 옷 클래스. 신발·양말은 비교 대상 부위를 가리지 않아 제외한다.
CLOTHING_CLASSES: tuple[str, ...] = ("Upper_Clothing", "Lower_Clothing", "Apparel")

#: 번짐 반복 상한. 헐렁한 옷도 이 정도면 채워진다.
#: ⚠️ 여기까지 와도 안 채워진 옷 픽셀은 **옷으로 남긴다** — 어떤 부위에도
#:    닿지 않는 옷(예: 몸이 전혀 안 보이는 통짜 셔츠)을 억지로 배정하면
#:    근거 없는 숫자가 된다.
MAX_STEPS = 256

#: 흡수량 상한 — 부위 하나가 옷에서 가져갈 수 있는 픽셀은 **자기 씨앗의 N배**까지
#: (2026-08-17, 프론트 재현 리포트). 상한이 없으면 씨앗 크기와 무관하게 닿는
#: 옷을 전부 삼킨다 — 실측: 발목 노이즈 2px 가 바짓가랑이 12,476px 를,
#: 오라벨 상완 261px 가 정강이 "상의" 12,520px 를 흡수(98%가 옷).
#:
#: ⚠️ 왜 20인가 — 살리려던 원래 케이스를 죽이면 안 된다. 긴팔에서 노출 씨앗이
#:    손목 언저리 수백 px 뿐이어도 소매 수천 px 는 흡수돼야 한다(20배면 충분).
#:    반대로 노이즈 씨앗(수 px)은 수십 px 에서 멈춘다. 잠정값 — 실호출 로그가
#:    쌓이면 clothing_ratio 게이트와 함께 재조정한다.
#: ⚠️ 상한 검사는 스텝 단위라 마지막 스텝에서 파면(한 겹) 만큼 넘칠 수 있다.
#:    정확히 자르려면 픽셀 단위 정렬이 필요한데, 넘침이 둘레 한 겹로 유계라
#:    복잡도를 치를 가치가 없다.
ABSORB_CAP_RATIO = 20

#: 좌우 경계벽에 쓰는 부위 접미사 — Left_X/Right_X 둘 다 씨앗이 있을 때만
#: 두 씨앗 무게중심의 가운데 x 를 벽으로 세운다 (2026-08-17, 같은 리포트).
#: 벽이 없으면 왼쪽 씨앗이 바짓가랑이를 넘어 오른쪽 다리의 옷까지 삼켜
#: bbox 가 양쪽 다리를 덮는다 — 좌우 굵기 비교가 그 순간 무의미해진다.
#: ⚠️ 한쪽 씨앗이 없으면 벽을 세우지 않는다 — 기준 없이 자르면 정당한 흡수까지
#:    막는다(그 손해는 흡수량 상한이 유계로 막아준다).
_LR_SUFFIXES: tuple[str, ...] = ("Upper_Arm", "Lower_Arm", "Upper_Leg", "Lower_Leg")

#: 옷이 **덮을 수 있는 부위**. 여기 없는 부위로는 번지지 않는다.
#:
#: ⚠️ 이건 모듈 주석이 경계한 "옷 → 부위 고정 매핑"이 아니다. 그건 소매를
#:    전부 몸통으로 보내는 식의 **배정**이라 긴팔에서 망가졌다. 이건 반대로
#:    **물리적으로 불가능한 방향만 막는** 제약이고, 어느 부위로 갈지는 여전히
#:    번짐(가장 가까운 부위)이 정한다.
#:
#: ⚠️ 왜 필요한가 (실측 2026-08-16) — 상의를 안 입고 반바지만 입은 사진에서
#:    **허벅지가 프레임 밖**이라 반바지가 갈 곳이 없었다. 그러자 유일하게 인접한
#:    몸통으로 256스텝 밀고 올라가 92,443px 이 흡수됐다.
#:      몸통  147,178px → 239,621px  (63% 부풀림, 그중 39% 가 반바지)
#:    부위 면적이 그만큼 틀어지면 크기·비율 비교가 통째로 흔들린다.
#:    덮을 부위가 검출되지 않은 옷은 **옷으로 남기는 것이 맞다.**
_COVERS: dict[str, tuple[str, ...]] = {
    "Upper_Clothing": ("Torso", "Upper_Arm", "Lower_Arm"),
    "Lower_Clothing": ("Upper_Leg", "Lower_Leg"),
    # 종류를 알 수 없는 옷은 제한하지 않는다 — 막을 근거가 없다.
    "Apparel": (),
}


def _allowed_targets(cloth_name: str, targets: set[str]) -> set[str]:
    """이 옷이 덮을 수 있는 부위만 남긴다. 좌우 접두사는 무시하고 비교한다."""
    suffixes = _COVERS.get(cloth_name, ())
    if not suffixes:
        return targets
    return {t for t in targets if any(t.endswith(s) for s in suffixes)}


def merge_clothing(
    labels: np.ndarray,
    label_map: dict[str, str],
    targets: set[str],
    max_steps: int = MAX_STEPS,
) -> tuple[np.ndarray, dict[str, int]]:
    """옷 픽셀을 인접한 비교 대상 부위로 흡수한 라벨 배열을 돌려준다.

    labels    : 원본 라벨 배열 (H×W uint8). **이 배열은 변경하지 않는다.**
    label_map : {인덱스(str): 클래스명} — 추론 당시 매핑
    targets   : 흡수 대상이 될 클래스명 집합 (보통 is_comparable=true 9개)

    반환: (병합된 라벨 배열, {클래스명: 옷에서 흡수한 픽셀 수})

    ⚠️ 두 번째 값(옷 기여도)을 꼭 같이 쓸 것. 병합은 "헐렁한 옷 실루엣"도
       유효한 부위로 만들어버려서, 진단이 틀려도 조용히 넘어간다.
       어디까지가 실제 노출이고 어디부터가 옷인지는 이 숫자로만 알 수 있다.
    """
    value_of = {name: int(idx) for idx, name in label_map.items()}

    present = [c for c in CLOTHING_CLASSES if c in value_of]
    if not present or not any(t in value_of for t in targets):
        return labels.copy(), {}

    # ⚠️ 옷 종류마다 **덮을 수 있는 부위가 다르다** (_COVERS). 한 번에 번지게 하면
    #    반바지가 몸통으로 올라간다. 종류별로 나눠서 번지고 결과를 합친다.
    merged = labels.copy()
    total: dict[str, int] = {}
    for cloth_name in present:
        allowed = _allowed_targets(cloth_name, targets)
        if not allowed:
            continue  # 덮을 부위가 이 사진에 없다 — 옷으로 남긴다
        merged, part = _spread_one(merged, value_of, cloth_name, allowed, max_steps)
        for k, v in part.items():
            total[k] = total.get(k, 0) + v
    return merged, total


def _spread_one(
    labels: np.ndarray,
    value_of: dict[str, int],
    cloth_name: str,
    targets: set[str],
    max_steps: int,
) -> tuple[np.ndarray, dict[str, int]]:
    """옷 클래스 하나를 허용된 부위로 번지게 한다. (원래 merge_clothing 본체)"""
    cloth_values = [value_of[cloth_name]]
    target_values = {value_of[t] for t in targets if t in value_of}
    if not target_values:
        return labels.copy(), {}

    cloth_mask = np.isin(labels, cloth_values)
    if not cloth_mask.any():
        return labels.copy(), {}

    # 옷 영역 + 여유 1px 만 잘라서 계산한다. 전체 맵을 반복해서 훑을 이유가 없다.
    ys, xs = np.nonzero(cloth_mask)
    y0, y1 = max(0, int(ys.min()) - 1), min(labels.shape[0], int(ys.max()) + 2)
    x0, x1 = max(0, int(xs.min()) - 1), min(labels.shape[1], int(xs.max()) + 2)

    crop = labels[y0:y1, x0:x1]
    crop_cloth = cloth_mask[y0:y1, x0:x1]

    # 씨앗 = 비교 대상 부위 픽셀. 여기서부터 옷 영역으로 번져나간다.
    filled = np.where(np.isin(crop, list(target_values)), crop, 0).astype(np.uint8)
    remaining = crop_cloth.copy()

    # ── 구조 제약 (2026-08-17, 프론트 재현 리포트) ──────────────────────────
    # ② 흡수량 상한 — 클래스별 씨앗 픽셀 수 × ABSORB_CAP_RATIO
    seed_values, seed_counts = np.unique(filled[filled > 0], return_counts=True)
    cap = {int(v): int(c) * ABSORB_CAP_RATIO for v, c in zip(seed_values, seed_counts)}
    absorbed_by = {int(v): 0 for v in seed_values}

    # ① 좌우 경계벽 — Left_X/Right_X 둘 다 씨앗이 있으면 무게중심 가운데가 벽.
    #    ⚠️ 라벨 이름이 아니라 **씨앗의 실제 위치**로 좌우를 정한다 — 거울 사진이든
    #       라벨이 뒤집혔든, 벽은 "자기 무게중심이 있는 쪽만 흡수"로 동작한다.
    walls: list[tuple[int, float, bool]] = []  # (클래스값, 벽 x, 자기쪽이 왼쪽인가)
    for suffix in _LR_SUFFIXES:
        lv = value_of.get(f"Left_{suffix}")
        rv = value_of.get(f"Right_{suffix}")
        if lv is None or rv is None or lv not in cap or rv not in cap:
            continue
        lx = float(np.nonzero(filled == lv)[1].mean())
        rx = float(np.nonzero(filled == rv)[1].mean())
        if lx == rx:
            continue  # 같은 열에 겹침 — 벽을 세울 근거가 없다
        wall = (lx + rx) / 2
        walls.append((lv, wall, lx < rx))
        walls.append((rv, wall, rx < lx))
    col = np.arange(filled.shape[1])[None, :]

    for _ in range(max_steps):
        if not remaining.any():
            break

        # ⚠️ 네 방향 후보를 **같은 스냅샷**에서 뽑는다. 중간에 갱신된 값을 다시
        #    참조하면 한 번의 반복에서 여러 칸이 번져 거리가 왜곡된다.
        snapshot = filled
        candidate = np.zeros_like(snapshot)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            rolled = np.roll(snapshot, shift, axis=axis)
            # 경계를 넘어온 값은 버린다 (np.roll 은 반대편에서 감아온다)
            if axis == 0:
                if shift == 1:
                    rolled[0, :] = 0
                else:
                    rolled[-1, :] = 0
            else:
                if shift == 1:
                    rolled[:, 0] = 0
                else:
                    rolled[:, -1] = 0
            candidate = np.where(candidate == 0, rolled, candidate)

        # ① 벽 너머 픽셀은 못 가져간다 / ② 상한에 닿은 클래스는 흡수를 멈춘다
        for v, wall, keep_left in walls:
            beyond = (col > wall) if keep_left else (col < wall)
            candidate = np.where((candidate == v) & beyond, 0, candidate)
        for v, used in absorbed_by.items():
            if used >= cap[v]:
                candidate = np.where(candidate == v, 0, candidate)

        take = remaining & (candidate > 0)
        if not take.any():
            break  # 더 번질 곳이 없다 — 남은 옷은 옷으로 둔다

        filled = np.where(take, candidate, filled)
        remaining &= ~take
        for v, c in zip(*np.unique(candidate[take], return_counts=True)):
            absorbed_by[int(v)] = absorbed_by.get(int(v), 0) + int(c)

    merged = labels.copy()
    absorbed = crop_cloth & (filled > 0)
    merged[y0:y1, x0:x1] = np.where(absorbed, filled, crop)

    name_of = {v: k for k, v in value_of.items()}
    values, counts = np.unique(filled[absorbed], return_counts=True)
    contribution = {
        name_of[int(v)]: int(c)
        for v, c in zip(values.tolist(), counts.tolist())
        if int(v) in name_of
    }
    return merged, contribution
