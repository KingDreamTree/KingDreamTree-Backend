/**
 * 1차 검사 — 자세 점수 계산 (프론트 전용)
 *
 * 서버는 이 계산을 하지 않는다. 값만 받아 임계값과 비교한다.
 * 산식과 근거: docs/pose-scoring.md
 *
 * 의존성 없음. <script type="module"> 로 그대로 불러 쓸 수 있다.
 *
 * ⚠️ **임계값을 여기 적지 않는다.** 서버에서 받아 쓴다(fetchCriteria).
 *    하드코딩하면 서버에서 조정한 순간 어긋나고, 화면에서는 통과인데 저장이
 *    거부되는 상황이 생긴다. 그래서 이 파일에는 기본값이 없고, criteria 를
 *    안 넘기면 에러가 난다 — 조용히 틀리는 것보다 낫다.
 */

/** MediaPipe Pose 33개 랜드마크 중 이 계산이 쓰는 것들. */
export const IDX = {
  shoulderL: 11, shoulderR: 12,
  elbowL: 13, elbowR: 14,
  wristL: 15, wristR: 16,
  hipL: 23, hipR: 24,
  kneeL: 25, kneeR: 26,
  ankleL: 27, ankleR: 28,
};

/**
 * 재는 방향 8개. **진단 부위와 1:1로 대응**시킨다.
 *
 * ⚠️ 비교하지 않을 부위(얼굴 방향, 손가락 등)의 각도는 재지 않는다.
 *    얼굴이 어디를 보든 팔뚝 굵기 비교와는 무관한데, 넣으면 그것 때문에
 *    멀쩡한 사진이 떨어진다.
 */
export const SEGMENTS = [
  ["upperArmL", IDX.shoulderL, IDX.elbowL],
  ["lowerArmL", IDX.elbowL, IDX.wristL],
  ["upperArmR", IDX.shoulderR, IDX.elbowR],
  ["lowerArmR", IDX.elbowR, IDX.wristR],
  ["upperLegL", IDX.hipL, IDX.kneeL],
  ["lowerLegL", IDX.kneeL, IDX.ankleL],
  ["upperLegR", IDX.hipR, IDX.kneeR],
  ["lowerLegR", IDX.kneeR, IDX.ankleR],
];

/** 몸통 기울기를 재는 데 필요한 네 점. 하나라도 안 보이면 몸통 각도를 뺀다. */
const TORSO_POINTS = [IDX.shoulderL, IDX.shoulderR, IDX.hipL, IDX.hipR];

// --------------------------------------------------------------------------
// 작은 도구들
// --------------------------------------------------------------------------

const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

