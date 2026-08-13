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

    cloth_values = [value_of[c] for c in CLOTHING_CLASSES if c in value_of]
    target_values = {value_of[t] for t in targets if t in value_of}
    if not cloth_values or not target_values:
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

        take = remaining & (candidate > 0)
        if not take.any():
            break  # 더 번질 곳이 없다 — 남은 옷은 옷으로 둔다

        filled = np.where(take, candidate, filled)
        remaining &= ~take

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
