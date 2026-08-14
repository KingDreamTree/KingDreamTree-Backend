# 1차 검사 — 자세 점수 산식

| | |
|---|---|
| **최종 수정일** | 2026-08-14 |
| **누가 계산하나** | 프론트엔드 (MediaPipe Pose 33 랜드마크) |
| **누가 판정하나** | 서버 (`app/services/pose.py`) |

---

## 무엇을 판단하는 건가

**"이 두 사진을 부위별로 견줘도 되는가"** 입니다. 사진의 잘잘못이 아닙니다.

레퍼런스는 팔을 내리고 있는데 사용자가 팔을 벌리고 있으면, 상완의 보이는 면적이
달라져서 **"팔이 굵다/가늘다"를 비교할 수 없습니다.** 자세가 결과를 오염시킵니다.

---

## ⚠️ 좌표를 직접 비교하면 안 되는 이유

사용자가 레퍼런스보다 **한 발 뒤에 서 있으면** 모든 좌표가 달라집니다. 하지만
자세는 똑같습니다. 좌표 차이로 점수를 매기면 **자세가 아니라 서 있는 위치를 재게 됩니다.**

그래서 세 가지를 따로 잽니다.

| | 무엇을 | 왜 필요한가 | 무엇에 불변인가 |
|---|---|---|---|
| **P** 자세 | 관절이 이루는 **각도** | 팔다리가 같은 방향인가 | 위치·거리·사람 크기 |
| **F** 프레이밍 | 인물이 화면을 채우는 **모양** | 둘 다 전신이 비슷하게 담겼나 | — |
| **R** 정면성 | 어깨폭 / 몸통길이 **비율** | 몸이 돌아가 있지 않은가 | 위치·거리 |

**R이 왜 따로 필요한가** — 팔을 내린 자세는 정면이든 30° 돌아섰든 **각도가 거의 같습니다.**
그런데 몸이 돌아가면 어깨폭이 좁아 보여서, 정작 우리가 진단하는 **실루엣 굵기가 왜곡됩니다.**
각도만으로는 이걸 못 잡습니다.

---

## P — 자세 유사도 (0~100)

### 재는 각도 9개

부위별 진단 대상과 정확히 대응시킵니다. **비교하지 않을 부위의 각도는 재지 않습니다** —
얼굴 방향이 다르다고 자세가 틀렸다고 할 이유가 없습니다.

| 각도 | 랜드마크 | 대응 진단 부위 |
|---|---|---|
| 왼쪽 상완 | 어깨(11) → 팔꿈치(13) | 왼팔 상완 |
| 왼쪽 전완 | 팔꿈치(13) → 손목(15) | 왼팔 전완 |
| 오른쪽 상완 | 어깨(12) → 팔꿈치(14) | 오른팔 상완 |
| 오른쪽 전완 | 팔꿈치(14) → 손목(16) | 오른팔 전완 |
| 왼쪽 허벅지 | 엉덩이(23) → 무릎(25) | 왼쪽 허벅지 |
| 왼쪽 종아리 | 무릎(25) → 발목(27) | 왼쪽 종아리 |
| 오른쪽 허벅지 | 엉덩이(24) → 무릎(26) | 오른쪽 허벅지 |
| 오른쪽 종아리 | 무릎(26) → 발목(28) | 오른쪽 종아리 |
| 몸통 기울기 | 어깨중점 → 엉덩이중점 | 몸통 |

### 계산

각 각도의 **차이 `d`(도)** 를 구하고, 관절 하나의 점수를 이렇게 냅니다.

```
s = clamp(1 − d / TOL, 0, 1)        TOL = 45°
P = 100 × (s 들의 평균)
```

**평균을 쓰되, 한 관절이라도 크게 어긋나면 무조건 탈락시킵니다.**

```
어느 하나라도 d > HARD 이면  →  P = 0        HARD = 60°
```

> ⚠️ **평균만 쓰면** 왼팔이 완전히 다른 방향인데 나머지가 잘 맞아 통과합니다.
> 그러면 왼팔 진단만 조용히 틀립니다.
> **최솟값만 쓰면** 손목이 살짝 흔들린 것 때문에 전체가 탈락합니다.
> 그래서 둘을 같이 씁니다 — 평균으로 점수를 내고, 하드 상한으로 파국을 막습니다.

### 신뢰도가 낮은 관절은 뺍니다

MediaPipe는 관절마다 `visibility`(0~1)를 줍니다. 가려진 관절은 좌표가 추측값이라
각도가 엉뚱하게 나옵니다. **양쪽 사진 모두에서 `visibility ≥ 0.5` 인 각도만** 계산에 넣습니다.

