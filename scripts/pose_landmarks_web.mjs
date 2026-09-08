// 사진 → 포즈 랜드마크 JSON — **프론트와 같은 MediaPipe(JS)** 로 잰다.
//
//   node scripts/pose_landmarks_web.mjs photos/123.jpg out/landmarks/123.json [photos/456.jpg out/landmarks/456.json ...]
//
// 왜 파이썬 mediapipe 가 아니라 이것인가
//   서버는 MediaPipe 를 돌리지 않는다 — 랜드마크는 프론트(브라우저)가 잰다. 얼굴 가림
//   (app/services/face_mask.py) 실측에는 실제 랜드마크가 필요한데, 파이썬 mediapipe 는
//   수백 MB 이고 프론트와 모델·버전이 달라질 수 있다. 여기서는 프론트 pose-detector.ts 와
//   **같은 wasm·같은 모델·같은 옵션**을 헤드리스 크롬에서 돌린다 → 프로덕션에서 팟이
//   받을 값과 같은 계산이다.
//
// 의존성 없음: Node 22+ (내장 WebSocket·fetch) + 설치된 Chrome. 네트워크 필요(wasm·모델 CDN).
// 출력 형식: [{x, y, z, visibility}, ...] 33개 — 프론트가 pose_landmarks 로 보내는 것과 같다.

import { spawn } from 'node:child_process'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, extname } from 'node:path'

// ⚠️ 프론트 src/lib/pose-detector.ts 와 같은 값. 바뀌면 여기도 맞춘다.
const WASM = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm'
const MODEL = 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task'
const BUNDLE = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs'
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROFILE = process.env.TEMP + '/pose-landmarks-web-profile'
const PORT = 9223

const args = process.argv.slice(2)
if (args.length < 2 || args.length % 2) {
  console.log('사용법: node scripts/pose_landmarks_web.mjs <사진> <출력.json> [<사진> <출력.json> ...]')
  process.exit(2)
}
const pairs = []
for (let i = 0; i < args.length; i += 2) pairs.push([args[i], args[i + 1]])

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`, '--no-first-run', '--no-default-browser-check', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', 'about:blank'], { stdio: 'ignore' })

let wsUrl
for (let i = 0; i < 40; i++) {
  try {
    const t = await (await fetch(`http://localhost:${PORT}/json/new?about:blank`, { method: 'PUT' })).json()
    wsUrl = t.webSocketDebuggerUrl; break
  } catch { await sleep(250) }
}
if (!wsUrl) { console.log('FAIL: Chrome 에 붙지 못함 — CHROME 경로 확인'); chrome.kill(); process.exit(1) }

const ws = new WebSocket(wsUrl)
await new Promise((r) => (ws.onopen = r))
let id = 0; const pending = new Map()
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id) } }
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })
const evalJs = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || JSON.stringify(r.result.exceptionDetails))
  return r.result?.result?.value
}

let code = 0
try {
  await send('Page.enable'); await send('Runtime.enable')
  // 페이지 컨텍스트에 랜드마커를 만든다 (프론트 getImageLandmarker 와 같은 옵션)
  const setup = await evalJs(`(async () => {
    const { FilesetResolver, PoseLandmarker } = await import(${JSON.stringify(BUNDLE)})
    const vision = await FilesetResolver.forVisionTasks(${JSON.stringify(WASM)})
    let delegate = 'GPU'
    try {
      window.__lm = await PoseLandmarker.createFromOptions(vision, { baseOptions: { modelAssetPath: ${JSON.stringify(MODEL)}, delegate }, runningMode: 'IMAGE', numPoses: 2 })
    } catch (e) {
      delegate = 'CPU'
      window.__lm = await PoseLandmarker.createFromOptions(vision, { baseOptions: { modelAssetPath: ${JSON.stringify(MODEL)}, delegate }, runningMode: 'IMAGE', numPoses: 2 })
    }
    return delegate
  })()`)
  console.log(`랜드마커 준비 (delegate=${setup}, wasm 0.10.14, pose_landmarker_lite)`)

  for (const [photo, out] of pairs) {
    const mime = { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp' }[extname(photo).toLowerCase()] || 'image/jpeg'
    const dataUrl = `data:${mime};base64,${readFileSync(photo).toString('base64')}`
    const res = await evalJs(`(async () => {
      const img = new Image(); img.src = ${JSON.stringify(dataUrl)}; await img.decode()
      const r = window.__lm.detect(img)
      const lm = r.landmarks[0]
      if (!lm || !lm.length) return { error: '사람을 찾지 못함' }
      return { width: img.naturalWidth, height: img.naturalHeight, poses: r.landmarks.length,
               landmarks: lm.map(p => ({ x: p.x, y: p.y, z: p.z, visibility: p.visibility })) }
    })()`)
    if (res.error) { console.log(`${photo}: ${res.error}`); code = 1; continue }
    mkdirSync(dirname(out), { recursive: true })
    writeFileSync(out, JSON.stringify(res.landmarks))
    const face = res.landmarks.slice(0, 11).filter(p => p.visibility >= 0.5).length
    console.log(`${photo} (${res.width}x${res.height}, 사람 ${res.poses}) → ${out}  얼굴 점 ${face}/11 보임`)
  }
} catch (e) {
  console.log('FAIL:', e.message); code = 1
} finally {
  ws.close(); chrome.kill()
}
process.exit(code)
