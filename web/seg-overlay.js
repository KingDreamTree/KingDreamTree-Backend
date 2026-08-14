/**
 * 세그멘테이션 오버레이 뷰어 — 부위를 클릭하면 사진 위에 그 부위가 색칠된다.
 *
 * 담당 A (F06 시각화). 의존성 없는 ES 모듈.
 *
 *   import { mountSegOverlay } from "./seg-overlay.js";
 *   mountSegOverlay(container, seg);   // seg = GET /sessions/{id}/segmentation 응답
 *
 * 서버를 부르지 않는다 — 응답 안의 signed URL(사진·맵)과 팔레트만 쓴다.
 * 그래서 어느 화면에든 응답 객체만 넘기면 붙는다 (e2e-test.html 이 첫 사용처).
 *
 * ⚠️ 렌더링 규칙 — docs/work-a.md §6 의 함정들이 그대로 적용된다.
 *    * 이미지에 crossOrigin="anonymous" 필수. 없으면 캔버스가 오염되어
 *      getImageData 가 SecurityError 를 던진다 (서버 CORS 는 이미 열려 있다).
 *    * 맵을 캔버스에 그릴 때 보간 금지(imageSmoothingEnabled=false).
 *      bilinear 가 라벨 값을 섞어 없는 부위가 경계에 생긴다.
 *    * 맵 좌표 ≠ 사진 좌표. x·y 를 각각 photo/map 비율로 늘린다
 *      (app/services/segmap.py scales() 와 같은 규칙).
 */

const OVERLAY_ALPHA = 165; // 0~255. 색은 보이되 몸의 윤곽도 남는 선

//: 마스터에 색이 없는 부위용 (비교 대상이 아닌 클래스는 color_hex 가 null 이다).
//  ⚠️ 이걸 안 받치면 rgb(null) 에서 통째로 죽는다 — 실측으로 잡은 버그.
const FALLBACK_HEX = "#9aa3b2";

/** hex "#RRGGBB" → [r,g,b]. null/이상값이면 회색으로 받친다. */
function rgb(hex) {
  const v = parseInt(String(hex || FALLBACK_HEX).replace("#", ""), 16);
  if (Number.isNaN(v)) return rgb(FALLBACK_HEX);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    // ⚠️ 이 한 줄이 없으면 이미지는 뜨는데 getImageData 에서 SecurityError.
    //    "화면은 되는데 클릭이 안 되는" 상태라 원인을 찾기 어렵다.
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`이미지를 불러오지 못했습니다: ${url.slice(0, 80)}…`));
    img.src = url;
  });
}

/** 맵 PNG → 라벨 배열(Uint8ClampedArray, R 채널). 원 해상도 그대로 읽는다. */
function readLabels(mapImg, w, h) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(mapImg, 0, 0, w, h);
  return ctx.getImageData(0, 0, w, h).data;
}

/** 한 쪽(레퍼런스/사용자)의 그리기 상태를 준비한다. */
async function prepareSide(result, maxHeight) {
  const [photoImg, mapImg] = await Promise.all([
    loadImage(result.photo_url),
    loadImage(result.map_url),
  ]);

  const mapW = result.map_width;
  const mapH = result.map_height;
  const labels = readLabels(mapImg, mapW, mapH);

  // 표시 크기는 **사진의** 비율을 따른다. 맵 비율로 그리면 인물이 눌려 보인다.
  const ch = Math.min(maxHeight, result.photo_height);
  const cw = Math.round((ch * result.photo_width) / result.photo_height);

  const canvas = document.createElement("canvas");
  canvas.width = cw;
  canvas.height = ch;

  // 오버레이는 맵 해상도로 만들어 두고 그릴 때 늘린다 — 픽셀 단위 계산은 한 번만.
  const overlay = document.createElement("canvas");
  overlay.width = mapW;
  overlay.height = mapH;

  const byValue = new Map(result.palette.map((p) => [p.label_value, p]));
  const colors = new Map(result.palette.map((p) => [p.class_name, rgb(p.color_hex)]));

  return {
    canvas,
    ctx: canvas.getContext("2d"),
    overlay,
    overlayCtx: overlay.getContext("2d"),
    photoImg,
    labels,
    mapW,
    mapH,
    cw,
    ch,
    byValue,
    colors,
    present: new Set(result.palette.map((p) => p.class_name)),
  };
}