⚠️ 남은 각도가 **4개 미만이면 판정 불가**로 보고 재촬영을 권합니다. 두세 개로 낸
평균은 근거가 약합니다.

---

## F — 프레이밍 일치 (0~1)

인물을 감싸는 사각형을 두 사진에서 각각 구해 **겹치는 정도(IoU)** 를 봅니다.
MediaPipe 좌표가 이미 0~1로 정규화돼 있어 사진 크기가 달라도 그대로 비교됩니다.

```
F = 교집합 넓이 / 합집합 넓이
```

**왜 필요한가** — 레퍼런스는 전신인데 사용자는 상반신만 나오면, 각도는 맞아도
다리를 아예 비교할 수 없습니다. F는 "둘 다 비슷한 범위를 담았는가"를 봅니다.

---

## R — 정면성 차이 (0~1)

```
비율 = 어깨 사이 거리 / 어깨중점~엉덩이중점 거리
R = |비율(사용자) − 비율(레퍼런스)| / 비율(레퍼런스)
```

몸이 돌아가면 어깨폭이 줄어들어 이 비율이 작아집니다. 몸통 길이로 나누기 때문에
**거리와 사람 크기에 영향받지 않습니다.**

---

## 최종 판정

```
F < F_MIN         →  재촬영 (프레이밍)
R > R_MAX         →  재촬영 (몸이 돌아감)
유효 각도 < 4개    →  재촬영 (판정 불가)
P < THRESHOLD     →  재촬영 (자세)
그 외             →  통과
```

⚠️ **순서가 의미를 갖습니다.** 프레이밍이 깨진 상태의 자세 점수는 믿을 수 없고,
안내 문구도 달라야 합니다 — "몸이 다 나오게 서주세요"와 "포즈를 맞춰주세요"는
사용자가 해야 할 행동이 다릅니다.

---

## 임계값과 그 근거

| 값 | 기본 | 근거 | 낮추면 / 높이면 |
|---|---|---|---|
| `TOL` | **45°** | 팔다리가 "다른 방향"으로 보이기 시작하는 지점. 45° 차이면 육안으로 명백히 다른 자세 | 낮추면 깐깐해져 재촬영이 잦음 |
| `HARD` | **60°** | 한 관절이 이 이상 어긋나면 그 부위 진단은 못 씀 | — |
| `THRESHOLD` | **70** | `TOL=45` 기준 **평균 오차 13.5° 이내**. 사람이 따라 하려 애쓸 때 나오는 편차 수준 | 높이면 통과가 어려워짐 |
| `F_MIN` | **0.65** | 전신 사진 둘이 비슷한 구도면 IoU 0.7~0.9. 0.65는 "한쪽이 눈에 띄게 다른 범위" 선 | — |
| `R_MAX` | **0.25** | 어깨폭 비율이 25% 넘게 차이나면 몸이 뚜렷하게 돌아간 것 | — |

> ⚠️ **이 값들은 시작점입니다.** "논리적 타당성"은 숫자 자체가 아니라
> **(1) 산식이 거리·위치에 불변이고 (2) 각 임계값이 무엇을 막는지 말할 수 있고
> (3) 조정 방법이 정해져 있다**는 데 있습니다.
>
> **조정 방법** — 시연에 쓸 레퍼런스로 사람이 **일부러 잘 맞춘 사진 5장**과
> **일부러 다르게 찍은 사진 5장**을 찍어 점수를 뽑습니다. 두 무리 사이가
> 벌어지는 지점에 `THRESHOLD`를 놓습니다. 겹치면 `TOL`을 조정합니다.

---

## 실시간 촬영은 왜 자동으로 1차를 통과하는가

촬영 화면은 매 프레임 P·F·R을 계산해 화면에 보여주고, **통과 조건을 만족한 순간에
셔터를 누릅니다.** 그러니 그렇게 찍힌 사진은 정의상 통과입니다.

⚠️ **그러려면 프론트와 서버가 같은 임계값을 봐야 합니다.** 프론트에 숫자를
하드코딩하면 서버에서 조정한 순간 어긋납니다 — 화면에서는 통과인데 저장이
거부되는 상황이 생깁니다.

서버가 값을 내려줍니다:

```
GET /api/v1/pose-criteria
{ "tol_deg": 45, "hard_tol_deg": 60, "threshold": 70,
  "f_min": 0.65, "r_max": 0.25, "min_visible_angles": 4, "min_visibility": 0.5 }
```

