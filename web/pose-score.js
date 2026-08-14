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
export const angleOf = (a, b) => (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;

/**
 * 두 각도의 차이. 항상 0~180 으로 접는다.
 *
 * ⚠️ 접지 않으면 350° 와 10° 가 340° 차이로 나온다. 실제로는 20° 다.
 *    그러면 살짝 흔들린 팔이 하드 상한에 걸려 통째로 탈락한다.
 */
export const angleDiff = (p, q) => {
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
  if (!c || typeof c.tol_deg !== "number") {
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
 *   JOINT_TOO_FAR     — 한 관절이라도 hard_tol_deg 를 넘음
 *
 * ⚠️ 평균만 쓰면 왼팔이 완전히 다른 방향인데 나머지가 맞아 통과한다 —
 *    그러면 왼팔 진단만 조용히 틀린다.
 *    최솟값만 쓰면 손목이 살짝 흔들린 것으로 전체가 떨어진다.
 *    그래서 평균으로 점수를 내고 하드 상한으로 파국만 막는다.
 */
export function poseScore(ref, user, criteria) {
  const c = requireCriteria(criteria);
  const diffs = {};

  for (const [name, a, b] of SEGMENTS) {
    if (!visibleIn([ref, user], [a, b], c.min_visibility)) continue;
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
// R — 정면성 차이 (0~1)
// --------------------------------------------------------------------------

/**
 * 어깨폭 ÷ 몸통길이 비율이 레퍼런스와 얼마나 다른가.
 *
 * ⚠️ 각도만으로는 몸이 돌아간 것을 못 잡는다. 팔을 내린 자세는 옆으로 돌아도
 *    팔다리 각도가 거의 같다. 그런데 어깨폭이 좁아 보여 실루엣 굵기가 왜곡된다.
 *
 * 몸통 길이로 나누므로 촬영 거리와 사람 크기에 영향받지 않는다.
 */
export function facingDelta(ref, user) {
  const ratio = (lm) => {
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
  };

  const r = ratio(ref);
  if (r <= 0) return 0; // 잴 수 없으면 통과 쪽으로 (서버도 같은 판단)
  return Math.abs(ratio(user) - r) / r;
}

// --------------------------------------------------------------------------
// 한 번에 — 화면 표시용
// --------------------------------------------------------------------------

/** 사용자에게 그대로 보여줘도 되는 문구. 서버 문구와 뜻을 맞춰 둔다. */
export const MESSAGES = {
  MULTI_PERSON: "혼자 나오도록 촬영해주세요.",
  NOT_ENOUGH_JOINTS: "전신이 보이도록 서주세요.",
  FRAMING: "레퍼런스와 촬영 거리가 너무 다릅니다.",
  //: 유도용 — 막지는 않는다. 촬영 화면에서만 띄운다.
  TOO_CLOSE: "조금 뒤로 물러나 주세요.",
  TOO_FAR: "조금 앞으로 와 주세요.",
  FACING: "정면을 보고 서주세요.",
  POSE: "레퍼런스와 포즈를 맞춰주세요.",
  OK: "좋습니다. 그대로 유지해주세요.",
};

/**
 * 세 값을 계산하고 통과 여부까지 판단한다.
 *
 * 두 가지를 따로 돌려준다.
 *   pass    — **자동 촬영 조건.** 유도 기준까지 만족했는가 (거리 포함)
 *   blocked — **서버가 거부하는가.** 갤러리 업로드 경로에서 이걸 본다
 *
 * ⚠️ 거리는 둘의 기준이 다르다. 촬영 중에는 f_min 으로 안내해도 공짜지만
 *    (한 걸음 물러나면 된다), 이미 찍힌 사진을 f_min 으로 막으면 처음부터
 *    다시 하라는 뜻이 된다. 게다가 거리 차이는 몸통 길이 정규화로 상쇄되므로
 *    **고쳐도 이득이 없다.** 그래서 거부는 f_hard 로만 한다.
 *
 * ⚠️ **판단 결과를 서버로 보내지 않는다.** 서버에는 세 값만 보내고 판정은
 *    서버가 다시 한다. 이건 화면에 실시간으로 보여주기 위한 것이다.
 *
 * ⚠️ blocked 판정 순서가 서버(app/services/pose.py judge_user_photo)와 같아야 한다.
 *    다르면 화면에서 통과인데 업로드가 거부되는 상황이 생긴다.
 *      여러 명 → 거리(f_hard) → 정면성 → 자세
 *
 * ⚠️ reason 이 NOT_ENOUGH_JOINTS 면 **업로드하지 마세요.** 서버는 숫자만 받아서
 *    "포즈를 맞춰주세요"라고 답하는데, 실제 문제는 몸이 안 보이는 것이라
 *    사용자가 엉뚱한 걸 고치게 된다.
 */
export function evaluate(ref, user, criteria, { multiPerson = false } = {}) {
  const c = requireCriteria(criteria);

  const pose = poseScore(ref, user, c);
  const framing = framingScore(ref, user, c);
  const facing = facingDelta(ref, user);
  const torsoRatio =
    torsoLength(user, c.min_visibility) / (torsoLength(ref, c.min_visibility) || 1);

  const values = {
    pose_similarity: pose.score,
    framing_score: Math.round(framing * 1000) / 1000,
    facing_delta: Math.round(facing * 1000) / 1000,
  };

  // ── 서버가 실제로 막는 조건 (app/services/pose.py 와 같은 순서·같은 기준) ──
  let blockReason = null;
  if (multiPerson) blockReason = "MULTI_PERSON";
  else if (pose.reason === "NOT_ENOUGH_JOINTS") blockReason = "NOT_ENOUGH_JOINTS";
  else if (framing < c.f_hard) blockReason = "FRAMING";
  else if (facing > c.r_max) blockReason = "FACING";
  else if (pose.score < c.threshold) blockReason = "POSE";

  // ── 촬영 화면 유도 (막지는 않는다) ──
  // ⚠️ 거리는 f_min 에서 **안내만** 한다. 부위 굵기를 몸통 길이로 나눠 비교하므로
  //    거리 차이는 계산에서 상쇄된다 — 여기서 막으면 고쳐도 이득이 없는 이유로
  //    사용자를 돌려보내게 된다. 실제 거부선은 f_hard 다.
  let guideReason = blockReason;
  if (guideReason === null && framing < c.f_min) {
    guideReason = torsoRatio > 1 ? "TOO_CLOSE" : "TOO_FAR";
  }

  return {
    ...values,
    /** 자동 촬영 조건. 유도 기준까지 만족했는가. */
    pass: guideReason === null,
    /** 이 상태로 업로드하면 서버가 거부하는가. 갤러리 업로드 경로에서 쓴다. */
    blocked: blockReason !== null,
    reason: guideReason,
    blockReason,
    message: MESSAGES[guideReason ?? "OK"],
    detail: {
      usedAngles: pose.usedAngles,
      diffs: pose.diffs,
      poseReason: pose.reason,
      torsoRatio: Math.round(torsoRatio * 100) / 100,
    },
  };
}

// --------------------------------------------------------------------------
// 자동 촬영 — 흔들림 방지
// --------------------------------------------------------------------------

/**
 * 통과 상태가 연속 n_hold 프레임 이어졌을 때만 true 를 돌려준다.
 *
 * ⚠️ 한 프레임만 보고 셔터를 누르면 손이 지나가다 우연히 맞는 순간에 찍힌다.
 *
 *   const hold = createHoldGate(criteria);
 *   ...매 프레임...
 *   if (hold(result.pass)) shutter();
 */
export function createHoldGate(criteria) {
  const need = requireCriteria(criteria).n_hold ?? 15;
  let streak = 0;
  return (ok) => {
    streak = ok ? streak + 1 : 0;
    if (streak >= need) {
      streak = 0; // 한 번 찍고 나면 다시 쌓는다
      return true;
    }
    return false;
  };
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