/** 선택된 부위들만 칠한 오버레이를 다시 만든다 (맵 해상도에서 한 번). */
function rebuildOverlay(side, selected) {
  const image = new ImageData(side.mapW, side.mapH);
  const out = image.data;
  const n = side.mapW * side.mapH;
  for (let i = 0; i < n; i++) {
    const part = side.byValue.get(side.labels[i * 4]);
    if (!part || !selected.has(part.class_name)) continue;
    const color = side.colors.get(part.class_name);
    const o = i * 4;
    out[o] = color[0];
    out[o + 1] = color[1];
    out[o + 2] = color[2];
    out[o + 3] = OVERLAY_ALPHA;
  }
  side.overlayCtx.putImageData(image, 0, 0);
}

function paint(side, selected) {
  const { ctx } = side;
  ctx.imageSmoothingEnabled = true; // 사진은 곱게
  ctx.clearRect(0, 0, side.cw, side.ch);
  ctx.drawImage(side.photoImg, 0, 0, side.cw, side.ch);
  if (selected.size === 0) return;

  rebuildOverlay(side, selected);
  // ⚠️ 오버레이는 라벨에서 왔으므로 보간 금지 — 경계가 섞이면 틀린 부위가 비쳐 보인다.
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(side.overlay, 0, 0, side.cw, side.ch);
}

/** 캔버스 클릭 좌표 → 그 지점의 부위 (없으면 null). */
function partAt(side, event) {
  const rect = side.canvas.getBoundingClientRect();
  const mx = Math.floor(((event.clientX - rect.left) / rect.width) * side.mapW);
  const my = Math.floor(((event.clientY - rect.top) / rect.height) * side.mapH);
  if (mx < 0 || my < 0 || mx >= side.mapW || my >= side.mapH) return null;
  return side.byValue.get(side.labels[(my * side.mapW + mx) * 4]) ?? null;
}

const STYLE = `
.segov { font-size: 13px; }
.segov .segov-hint { color: var(--dim, #9aa3b2); margin-bottom: 10px; }
.segov .segov-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.segov .segov-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: 99px; cursor: pointer; user-select: none;
  border: 1px solid var(--line, #272b35); background: transparent;
  color: var(--tx, #e6e8ee); font: inherit;
}
.segov .segov-chip .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.segov .segov-chip.on { border-color: var(--accent, #60a5fa); background: rgba(96,165,250,.12); }
.segov .segov-chip.invalid { opacity: .45; }
.segov .segov-chip:focus-visible { outline: 2px solid var(--accent, #60a5fa); outline-offset: 2px; }
.segov .segov-panes { display: flex; gap: 14px; flex-wrap: wrap; }
.segov .segov-pane { flex: 1; min-width: 240px; }
.segov .segov-pane .cap { color: var(--dim, #9aa3b2); margin-bottom: 6px; }
.segov canvas { max-width: 100%; height: auto; border-radius: 6px; background: #000;
                cursor: crosshair; display: block; }
.segov .segov-note { color: var(--dim, #9aa3b2); margin-top: 8px; }
.segov .segov-err { color: var(--bad, #f87171); }
`;

/**
 * @param {HTMLElement} container 비어 있어도 되고, 재호출 시 내용을 갈아끼운다
 * @param {object} seg GET /sessions/{id}/segmentation 응답 (reference/user/comparable)
 */