/** 두 점이 이루는 방향(도). */
const angleOf = (a, b) => (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;

/**
 * 두 각도의 차이. 항상 0~180 으로 접는다.
 *
 * ⚠️ 접지 않으면 350° 와 10° 가 340° 차이로 나온다. 실제로는 20° 다.
 *    그러면 살짝 흔들린 팔이 하드 상한에 걸려 통째로 탈락한다.
 */
const angleDiff = (p, q) => {
  const d = Math.abs(p - q) % 360;
  return d > 180 ? 360 - d : d;
};

/**
 * 랜드마크의 신뢰도.
 *
 * ⚠️ visibility 를 안 주는 구현이 있어 그때는 1(보임)로 본다. 0 으로 보면
 *    모든 관절이 걸러져 항상 "판정 불가"가 된다 — 조용히 전부 막힌다.
 */
const vis = (p) => (typeof p?.visibility === "number" ? p.visibility : 1);

const visibleIn = (frames, indexes, minVisibility) =>
  frames.every((lm) => indexes.every((i) => lm[i] && vis(lm[i]) >= minVisibility));

function requireCriteria(c) {
  // ⚠️ 새로 추가된 키까지 본다(min_seg_ratio 2026-08-14, min_ref_coverage 2026-08-16).
  //    옛 서버가 내려준 criteria 를 그대로 쓰면 해당 컷이 조용히 꺼진다.
  //    시끄럽게 죽는 게 낫다.
  if (!c || typeof c.tol_deg !== "number" || typeof c.min_seg_ratio !== "number"
      || typeof c.min_ref_coverage !== "number") {
    throw new Error(
      "criteria 가 필요합니다. GET /api/v1/pose-criteria 로 받아서 넘기세요. " +
        "(임계값을 프론트에 하드코딩하면 서버 조정 시 어긋납니다)"
    );
  }
  return c;
}

// --------------------------------------------------------------------------
// P — 자세 유사도 (0~100)
// --------------------------------------------------------------------------

/**
 * @returns {{score:number, reason:string|null, usedAngles:number, diffs:object}}
 *
 * reason 이 null 이 아니면 score 는 0 이다.
 *   NOT_ENOUGH_JOINTS — 양쪽 모두에서 보이는 각도가 부족해 판정 자체가 불가
 *   REF_PARTS_MISSING — 레퍼런스에 나온 부위가 사용자 사진에 충분히 없음
 *   JOINT_TOO_FAR     — 한 관절이라도 hard_tol_deg 를 넘음
 *
 * ⚠️ 평균만 쓰면 왼팔이 완전히 다른 방향인데 나머지가 맞아 통과한다 —
 *    그러면 왼팔 진단만 조용히 틀린다.
 *    최솟값만 쓰면 손목이 살짝 흔들린 것으로 전체가 떨어진다.
 *    그래서 평균으로 점수를 내고 하드 상한으로 파국만 막는다.
 *
 * ⚠️ **카메라 쪽으로 뻗은(단축된) 세그먼트는 뺀다.** 팔이 렌즈를 향하면 투영
 *    길이가 짧아지고, 그 2D 각도는 사실상 난수다 — 자세가 같아도 hard_tol_deg
 *    를 넘겨 전체를 0점으로 만들었다. "육안으로 같은데 안 찍히는" 제1 원인.
 *    투영 길이 < 몸통 길이 × min_seg_ratio 면 그 세그먼트는 없는 것으로 본다.
 */
export function poseScore(ref, user, criteria) {
  const c = requireCriteria(criteria);
  const diffs = {};

  const tRef = torsoLength(ref, c.min_visibility);
  const tUser = torsoLength(user, c.min_visibility);
  const segLen = (lm, a, b) => Math.hypot(lm[b].x - lm[a].x, lm[b].y - lm[a].y);
  // ⚠️ 몸통을 잴 수 없으면(0) 컷을 끈다 — 기준 없이 자르면 전부 잘려나간다.
  const foreshortened = (lm, a, b, torso) =>
    torso > 0 && segLen(lm, a, b) < torso * c.min_seg_ratio;

  for (const [name, a, b] of SEGMENTS) {
    if (!visibleIn([ref, user], [a, b], c.min_visibility)) continue;
    // ⚠️ **한쪽에서만 단축이어도 뺀다.** 각도 비교는 양쪽이 다 믿을 만해야 성립한다.
    if (foreshortened(ref, a, b, tRef) || foreshortened(user, a, b, tUser)) continue;
    diffs[name] = angleDiff(angleOf(ref[a], ref[b]), angleOf(user[a], user[b]));
  }

  // ⚠️ 몸통도 **보일 때만** 넣는다. 예전 참고 구현은 무조건 넣고 있었는데,
  //    골반이 가려지면 좌표가 추측값이라 엉뚱한 각도가 평균에 섞인다.
  if (visibleIn([ref, user], TORSO_POINTS, c.min_visibility)) {
    const torso = (lm) =>
      angleOf(mid(lm[IDX.shoulderL], lm[IDX.shoulderR]), mid(lm[IDX.hipL], lm[IDX.hipR]));
    diffs.torso = angleDiff(torso(ref), torso(user));
  }

  const values = Object.values(diffs);
  const usedAngles = values.length;

  if (usedAngles < c.min_visible_angles) {
    return { score: 0, reason: "NOT_ENOUGH_JOINTS", usedAngles, diffs };
  }

  // ⚠️ 커버리지 — **레퍼런스에서 보이는 세그먼트는 사용자 사진에도 있어야 한다**
  //    (2026-08-16, 실측 리포트: 전신 레퍼런스 대비 상반신만 잡힌 사진이 고득점
  //    통과했다. 안 보이는 세그먼트는 "제외"만 되고, 남은 각도끼리 비슷하면
  //    점수가 높게 나온다 — 비교 자체가 성립 안 하는 사진인데도).
  //
  //  ⚠️ 방향은 비대칭이다. 사용자가 레퍼런스를 덮어야 하고, 반대는 요구하지
  //     않는다 — 상체 레퍼런스에 전신 사용자 사진은 정상이다.
  //  ⚠️ visibility 만 본다. 단축(foreshortening) 세그먼트를 빠진 것으로 치면
  //     카메라 쪽으로 팔을 뻗은 정상 사진이 반려된다 — 각도만 못 잴 뿐
  //     부위는 사진에 있다. 단축 제외를 만든 이유를 그대로 되살리는 실수다.
  const refVisible = SEGMENTS.filter(([, a, b]) => visibleIn([ref], [a, b], c.min_visibility));
  if (refVisible.length > 0) {
    const covered = refVisible.filter(([, a, b]) => visibleIn([user], [a, b], c.min_visibility));
    if (covered.length / refVisible.length < c.min_ref_coverage) {
      return { score: 0, reason: "REF_PARTS_MISSING", usedAngles, diffs };
    }
  }

  if (values.some((d) => d > c.hard_tol_deg)) {
    return { score: 0, reason: "JOINT_TOO_FAR", usedAngles, diffs };
  }

  const mean = values.reduce((s, d) => s + Math.max(0, 1 - d / c.tol_deg), 0) / usedAngles;
  return { score: Math.round(mean * 1000) / 10, reason: null, usedAngles, diffs };
}

// --------------------------------------------------------------------------
// F — 프레이밍 일치 (0~1)
// --------------------------------------------------------------------------

/** 몸통 길이 — 어깨 중점에서 골반 중점까지. 팔다리를 어떻게 벌리든 변하지 않는다. */
function torsoLength(lm, minVisibility) {
  const need = TORSO_POINTS;
  if (!need.every((i) => lm[i] && vis(lm[i]) >= minVisibility)) return 0;
  const s = mid(lm[IDX.shoulderL], lm[IDX.shoulderR]);
  const h = mid(lm[IDX.hipL], lm[IDX.hipR]);
  return Math.hypot(s.x - h.x, s.y - h.y);
}

/**
 * 두 사진에서 **인물이 비슷한 크기로 담겼는가**. 1 이면 같은 거리에서 찍은 것.
 *
 *   F = min(비율, 1/비율)      비율 = 사용자 몸통 길이 / 레퍼런스 몸통 길이
 *
 * ⚠️ **예전에는 랜드마크 전체의 사각형 IoU 였다. 그건 틀린 값이었다.**
 *    팔다리를 움직이면 사각형이 같이 변해서, **자세가 다른 것을 프레이밍 문제로
 *    보고했다.** 실측 — 전신을 균일하게 16° 이상 틀면 프레이밍이 자세보다 먼저
 *    걸린다. 그러면 사용자에게 "몸이 화면에 다 나오도록 서주세요"가 뜨는데,
 *    실제로 고쳐야 할 건 자세다. 아무리 뒤로 물러나도 통과하지 못한다.
 *
 * ⚠️ 위치는 보지 않는다. 사용자가 화면 왼쪽에 서든 오른쪽에 서든 굵기 비교에는
 *    아무 영향이 없다 — 부위 굵기를 몸통 길이로 정규화하기 때문이다.
 *    실측에서 옆으로 6%만 움직여도 옛 IoU 는 0.58 로 떨어져 거부됐다.
 *
 * 무엇을 막는가 — 레퍼런스는 전신인데 사용자는 바짝 붙어 찍은 경우.
 * 실측: 2배로 다가서면(상반신만 담김) 0.50 이라 기준(0.65)에서 걸린다.
 *
 * ⚠️ 어깨나 골반이 안 보이면 0 을 돌려준다. 잴 기준이 없다는 뜻이고,
 *    그때는 "몸이 화면에 다 나오도록"이 맞는 안내다.
 */
export function framingScore(ref, user, criteria) {
  const c = requireCriteria(criteria);
  const a = torsoLength(ref, c.min_visibility);
  const b = torsoLength(user, c.min_visibility);
  if (a <= 0 || b <= 0) return 0;
  const ratio = b / a;
  return Math.min(ratio, 1 / ratio);
}

// --------------------------------------------------------------------------
// R — 몸통 방향 차이 (0~. ⚠️ 1 을 넘을 수 있다)
//
// ⚠️ **2026-08-14 부터 관찰용이다. 판정에 쓰지 않는다** (서버도 같이 뺐다 —
//    app/services/pose.py). 어깨폭/몸통길이 비율이 잡음에 민감해 육안으로
//    비슷한 자세를 반려했다. 값은 계속 계산·표시·저장한다 — "돌아간 사진이
//    진단을 실제로 얼마나 망치나"가 데이터로 쌓이면 관문을 되살릴 수 있다.
// --------------------------------------------------------------------------

/**
 * 어깨폭 ÷ 몸통길이 비율이 레퍼런스와 얼마나 다른가.
 *
 * ⚠️ **"정면인가"를 재는 게 아니다.** 레퍼런스와의 **차이**다.
 *    레퍼런스가 측면이면 사용자도 측면일 때 0 이 나온다. 절대 기준이 없다.
 *    문구에 "정면"을 쓰면 측면 레퍼런스를 쓰는 사용자가 올바르게 섰는데도
 *    따라도 통과 못 하는 안내를 받게 된다.
 *
 * ⚠️ 각도만으로는 몸이 돌아간 것을 못 잡는다. 팔을 내린 자세는 옆으로 돌아도
 *    팔다리 각도가 거의 같다. 그런데 어깨폭이 좁아 보여 실루엣 굵기가 왜곡된다.
 *
 * 몸통 길이로 나누므로 촬영 거리와 사람 크기에 영향받지 않는다.
 */
/**
 * 어깨폭 ÷ 몸통길이. 몸이 돌아갈수록 작아진다 (정면 대비 cos θ 배).
 *
 * ⚠️ 이 값 자체는 절대적인 의미가 없다. 화면비에 따라 배율이 달라지기 때문이다
 *    (x 는 너비로, y 는 높이로 각각 나눈 좌표다). 두 사진을 **서로 비교할 때만**
 *    쓴다 — 같은 배율이 양쪽에 걸려 나눗셈에서 상쇄된다.
 */
export function shoulderRatio(lm) {
  const sl = lm[IDX.shoulderL];
  const sr = lm[IDX.shoulderR];
  const hl = lm[IDX.hipL];
  const hr = lm[IDX.hipR];
  if (!sl || !sr || !hl || !hr) return 0;

  const shoulderWidth = Math.hypot(sl.x - sr.x, sl.y - sr.y);
  const s = mid(sl, sr);
  const h = mid(hl, hr);
  const torsoLen = Math.hypot(s.x - h.x, s.y - h.y);
  return torsoLen > 0 ? shoulderWidth / torsoLen : 0;
}

/**
 * 몸이 **어느 쪽으로** 돌았는지가 확실한가, 확실하면 어느 쪽인가.
 *
 * ⚠️ 어깨폭 비율만으로는 좌우를 구분할 수 없다. 비율이 cos θ 에 비례하는데
 *    **cos 는 좌우 대칭**이라 +30° 와 −30° 가 같은 값이 된다. 실측 —
 *
 *        레퍼런스 +45°, 사용자 −45°  →  facing_delta 0.000  →  통과
 *
 *    **90° 어긋난 사람이 만점으로 통과한다.** 그 사진으로 낸 부위 비교는 틀린다.
 *
 * 그래서 깊이(z)로 **어느 어깨가 카메라에 가까운지**를 본다.
 *
 * ⚠️ **MediaPipe 의 z 는 정밀도가 낮다.** 그래서 값으로 쓰지 않고 **부호만**,
 *    그것도 **확실할 때만** 쓴다. 어중간하면 "모르겠다"로 두고 기존 계산을 그대로 둔다.
 *    z 를 각도로 환산해서 쓰면(atan) 매 프레임 흔들려 점수가 튄다.
 *
 * ⚠️ DEADBAND=0.25 는 회전 약 14° 에 해당한다(spread ≈ tan θ). 이보다 덜 돌면
 *    부호를 안 믿는다. 그래도 손해가 없다 — 양쪽이 14° 씩 반대로 돌아야 28° 차이인데,
 *    그건 지금도 통과하는 범위다(0° vs 30° 가 0.134 로 통과).
 *
 * ⚠️ z 를 안 주는 구현이면 0(모르겠다)을 돌려준다 → 기존 동작 그대로.
 */
const TURN_DEADBAND = 0.25;

function turnSign(lm) {
  const sl = lm[IDX.shoulderL];
  const sr = lm[IDX.shoulderR];
  if (typeof sl?.z !== "number" || typeof sr?.z !== "number") return 0;

  const width = Math.hypot(sl.x - sr.x, sl.y - sr.y);
  if (!(width > 0)) return 0;

  // z 는 작을수록 카메라에 가깝다. 깊이 차를 어깨폭으로 나눠 크기에 무관하게 만든다.
  const spread = (sl.z - sr.z) / width;
  if (Math.abs(spread) < TURN_DEADBAND) return 0; // 모르겠다
  return spread < 0 ? 1 : -1;
}

export function facingDelta(ref, user) {
  const r = shoulderRatio(ref);
  if (r <= 0) return 0; // 잴 수 없으면 통과 쪽으로 (서버도 같은 판단)
  const u = shoulderRatio(user);

  // ⚠️ **반대쪽으로 돈 게 확실할 때만** 더한다. 빼면 서로 상쇄돼 0 이 된다.
  //    둘 다 확실히 돌았고(0 이 아니고) 방향이 다를 때만 걸리므로,
  //    정면 근처에서는 이 가지로 들어오지 않는다 — 점수가 튀지 않는다.
  const sr = turnSign(ref);
  const su = turnSign(user);
  if (sr !== 0 && su !== 0 && sr !== su) return (u + r) / r;

  return Math.abs(u - r) / r;
}

// --------------------------------------------------------------------------
// K — OKS-inspired 자세 유사도 · 0~1
//
// ⚠️ **COCO OKS 를 그대로 쓴 게 아니다.** COCO OKS 는 같은 이미지 안에서
//    정답과 검출을 견주는 구조인데, 우리는 **다른 사진 두 장**을 견준다.
//    관절 정의(BlazePose≠COCO)도, 크기 기준(몸통≠세그 면적)도 다르다.
//    말할 수 있는 선은 **"OKS 의 관절별 오차 정규화 개념을 차용했다"** 까지다.
// --------------------------------------------------------------------------

/**
 * MediaPipe 33개 랜드마크 ↔ COCO 17개 keypoint 대응표.
 *
 * COCO 순서(pycocotools 와 같다):
 *   nose, eyeL, eyeR, earL, earR, shoulderL/R, elbowL/R, wristL/R,
 *   hipL/R, kneeL/R, ankleL/R
 *
 * ⚠️ MediaPipe 는 눈을 3점(inner/center/outer)으로 찍는다. COCO 의 "eye" 에
 *    해당하는 건 가운데(2, 5)다. inner(1,4)나 outer(3,6)를 쓰면 미묘하게
 *    어긋나는데 **에러가 안 난다.**
 *
 * ⚠️ 대응이 되는 것과 **정의가 같은 것은 다르다.** BlazePose 는 어깨·골반을
 *    GHUM 기준으로 찍고, COCO 는 라벨러가 눈으로 찍는다. 위치가 체계적으로
 *    조금씩 다르다. 그래서 아래 σ 를 최종값으로 쓰면 안 된다.
 */
export const COCO_FROM_MEDIAPIPE = {
  nose: 0,
  eyeL: 2, eyeR: 5,
  earL: 7, earR: 8,
  shoulderL: 11, shoulderR: 12,
  elbowL: 13, elbowR: 14,
  wristL: 15, wristR: 16,
  hipL: 23, hipR: 24,
  kneeL: 25, kneeR: 26,
  ankleL: 27, ankleR: 28,
};

/** COCO 17개 전부. 얼굴 포함. */
export const COCO_17 = Object.values(COCO_FROM_MEDIAPIPE);

/**
 * 얼굴 5개를 뺀 12개. **기본값은 이쪽이다.**
 *
 * ⚠️ 이전 주석에 "얼굴 σ 가 작아 점수를 지배한다"고 적었는데 **틀렸다.**
 *    실측 — 고개를 60° 돌려도 17개 점수는 0.949 다. 지배하지 않는다.
 *
 * 실제 이유는 반대다. **얼굴이 몸의 신호를 희석한다.**
 *    왼팔 35° 차이:  17개 0.912  /  12개 0.876
 *    얼굴 5점은 몸이 어떻든 거의 1.0 이라 평균을 끌어올린다.
 *    우리가 잡아야 할 건 몸의 차이인데 그게 묽어진다.
 *
 * ⚠️ 다만 **차이가 크지 않다**(0.037). COCO_17 로 바꿔도 크게 안 달라진다 —
 *    그래서 인자로 넘길 수 있게 뒀다. "얼굴을 빼야만 동작한다"는 아니다.
 */
export const COCO_BODY_12 = [
  COCO_FROM_MEDIAPIPE.shoulderL, COCO_FROM_MEDIAPIPE.shoulderR,
  COCO_FROM_MEDIAPIPE.elbowL, COCO_FROM_MEDIAPIPE.elbowR,
  COCO_FROM_MEDIAPIPE.wristL, COCO_FROM_MEDIAPIPE.wristR,
  COCO_FROM_MEDIAPIPE.hipL, COCO_FROM_MEDIAPIPE.hipR,
  COCO_FROM_MEDIAPIPE.kneeL, COCO_FROM_MEDIAPIPE.kneeR,
  COCO_FROM_MEDIAPIPE.ankleL, COCO_FROM_MEDIAPIPE.ankleR,
];

/**
 * 관절별 편차 σ — **COCO 값을 임시로 넣어둔 것이다. 우리 값이 아니다.**
 *
 * 출처: pycocotools `cocoeval.py` kpt_oks_sigmas
 *   [.26,.25,.25,.35,.35,.79,.79,.72,.72,.62,.62,1.07,1.07,.87,.87,.89,.89]/10
 *
 * σ 가 뜻하는 것 — 같은 사진을 여러 사람에게 라벨 붙이게 했을 때 어긋난 정도.
 * 손목(0.062)은 사람끼리 잘 맞고 골반(0.107)은 잘 안 맞는다.
 *
 * ⚠️ **σ 는 "관절 중요도"가 아니다.** "이 관절이 진단에 더 중요하다"가 아니라
 *    "이 관절은 원래 위치를 잘 못 짚는다"는 뜻이다. 우리가 실제로 원하는
 *    부위별 중요도는 **downstream(진단하는 9부위)에서 와야 하고**, 그건 다른 축이다.
 *    지금은 그 둘이 구분 없이 섞여 있다.
 *
 * ⚠️⚠️ **최종본으로 쓰면 안 된다. 두 가지가 어긋나 있다.**
 *
 *   1. **관절 정의** — BlazePose 어깨·골반 ≠ COCO 어깨·골반
 *   2. **크기 기준** — COCO σ 는 √(세그멘테이션 면적) 기준이다.
 *      우리는 몸통 길이 기준이라 눈금이 다르다 (SCALE_FROM_TORSO 참고)
 *
 * σ 는 원래 **데이터셋마다 구하는 상수**다(COCO 문서에 명시돼 있다).
 * ⚠️ **30초 jitter 를 σ 라고 부르면 안 된다.** jitter 는 한 사람·한 자세·한 조명의
 *    **시간적 흔들림**이고, σ 는 데이터 분포 전체에 걸친 **위치 불확실성**이다.
 *    jitter 는 σ 의 **하한**이지 σ 가 아니다. (앞서 "같은 실험"이라고 적었는데 틀렸다)
 *
 * 그래도 재는 것이 출발점은 된다 —
 *
 *      σᵢ = √( 평균( |pᵢ − p̄ᵢ|² ) / 2 )        정규화 좌표에서
 *
 * (2 로 나누는 건 2D 라 축마다 σ 면 거리제곱의 기댓값이 2σ² 이기 때문)
 *
 * 그 값이 나오면 위의 어긋남 두 가지가 **동시에 사라진다** — 우리 검출기,
 * 우리 관절 정의, 우리 크기 기준으로 잰 값이 되기 때문이다.
 * 그리고 우리에게는 그쪽이 더 맞다: 우리 서비스에 사람 라벨러는 없고,
 * 실제 잡음원은 MediaPipe 흔들림과 사용자의 미세한 움직임이다.
 */
export const KEYPOINT_SIGMA = {
  [COCO_FROM_MEDIAPIPE.nose]: 0.026,
  [COCO_FROM_MEDIAPIPE.eyeL]: 0.025, [COCO_FROM_MEDIAPIPE.eyeR]: 0.025,
  [COCO_FROM_MEDIAPIPE.earL]: 0.035, [COCO_FROM_MEDIAPIPE.earR]: 0.035,
  [COCO_FROM_MEDIAPIPE.shoulderL]: 0.079, [COCO_FROM_MEDIAPIPE.shoulderR]: 0.079,
  [COCO_FROM_MEDIAPIPE.elbowL]: 0.072, [COCO_FROM_MEDIAPIPE.elbowR]: 0.072,
  [COCO_FROM_MEDIAPIPE.wristL]: 0.062, [COCO_FROM_MEDIAPIPE.wristR]: 0.062,
  [COCO_FROM_MEDIAPIPE.hipL]: 0.107, [COCO_FROM_MEDIAPIPE.hipR]: 0.107,
  [COCO_FROM_MEDIAPIPE.kneeL]: 0.087, [COCO_FROM_MEDIAPIPE.kneeR]: 0.087,
  [COCO_FROM_MEDIAPIPE.ankleL]: 0.089, [COCO_FROM_MEDIAPIPE.ankleR]: 0.089,
};

/** 뒤집은 표 — 화면에 관절별 점수를 이름으로 보여줄 때 쓴다. */
const COCO_NAME_OF = Object.fromEntries(
  Object.entries(COCO_FROM_MEDIAPIPE).map(([k, v]) => [v, k])
);

/**
 * 몸통 길이를 √(세그멘테이션 면적) 눈금에 맞추는 배율.
 *
 * COCO 의 s 는 사람 세그멘테이션 면적의 제곱근이다. 우리는 자세 관문 시점에
 * 세그멘테이션이 없으므로(그건 업로드 후 GPU 에서 난다) 몸통 길이를 쓴다.
 * 서 있는 사람 기준 √(면적) ≈ 몸통 × 1.56 이라 그 값을 넣었다.
 *
 * ⚠️ **임시 눈금 맞춤이다.** 우리 σ 를 직접 재면 이 배율이 σ 안으로
 *    흡수되어 사라진다. 그때 이 상수는 1 로 두면 된다.
 */
export const SCALE_FROM_TORSO = 1.56;

/**
 * 자세 하나를 **위치·크기를 지운 좌표**로 바꾼다.
 *
 * COCO 의 OKS 는 같은 사진 안에서 정답과 예측을 견주므로 좌표계가 이미 같다.
 * 우리는 **다른 사진 두 장**을 견주므로 먼저 맞춰놓아야 한다.
 *
 *   중심 = 어깨 중점과 골반 중점의 가운데
 *   크기 = 몸통 길이 × SCALE_FROM_TORSO
 *
 * ⚠️⚠️ **크기 기준을 bbox 로 잡으면 안 된다. 실측으로 확인한 함정이다.**
 *    팔 하나만 20° 움직여도 사각형이 커지고 중심이 밀린다 —
 *
 *        s  0.390 → 0.455        중심x  0.500 → 0.464
 *
 *    그러면 **가만히 있던 나머지 관절까지 전부 어긋난 것으로 계산된다.**
 *    같은 자세(왼팔만 20°)를 두 방식으로 재면
 *
 *        bbox 기준   0.595      ← 팔 하나 움직였는데 "거의 딴 자세"
 *        몸통 기준   0.946      ← 실제로 어긋난 만큼만
 *
 *    몸통 길이는 팔다리를 어떻게 벌리든 변하지 않는다. framingScore 가
 *    같은 이유로 이미 몸통 길이를 쓰고 있다 — 기준을 맞춘 것이다.
 *
 * ⚠️ **회전은 맞추지 않는다.** Procrustes 처럼 돌려서 겹치면 몸이 돌아간 것과
 *    기울어진 것이 지워진다 — 그건 FACING 이 **일부러 잡는** 신호다.
 *    위치와 크기만 지운다. (그래서 FRAMING 도 별도 관문으로 남는다)
 *
 * ⚠️ aspect — MediaPipe 좌표는 x 를 너비로, y 를 높이로 각각 나눈 값이라
 *    화면비가 1 이 아니면 x·y 의 눈금이 다르다. 거리를 재는 계산에서는
 *    이걸 안 맞추면 조용히 틀린다. 화면비(가로/세로)를 넘겨야 한다.
 */
function normalizeForOks(lm, indexes, aspect) {
  const at = (i) => ({ x: lm[i].x * aspect, y: lm[i].y });
  const sh = mid(at(IDX.shoulderL), at(IDX.shoulderR));
  const hp = mid(at(IDX.hipL), at(IDX.hipR));

  const torso = Math.hypot(sh.x - hp.x, sh.y - hp.y);
  // ⚠️ 0 이면 나눌 수 없다. 그냥 두면 NaN 이 되어 점수가 늘 0 으로 나온다.
  if (!(torso > 0)) return null;

  const s = torso * SCALE_FROM_TORSO;
  const cx = (sh.x + hp.x) / 2;
  const cy = (sh.y + hp.y) / 2;
  return indexes.map((i) => {
    const p = at(i);
    return { x: (p.x - cx) / s, y: (p.y - cy) / s };
  });
}

/**
 * 두 자세가 얼마나 같은가를 **관절별 편차로 나눠서** 잰다.
 *
 *   OKS = 평균  exp( −d² / (2 · (2σ)²) )
 *
 * d 는 위치·크기를 지운 좌표에서의 관절 간 거리, σ 는 그 관절의 편차다.
 * **d 가 2σ 만큼 벌어지면 그 관절 점수는 약 0.61 이 된다.**
 * 1 에 가까울수록 같은 자세, 0 에 가까울수록 다른 자세다.
 *
 * 왜 이걸 쓰는가 — poseScore 는 각도 차이를 TOL=45° 로 나눈다. 45 에 근거가
 * 없고, 관절을 전부 똑같이 취급한다. OKS 는 **관절마다 다르게 취급하고,
 * 그 차등이 측정된 숫자에서 온다.**
 *
 * ⚠️ **아직 이걸로 막지 않는다.** poseScore 와 나란히 계산해서 비교만 한다.
 *    σ 를 우리 값으로 재기 전에 관문을 갈아끼우면 나아졌는지 알 방법이 없다.
 *
 * ⚠️ **이 값 하나로 F·R 을 대신할 수 없다.** 위 정규화가 크기와 위치를
 *    **지워버리기 때문**이다. "너무 가까이 서 있다"를 OKS 는 원리상 못 잡는다.
 *
 * @param opts.keypoints  기본 COCO_BODY_12. 얼굴까지 보려면 COCO_17
 * @param opts.refAspect  레퍼런스 사진의 가로/세로
 * @param opts.userAspect 사용자 화면의 가로/세로
 * @returns {{oks:number, reason:string|null, used:number, per:object}}
 */
export function oksScore(
  ref,
  user,
  criteria,
  { keypoints = COCO_BODY_12, refAspect = 1, userAspect = 1 } = {}
) {
  const c = requireCriteria(criteria);

  const used = keypoints.filter((i) => visibleIn([ref, user], [i], c.min_visibility));
  if (used.length < c.min_visible_angles) {
    return { oks: 0, reason: "NOT_ENOUGH_JOINTS", used: used.length, per: {} };
  }

  // ⚠️ 정규화 기준(어깨·골반)이 안 보이면 크기를 잴 수 없다. 위의 visible
  //    검사와 별개다 — 무릎·발목만 보여도 used 는 채워지기 때문이다.
  const A = normalizeForOks(ref, used, refAspect);
  const B = normalizeForOks(user, used, userAspect);
  if (!A || !B) {
    return { oks: 0, reason: "NOT_ENOUGH_JOINTS", used: used.length, per: {} };
  }

  const per = {};
  let sum = 0;

  used.forEach((idx, k) => {
    const d2 = (A[k].x - B[k].x) ** 2 + (A[k].y - B[k].y) ** 2;
    const kappa = 2 * KEYPOINT_SIGMA[idx];
    const v = Math.exp(-d2 / (2 * kappa * kappa));
    per[COCO_NAME_OF[idx] ?? idx] = Math.round(v * 1000) / 1000;
    sum += v;
  });

  return {
    oks: Math.round((sum / used.length) * 1000) / 1000,
    reason: null,
    used: used.length,
    per,
  };
}

// --------------------------------------------------------------------------
// 한 번에 — 화면 표시용
// --------------------------------------------------------------------------

/** 사용자에게 그대로 보여줘도 되는 문구. 서버 문구와 뜻을 맞춰 둔다.
 *  ⚠️ export 해 둔 이유 — 화면 톤에 맞게 갈아끼울 수 있게. evaluate() 가
 *     이 표를 그대로 읽으므로, 바꾸면 message 도 같이 바뀐다. */
export const MESSAGES = {
  MULTI_PERSON: "혼자 나오도록 촬영해주세요.",
  NOT_ENOUGH_JOINTS: "전신이 보이도록 서주세요.",
  REF_PARTS_MISSING: "레퍼런스에 나온 부위가 모두 보이도록 서주세요.",
  FRAMING: "레퍼런스와 촬영 거리가 너무 다릅니다.",
  //: 유도용 — 셔터도 업로드도 막지 않는다. 문구만 띄운다.
  TOO_CLOSE: "조금 뒤로 물러나 주세요.",
  TOO_FAR: "조금 앞으로 와 주세요.",
  // FACING 은 2026-08-14 에 뺐다 — R 은 관찰용이라 이 문구가 나올 경로가 없다.
  POSE: "레퍼런스와 포즈를 맞춰주세요.",
  OK: "좋습니다. 그대로 유지해주세요.",
};

/**
 * 세 값을 계산하고 통과 여부까지 판단한다.
 *
 * 두 가지를 따로 돌려준다.
 *   pass    — **자동 촬영 조건.** 서버가 거부하지 않는 상태와 같다 (= !blocked)
 *   blocked — **서버가 거부하는가.** 갤러리 업로드 경로에서 이걸 본다
 *
 * ⚠️ 거리 유도(f_min)는 **문구만 낸다. 셔터를 막지 않는다** (2026-08-14).
 *    거리 차이는 몸통 길이 정규화로 상쇄되므로 f_min 미달은 고쳐도 이득이 없다 —
 *    서버가 애초에 그 이유로 f_hard 로만 막는데, 셔터만 f_min 을 요구하면
 *    "업로드는 되는데 자동 촬영은 안 되는" 사진을 만든다. 좁은 방에서는
 *    f_min 을 영영 못 넘기도 한다(뒤로 가면 작아지고, 앞으로 오면 발이 잘린다).
 *
 * ⚠️ **판단 결과를 서버로 보내지 않는다.** 서버에는 값만 보내고 판정은
 *    서버가 다시 한다. 이건 화면에 실시간으로 보여주기 위한 것이다.
 *
 * ⚠️ blocked 판정 순서가 서버(app/services/pose.py judge_user_photo)와 같아야 한다.
 *    다르면 화면에서 통과인데 업로드가 거부되는 상황이 생긴다.
 *      여러 명 → 거리(f_hard) → 자세      (정면성 R 은 2026-08-14 에 뺐다)
 *
 * ⚠️ reason 이 NOT_ENOUGH_JOINTS 면 **업로드하지 마세요.** 서버는 숫자만 받아서
 *    "포즈를 맞춰주세요"라고 답하는데, 실제 문제는 몸이 안 보이는 것이라
 *    사용자가 엉뚱한 걸 고치게 된다.
 */
export function evaluate(
  ref,
  user,
  criteria,
  { multiPerson = false, refAspect = 1, userAspect = 1 } = {}
) {
  const c = requireCriteria(criteria);

  const pose = poseScore(ref, user, c);
  const framing = framingScore(ref, user, c);
  const facing = facingDelta(ref, user);
  // ⚠️ **관찰만 한다.** 아래 판정에 쓰지 않는다 — 잡음을 재기 전에 관문을
  //    갈아끼우면 나아졌는지 나빠졌는지 판단할 기준이 없다.
  const oks = oksScore(ref, user, c, { refAspect, userAspect });
  const torsoRatio =
    torsoLength(user, c.min_visibility) / (torsoLength(ref, c.min_visibility) || 1);

  const values = {
    pose_similarity: pose.score,
    framing_score: Math.round(framing * 1000) / 1000,
    facing_delta: Math.round(facing * 1000) / 1000,
  };

  // ── 서버가 실제로 막는 조건 (app/services/pose.py 와 같은 순서·같은 기준) ──
  // ⚠️ 정면성(R) 관문은 여기 있었다 — 2026-08-14 에 서버와 함께 뺐다(위 R 절 참고).
  let blockReason = null;
  if (multiPerson) blockReason = "MULTI_PERSON";
  else if (pose.reason === "NOT_ENOUGH_JOINTS") blockReason = "NOT_ENOUGH_JOINTS";
  // ⚠️ 커버리지 미달도 FRAMING 보다 먼저 — 부위가 화면 밖이면 거리 안내("물러나라")가
  //    아니라 "그 부위가 나오게 서라"가 고칠 수 있는 안내다. 서버는 이 사유를
  //    모른다(숫자만 받는다) — NOT_ENOUGH_JOINTS 처럼 이 상태로는 업로드하지 않는다.
  else if (pose.reason === "REF_PARTS_MISSING") blockReason = "REF_PARTS_MISSING";
  else if (framing < c.f_hard) blockReason = "FRAMING";
  else if (pose.score < c.threshold) blockReason = "POSE";

  // ── 촬영 화면 안내 문구 (셔터도 업로드도 막지 않는다) ──
  // ⚠️ 거리는 f_min 에서 **안내만** 한다. 부위 굵기를 몸통 길이로 나눠 비교하므로
  //    거리 차이는 계산에서 상쇄된다 — 막으면 고쳐도 이득이 없는 이유로
  //    사용자를 돌려보내게 된다. 실제 거부선은 f_hard 다.
  let guideReason = blockReason;
  if (guideReason === null && framing < c.f_min) {
    guideReason = torsoRatio > 1 ? "TOO_CLOSE" : "TOO_FAR";
  }

  return {
    ...values,
    /** 자동 촬영 조건. 서버가 거부하지 않으면 찍는다 — 거리 유도는 문구만. */
    pass: blockReason === null,
    /** 이 상태로 업로드하면 서버가 거부하는가. 갤러리 업로드 경로에서 쓴다. */
    blocked: blockReason !== null,
    reason: guideReason,
    blockReason,
    message: MESSAGES[guideReason ?? "OK"],
    /** OKS — 관찰용. 판정에 쓰지 않는다. 서버로도 보내지 않는다. */
    oks: oks.oks,
    detail: {
      usedAngles: pose.usedAngles,
      diffs: pose.diffs,
      poseReason: pose.reason,
      torsoRatio: Math.round(torsoRatio * 100) / 100,
      oksUsed: oks.used,
      oksPer: oks.per,
    },
  };
}

// --------------------------------------------------------------------------
// 거울 프리뷰 보정 — 실시간 촬영 화면 전용 (2026-08-16 결정)
//
// 셀피 미리보기는 거울(좌우반전)인데 레퍼런스는 원본으로 보여준다. 그래서
// 사용자가 화면에 **보이는 대로** 따라 하면 해부학적으로는 좌우가 반전된
// 자세가 된다 — 그게 자연스러운 행동이므로 그 방향이 정답이어야 한다.
//
// 규칙은 화면당 하나다. **판정 방향 = 프리뷰 방향.**
//
//   실시간 촬영(거울 프리뷰):  evaluate(mirrorLandmarks(ref), user, ...)
//   갤러리 업로드(프리뷰 없음): evaluate(ref, user, ...)                — 기존 그대로
//
// ⚠️ "양방향 다 계산해서 좋은 쪽 채택"은 기각됐다 — 판정 기준이 사용자마다
//    달라지고, 저장되는 사진의 좌우 방향이 뒤섞여 downstream(좌우 부위 진단)이
//    어느 방향인지 알 수 없게 된다. 한 화면 한 규칙이면 방향이 균일하다.
// ⚠️ **레퍼런스 이미지를 화면에서 뒤집는 방식도 기각됐다.** 사진 속 배경
//    글자가 뒤집혀 보이는 표시 품질 문제. 표시는 원본, 판정만 거울 기준.
// --------------------------------------------------------------------------

/** MediaPipe 33개 중 좌/우 쌍. 0(코) 등 중앙점은 쌍이 없다. */
export const LR_PAIRS = [
  [1, 4], [2, 5], [3, 6], [7, 8], [9, 10],
  [11, 12], [13, 14], [15, 16], [17, 18], [19, 20], [21, 22],
  [23, 24], [25, 26], [27, 28], [29, 30], [31, 32],
];

/**
 * 랜드마크를 좌우반전한다 — x 를 뒤집고 좌/우 이름표를 맞바꾼다.
 * 실시간 촬영 화면에서 **레퍼런스에만** 적용해 판정 기준으로 쓴다.
 *
 * ⚠️ 이름표 스왑 없이 x 만 뒤집으면 안 된다. 왼팔 좌표에 "오른팔" 이름이
 *    남아, 비교가 전부 반대 관절과 붙는다 (서버 unmirror_landmarks 와 같은 원리).
 * ⚠️ z 는 그대로 둔다 — 깊이(카메라와의 거리)는 좌우반전과 무관하다.
 * ⚠️ **사용자 랜드마크와 사진에는 절대 적용하지 않는다.** 업로드는 항상 카메라
 *    원본이다 — 뒤집어 보내면 이름표가 실제 몸과 어긋나 왼팔 진단이 오른팔에
 *    붙는다 (is_mirrored 경로는 "이미 반전 저장된 갤러리 사진" 전용이다).
 */
export function mirrorLandmarks(lm) {
  const out = lm.map((p) => ({ ...p, x: 1 - p.x }));
  for (const [a, b] of LR_PAIRS) {
    const t = out[a];
    out[a] = out[b];
    out[b] = t;
  }
  return out;
}

// --------------------------------------------------------------------------
// 자동 촬영 — 흔들림 방지
// --------------------------------------------------------------------------

/**
 * 최근 프레임 중 n_hold 개가 통과 상태였을 때 true 를 돌려준다.
 *
 * ⚠️ 한 프레임만 보고 셔터를 누르면 손이 지나가다 우연히 맞는 순간에 찍힌다.
 *
 * ⚠️ **"연속 n_hold"가 아니다** (2026-08-14 에 바꿨다). MediaPipe lite 는 프레임이
 *    수시로 튀는데, 연속 조건은 한 프레임만 튀어도 처음부터 다시 세게 했다 —
 *    점수가 통과선 근처면 영영 안 찍혔다. 최근 n_hold+SLACK 프레임 중 n_hold 개
 *    통과로 완화한다. 손이 지나가는 순간(통과가 몇 프레임 안 됨)은 여전히 못 찍는다.
 *
 *   const hold = createHoldGate(criteria);
 *   ...매 프레임...
 *   if (hold(result.pass)) shutter();
 */
export function createHoldGate(criteria) {
  const need = requireCriteria(criteria).n_hold ?? 30;
  // 허용하는 튐 프레임 수 — 창 크기는 need + SLACK.
  // 튐은 프레임당 확률로 생기므로 need 에 비례(30%)해야 한다. 고정 3이면
  // need=30 처럼 긴 유지에서 튐 몇 번에 영영 안 찍히는 문제가 돌아온다.
  const SLACK = Math.ceil(need * 0.3);
  let window = [];

  const gate = (ok) => {
    window.push(ok ? 1 : 0);
    if (window.length > need + SLACK) window.shift();
    const got = window.reduce((s, v) => s + v, 0);
    gate.progress = Math.min(1, got / need);
    if (got >= need) {
      window = []; // 한 번 찍고 나면 다시 쌓는다
      gate.progress = 0;
      return true;
    }
    return false;
  };

  /** 0~1. 화면에 "곧 찍힙니다" 막대를 그릴 때 쓴다. */
  gate.progress = 0;
  gate.n_hold = need;
  return gate;
}

// --------------------------------------------------------------------------
// 서버에서 임계값 받아오기
// --------------------------------------------------------------------------

/**
 * ⚠️ 앱 시작 시 **한 번** 부르고 보관해서 쓴다. 매 프레임 부르면 안 된다.
 * ⚠️ 실패하면 던진다. 기본값으로 대신하지 않는다 — 서버와 다른 기준으로
 *    "통과"를 보여주면 그 뒤 업로드가 전부 거부된다.
 */
export async function fetchCriteria(baseUrl = "/api/v1") {
  const res = await fetch(`${baseUrl}/pose-criteria`);
  if (!res.ok) throw new Error(`판정 기준을 받지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}