⚠️ **한 번 흔들리는 걸 방지하려면** 조건을 만족한 상태가 몇 프레임 이어질 때
셔터를 누르세요 (`N_HOLD`, 기본 15프레임 ≈ 0.5초). 손이 지나가다 우연히 맞는
순간에 찍히면 안 됩니다.

---

## 프론트 참고 구현

```js
const IDX = {
  shoulderL: 11, shoulderR: 12, elbowL: 13, elbowR: 14, wristL: 15, wristR: 16,
  hipL: 23, hipR: 24, kneeL: 25, kneeR: 26, ankleL: 27, ankleR: 28,
};

// 진단 부위와 1:1 대응하는 9개 방향
const SEGMENTS = [
  ["upperArmL", IDX.shoulderL, IDX.elbowL],
  ["lowerArmL", IDX.elbowL,    IDX.wristL],
  ["upperArmR", IDX.shoulderR, IDX.elbowR],
  ["lowerArmR", IDX.elbowR,    IDX.wristR],
  ["upperLegL", IDX.hipL,      IDX.kneeL],
  ["lowerLegL", IDX.kneeL,     IDX.ankleL],
  ["upperLegR", IDX.hipR,      IDX.kneeR],
  ["lowerLegR", IDX.kneeR,     IDX.ankleR],
];

const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
const angleOf = (a, b) => Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;

// 두 각도의 차이는 항상 0~180 으로 접는다 (350° 와 10° 는 20° 차이다)
const angleDiff = (p, q) => { const d = Math.abs(p - q) % 360; return d > 180 ? 360 - d : d; };

function poseScore(ref, user, c) {
  const diffs = [];

  for (const [, a, b] of SEGMENTS) {
    const ok = [ref[a], ref[b], user[a], user[b]].every(p => p.visibility >= c.min_visibility);
    if (!ok) continue;                       // 가려진 관절은 뺀다
    diffs.push(angleDiff(angleOf(ref[a], ref[b]), angleOf(user[a], user[b])));
  }

  // 몸통 기울기
  const torso = (lm) => angleOf(mid(lm[IDX.shoulderL], lm[IDX.shoulderR]),
                                mid(lm[IDX.hipL], lm[IDX.hipR]));
  diffs.push(angleDiff(torso(ref), torso(user)));

  if (diffs.length < c.min_visible_angles) return { score: 0, reason: "NOT_ENOUGH_JOINTS" };
  if (diffs.some(d => d > c.hard_tol_deg))  return { score: 0, reason: "JOINT_TOO_FAR" };

  const mean = diffs.reduce((s, d) => s + Math.max(0, 1 - d / c.tol_deg), 0) / diffs.length;
  return { score: Math.round(mean * 1000) / 10, reason: null };   // 소수 첫째자리
}

function framingIoU(ref, user) {
  const box = (lm) => ({
    x0: Math.min(...lm.map(p => p.x)), x1: Math.max(...lm.map(p => p.x)),
    y0: Math.min(...lm.map(p => p.y)), y1: Math.max(...lm.map(p => p.y)),
  });
  const a = box(ref), b = box(user);
  const w = Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0));
  const h = Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  const inter = w * h;
  const union = (a.x1-a.x0)*(a.y1-a.y0) + (b.x1-b.x0)*(b.y1-b.y0) - inter;
  return union > 0 ? inter / union : 0;
}

function facingDelta(ref, user) {
  const ratio = (lm) => {
    const sw = Math.hypot(lm[IDX.shoulderL].x - lm[IDX.shoulderR].x,
                          lm[IDX.shoulderL].y - lm[IDX.shoulderR].y);
    const s = mid(lm[IDX.shoulderL], lm[IDX.shoulderR]);
    const h = mid(lm[IDX.hipL], lm[IDX.hipR]);
    const torsoLen = Math.hypot(s.x - h.x, s.y - h.y);
    return torsoLen > 0 ? sw / torsoLen : 0;
  };
  const r = ratio(ref);
  return r > 0 ? Math.abs(ratio(user) - r) / r : 0;
}
```

업로드할 때 이 세 값을 그대로 보냅니다.

```
pose_similarity  = poseScore(...).score    // 0~100
framing_score    = framingIoU(...)         // 0~1
facing_delta     = facingDelta(...)        // 0~1
```

⚠️ **최종 통과 여부를 프론트가 판단해 보내지 않습니다.** 값만 보내고 판정은 서버가
합니다. 화면에는 서버가 내려준 임계값으로 미리 계산해 보여주면 됩니다.