export async function mountSegOverlay(container, seg) {
  container.innerHTML = "";
  container.classList.add("segov");
  if (!document.getElementById("segov-style")) {
    const style = document.createElement("style");
    style.id = "segov-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  const sides = [
    ["레퍼런스", seg.reference],
    ["내 사진", seg.user],
  ].filter(([, r]) => r);

  if (sides.length === 0) {
    container.innerHTML = `<div class="segov-err">세그멘테이션 결과가 아직 없습니다.</div>`;
    return;
  }

  const hint = document.createElement("div");
  hint.className = "segov-hint";
  hint.textContent =
    "부위 칩이나 사진 속 부위를 클릭하면 색칠됩니다. 좌우가 비슷한 색 계열인 것은 " +
    "의도입니다 — 색이 좌우 대칭으로 뒤집혀 보이면 반전 사고입니다.";
  container.appendChild(hint);

  const chipRow = document.createElement("div");
  chipRow.className = "segov-chips";
  container.appendChild(chipRow);

  const panes = document.createElement("div");
  panes.className = "segov-panes";
  container.appendChild(panes);

  const note = document.createElement("div");
  note.className = "segov-note";
  container.appendChild(note);

  let prepared;
  try {
    prepared = await Promise.all(
      sides.map(async ([label, result]) => {
        const side = await prepareSide(result, 560);
        const pane = document.createElement("div");
        pane.className = "segov-pane";
        const cap = document.createElement("div");
        cap.className = "cap";
        cap.textContent = label;
        pane.appendChild(cap);
        pane.appendChild(side.canvas);
        panes.appendChild(pane);
        return side;
      })
    );
  } catch (e) {
    container.innerHTML =
      `<div class="segov-err">오버레이를 그리지 못했습니다: ${e.message}<br>` +
      `signed URL 이 만료됐거나(1시간) 맵 픽셀을 읽지 못한 경우입니다 — ` +
      `"전 구간 실행"을 다시 돌리면 새 URL 로 열립니다.</div>`;
    return;
  }

  // ── 칩: 두 쪽 팔레트의 합집합. 서버가 정렬해 준 순서를 유지한다 ──
  const partsByName = new Map();
  for (const [, result] of sides) {
    for (const p of result.palette) {
      if (!partsByName.has(p.class_name)) partsByName.set(p.class_name, p);
    }
  }

  const selected = new Set();
  const chips = new Map();

  const repaint = () => {
    for (const side of prepared) paint(side, selected);
    for (const [name, chip] of chips) chip.classList.toggle("on", selected.has(name));
    const picked = [...selected].map((n) => partsByName.get(n)?.name_ko ?? n);
    note.textContent = picked.length
      ? `선택: ${picked.join(", ")}`
      : "선택된 부위 없음";
  };

  const toggle = (name) => {
    if (selected.has(name)) selected.delete(name);
    else selected.add(name);
    repaint();
  };

  for (const [name, p] of partsByName) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "segov-chip" + (p.is_valid ? "" : " invalid");
    if (!p.is_valid) chip.title = `무효: ${p.invalid_reason ?? "?"}`;
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = p.color_hex || FALLBACK_HEX;
    chip.appendChild(dot);
    chip.appendChild(document.createTextNode(p.name_ko ?? name));
    chip.onclick = () => toggle(name);
    chips.set(name, chip);
    chipRow.appendChild(chip);
  }

  // 전체/지우기 — 비교 대상만 한 번에 켜는 게 제일 자주 쓰는 동작이다.
  const all = document.createElement("button");
  all.type = "button";
  all.className = "segov-chip";
  all.textContent = "비교 대상 전체";
  all.onclick = () => {
    for (const [name, p] of partsByName) if (p.is_comparable) selected.add(name);
    repaint();
  };
  const none = document.createElement("button");
  none.type = "button";
  none.className = "segov-chip";
  none.textContent = "지우기";
  none.onclick = () => {
    selected.clear();
    repaint();
  };
  chipRow.prepend(none);
  chipRow.prepend(all);

  // 사진 클릭 → 그 지점의 부위 토글 (어느 쪽을 클릭하든 양쪽에 반영)
  for (const side of prepared) {
    side.canvas.addEventListener("click", (e) => {
      const part = partAt(side, e);
      if (part) toggle(part.class_name);
    });
  }

  repaint();
}
