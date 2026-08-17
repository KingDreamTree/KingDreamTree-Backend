# 프론트엔드 연동 가이드 — 사진 파이프라인

> **범위** — 사용자 등록 · 세션 · 사진 촬영/업로드 · 자세 판정 · 부위 분리 결과 조회까지.
> 인바디(F07) · 진단(F08·F09) · 루틴(F10~F12)은 다른 담당이며 이 문서에 없습니다.
> 그쪽 엔드포인트는 [Swagger](http://localhost:8000/docs)의 `inbody` · `analysis` 태그를 보세요.
>
> **필드 정의의 최종 근거는 [Swagger UI](http://localhost:8000/docs) 입니다.** 이 문서는
> 호출 순서, 화면에서 지켜야 할 것, 자주 막히는 지점을 다룹니다.

| | |
|---|---|
| Base URL | `/api/v1` |
| 최종 수정 | 2026-08-14 |

---

## 목차

1. [전체 호출 순서](#1-전체-호출-순서)
2. [준비 — CORS와 인증](#2-준비--cors와-인증)
3. [사용자와 세션](#3-사용자와-세션)
4. [자세 판정은 프론트가 계산합니다](#4-자세-판정은-프론트가-계산합니다)
5. [레퍼런스 사진 업로드](#5-레퍼런스-사진-업로드)
6. [사용자 사진 업로드](#6-사용자-사진-업로드)
7. [거울 촬영 처리](#7-거울-촬영-처리)
8. [작업 진행 확인](#8-작업-진행-확인)
9. [부위별 오버레이 그리기](#9-부위별-오버레이-그리기)
10. [좌우 비교 화면](#10-좌우-비교-화면)
11. [이미지 URL 재발급](#11-이미지-url-재발급)
12. [에러 전체 목록](#12-에러-전체-목록)
13. [이미지 규칙 요약](#13-이미지-규칙-요약)

---

## 1. 전체 호출 순서

```
웹앱 최초 진입   POST /users                    → user_id 를 로컬에 보관
                GET  /pose-criteria            → 자세 판정 기준 (한 번만)

앱 재진입       GET  /users/me                 → 저장된 id 가 유효한지
                GET  /sessions/active          → steps 로 어느 화면인지 판단

분석 시작       POST /sessions
레퍼런스        POST /sessions/{id}/photos/reference   → job_id
촬영 화면       GET  /sessions/{id}/photos/reference   → 기준 랜드마크
사용자 사진     POST /sessions/{id}/photos/user        → job_id

진행 확인       GET  /jobs/{job_id}            → 1.5초 간격 폴링
결과 화면       GET  /sessions/{id}/segmentation

처음부터 다시   POST /sessions/{id}/archive    → 그 다음 POST /sessions
```

---

## 2. 준비 — CORS와 인증

### CORS

서버가 허용 목록에 있는 오리진만 받습니다. 개발 중에는 전부 허용돼 있습니다.

⚠️ **Postman에서는 되는데 브라우저에서만 실패**하면 이걸 의심하세요.
배포 주소가 정해지면 백엔드에 알려주시면 목록에 넣습니다.

### `X-User-Id` 헤더

로그인이 없습니다. `POST /users` 로 받은 UUID를 **모든 요청 헤더에** 넣습니다.

```
X-User-Id: 8f14e45f-ceea-467a-9b21-0c3e7d1a55b2
```

**헤더가 필요 없는 엔드포인트** — `POST /users`, `GET /body-parts`, `GET /pose-criteria`

⚠️ **반드시 처리해야 할 것**

- `user_id` 를 로컬 스토리지에 보관합니다. **잃으면 복구 수단이 없습니다**
- 앱 진입 시 `GET /users/me` 로 먼저 확인하세요. 서버 DB가 초기화됐는데 옛 id가 남아 있으면 이후 요청이 전부 실패합니다
- **"기기를 바꾸거나 브라우저 데이터를 지우면 기록이 사라집니다"** 안내가 필요합니다

---

## 3. 사용자와 세션

### `POST /users` — 식별자 발급 (헤더 불필요)

```json
{ "user_id": "8f14e45f-...", "created_at": "..." }
```

### `GET /users/me` — 저장된 id 가 유효한지

404면 서버에 그 사용자가 없습니다 → **재발급**하세요.

### `POST /sessions` — 분석 회차 시작

**한 사람당 진행 중인 분석은 하나뿐**입니다.

| 응답 | 뜻 |
|---|---|
| 201 | 새 세션 시작 |
| 409 `ACTIVE_SESSION_EXISTS` | 이미 있음. `detail.session_id` 로 **이어서 진행** |
| 404 `NOT_FOUND` | 저장된 `user_id` 가 서버에 없음 → **재발급** |

### `POST /sessions/{session_id}/archive` — 처음부터 다시 하기

409를 받았는데 **이어서가 아니라 새로 시작**하고 싶을 때 씁니다.

```
POST /sessions/{id}/archive     → 200, status: "ARCHIVED"
POST /sessions                  → 201, 새 session_id
```

- 사진과 결과는 **지워지지 않습니다.** 지난 분석은 그대로 남습니다
- **여러 번 눌러도 안전합니다** — 이미 종료된 세션에 다시 불러도 200입니다

⚠️ 이 버튼이 없으면 사용자는 **첫 세션에 갇힙니다.** 레퍼런스를 잘못 올렸을 때
빠져나갈 길이 계정 삭제밖에 없습니다.

### `GET /sessions/active` — 새로고침·재진입 시 어느 화면으로

```json
{
  "session_id": "3c9a1b7e-...",
  "status": "ACTIVE",
  "steps": {
    "reference_photo": { "uploaded": true,  "segmented": true,  "job_status": "DONE" },
    "user_photo":      { "uploaded": true,  "segmented": false, "job_status": "PROCESSING" },
    "inbody":          { "count": 2, "done": 1, "failed": 0, "pending": 1 },
    "analysis":        { "part_done": 3, "part_total": 9, "overall_status": "PENDING" },
    "routine":         { "active_version": null, "status": null }
  },
  "created_at": "..."
}
```

> `steps` 안의 `inbody` · `analysis` · `routine` 은 다른 담당의 기능입니다.
> 이 문서에서는 `reference_photo` 와 `user_photo` 만 보시면 됩니다.

| 응답 | 화면 |
|---|---|
| 200 | `steps` 를 보고 이어갈 화면으로 |
| 404 `NO_ACTIVE_SESSION` | **오류가 아니라 상태입니다.** 시작 화면으로 |
| 404 `NOT_FOUND` | 계정이 없음 → 재발급 |

### `DELETE /users/me` — 계정 삭제

204를 반환합니다. **사진·결과·세션이 전부 실제로 지워집니다.**
되돌릴 수 없으니 확인 다이얼로그가 필요합니다.

---

## 4. 자세 판정은 프론트가 계산합니다

**서버는 MediaPipe를 돌리지 않습니다.** 관절 추출과 점수 계산은 프론트가 하고,
서버는 값의 형식 검사와 통과·거부 판정만 합니다.

실시간 촬영뿐 아니라 **갤러리 업로드 경로에서도** 프론트가 계산해야 합니다.

### 보낼 값 세 개

| 값 | 범위 | 무엇 |
|---|---|---|
| `pose_similarity` | 0~100 | 관절이 **가리키는 방향**의 일치도 |
| `framing_score` | 0~1 | **촬영 거리** 일치도 (몸통 길이 비율) |
| `facing_delta` | 0~ | **레퍼런스와 몸 방향의 차이.** ⚠️ '정면인가'가 아니라 상대값이고, **1을 넘을 수 있습니다** (레퍼런스가 많이 돌아가 있을 때). ⚠️ **2026-08-14부터 판정에 안 씁니다** — 계속 보내주세요(저장·관찰용), 다만 이 값 때문에 거부되는 일은 없습니다 |
| `oks` | 0~1 | OKS-inspired 유사도. ⚠️ **판정에 안 씁니다.** 업로드 때 `pose_oks`로 같이 보내주세요 — 어느 산식이 나은지 비교하려고 모으는 중입니다 |

### ⚠️ 노트북 웹캠에서는 전신이 잘 안 나옵니다

브라우저에서 도는 서비스라 대부분 노트북 웹캠으로 찍게 됩니다. 그런데:

- 카메라가 **눈높이보다 낮습니다** → 다리가 짧아 보이고 원근이 왜곡됩니다
- **화각이 좁습니다** → 앉은 자세면 상반신만 나옵니다

안내가 없으면 사용자는 계속 `POSE` 미달이나 `CROPPED` 를 받고, **왜 그런지 모릅니다.**

**촬영 화면에 이런 안내를 넣어주세요.**

```
"노트북이면 2~3걸음 물러나고, 카메라를 눈높이에 맞춰주세요"
"발끝까지 나올 필요는 없습니다 — 몸통과 팔다리만 보이면 됩니다"
```

⚠️ 두 번째 문장이 중요합니다. **머리나 발끝이 잘려도 통과합니다.** 사용자가
전신을 다 넣으려고 애쓰다가 오히려 너무 멀리 서면, 사람이 작게 나와서
부위별 픽셀이 부족해집니다.

### 📦 구현이 있습니다 — 그대로 쓰세요

```
web/pose-score.js          의존성 없는 ES 모듈
web/pose-score.test.html   브라우저로 열면 스스로 검사 (설치 불필요)
web/pose-live.html         레퍼런스 사진 + 웹캠으로 직접 확인 (실서비스 흐름)
```

```js
import { evaluate, createHoldGate, fetchCriteria } from "./pose-score.js";

const criteria = await fetchCriteria();      // 앱 시작 시 한 번
const hold = createHoldGate(criteria);

// 매 프레임
const r = evaluate(refLandmarks, userLandmarks, criteria, { multiPerson });

showGuide(r.message);        // "포즈를 맞춰주세요" 등, 그대로 노출 가능
                             // 문구를 바꾸려면 MESSAGES 를 수정하세요 (export 돼 있습니다)
if (hold(r.pass)) shutter(); // 최근 39프레임 중 30개 통과면 자동 촬영 (약 1초)

// 업로드할 때 이 세 값을 보냅니다
//   r.pose_similarity / r.framing_score / r.facing_delta
```

`evaluate()` 가 돌려주는 것:

| 필드 | 용도 |
|---|---|
| `pass` | **자동 촬영 조건.** 실시간 촬영 화면에서 씁니다 |
| `blocked` | **서버가 거부하는가.** 갤러리 업로드 경로에서 이걸 보세요 |
| `message` | 사용자에게 그대로 보여줄 문구 |
| `reason` | 로그용. 화면 분기에는 쓰지 마세요 |

⚠️ **통과 여부를 서버로 보내지 않습니다.** 값만 보내고 판정은 서버가 다시 합니다.
`pass` 는 화면 표시용입니다.

⚠️ `reason` 이 `NOT_ENOUGH_JOINTS` 면 **업로드하지 마세요.** 서버는 숫자만 받아
"포즈를 맞춰주세요"라고 답하는데, 실제 문제는 몸이 안 보이는 것이라 사용자가
엉뚱한 걸 고치게 됩니다.

### `GET /pose-criteria` — 임계값을 하드코딩하지 마세요

헤더 불필요. 앱 시작 시 한 번 받아 보관합니다.

```json
{ "tol_deg": 60, "hard_tol_deg": 70, "threshold": 70,
  "f_min": 0.65, "f_hard": 0.40, "min_seg_ratio": 0.25,
  "min_visible_angles": 4, "min_visibility": 0.5,
  "min_ref_coverage": 0.7, "n_hold": 30 }
```

하드코딩하면 서버에서 값을 조정한 순간 어긋나서, **화면에서는 통과인데 업로드가
거부되는 상황**이 생깁니다.

## 거울 매칭 세션의 표시 규약 (cross_paired)

`GET /sessions/{id}/segmentation` 의 `comparable.cross_paired` 가 true 면
(실시간 촬영 세션 — 서버가 `capture_source` 로 판단):

1. **비교 부위 짝**: `class_names` 는 사용자 기준. 레퍼런스 쪽 색칠만
   부위명을 Left↔Right 교차해서 찾는다.
2. **사용자 사진은 거울로 표시** (합성 캔버스째 `scaleX(-1)`) — 촬영 미리보기와
   같은 방향. **저장본·좌표·진단은 전부 원본 기준**이므로 표시 반전만 한다.
3. 레퍼런스 사진은 원본 그대로 표시한다.

이 조합이 두 사진의 색칠을 같은 편에 놓고, "오른팔" 라벨을 사용자의 거울
감각과 일치시킨다. 근거와 규칙의 단일 관문은 `app/services/part_pairing.py`.

`n_hold` 는 자동 촬영용입니다. `createHoldGate` 가 **최근 `n_hold`+30% 프레임 중
`n_hold` 개 통과**일 때 셔터를 누릅니다 (기본 30 → 39프레임 중 30개, 30fps 기준
통과 상태 약 1초 유지). 손이 지나가다 우연히 맞는 순간에 찍히면
안 되고, 반대로 검출이 한 프레임 튄 것 때문에 0부터 다시 세도 안 됩니다.

⚠️ **`framing_score` 는 bbox 겹침(IoU)이 아닙니다.** 몸통 길이 비율로
"비슷한 거리에서 찍었는가"만 봅니다. bbox로 재면 **팔다리를 움직인 것이 프레이밍
문제로 보고돼** 사용자에게 고칠 수 없는 안내가 나갑니다.

📄 산식과 근거: `docs/pose-scoring.md`

---

## 5. 레퍼런스 사진 업로드

### `POST /api/v1/sessions/{session_id}/photos/reference`

`multipart/form-data`

| 필드 | 필수 | 설명 |
|---|---|---|
| `file` | O | jpeg / png / heic / webp, 10MB 이하 |
| `pose_landmarks` | O | MediaPipe 33개 랜드마크 배열을 **JSON 문자열로** |
| `pose_scale_basis` | O | `TORSO` \| `HIP_KNEE` |
| `pose_person_area_ratio` | X | 0~1 |
| `multi_person` | X | 기본 `false` |
| `is_mirrored` | X | 기본 `false` — [7번](#7-거울-촬영-처리) 참조 |

랜드마크 하나의 형태:

```json
{ "index": 0, "x": 0.51, "y": 0.18, "z": -0.32, "visibility": 0.99 }
```

**응답 201** — `photo_id` / `job_id` / `width` / `height` / `pose_scale_basis` /
`was_mirrored` / `pose_landmarks` / `signed_url` / `signed_url_expires_at` / `segmented`

### ⚠️ 레퍼런스 복장 안내가 꼭 필요합니다

**레퍼런스에서 안 잡힌 부위는 사용자 사진이 아무리 좋아도 비교되지 않습니다.**
두 사진의 교집합만 비교하기 때문입니다.

실측 — 같은 서버 설정인데 사진만 다릅니다.

| 레퍼런스 | 비교 가능 부위 |
|---|---|
| 긴팔·긴바지, 팔이 몸에 붙은 자세 | **0개** |
| 반팔·반바지, 정면 전신 | **9개 전부** |

**두 가지를 해주세요.**

1. 업로드 화면에 안내 — **"반팔·반바지, 정면 전신 사진을 올려주세요"**
2. 작업이 `DONE` 이 되면 `job.result.valid_comparable` 을 확인하고, **3 미만이면
   그 자리에서 다른 사진을 권하세요.** `invalid` 에 어떤 부위가 왜 빠졌는지 있습니다

```json
{ "detected": 12, "valid_comparable": 2,
  "invalid": [ { "class_name": "Left_Upper_Arm", "reason": "TOO_SMALL" } ] }
```

사용자 사진까지 다 찍게 한 다음에 "비교할 게 없습니다"라고 하면 두 번 일하게 됩니다.

### `GET /api/v1/sessions/{session_id}/photos/reference`

촬영 화면에서 기준 랜드마크를 받아옵니다.

⚠️ `job_id` 가 **`null` 일 수 있습니다.** null이면 폴링하지 마세요.

---

## 6. 사용자 사진 업로드

### `POST /api/v1/sessions/{session_id}/photos/user`

레퍼런스와 같은 필드에 더해:

| 필드 | 필수 | 설명 |
|---|---|---|
| `capture_source` | O | `CAPTURE`(앱 내 촬영) \| `UPLOAD`(파일 선택) |
| `pose_similarity` | O | 0~100 |
| `framing_score` | O | 0~1 |
| `facing_delta` | X | 안 보내면 통과 처리되지만 **몸이 돌아간 사진을 못 거릅니다**. ⚠️ 1을 넘을 수 있으니 클램프하지 마세요 |
| `pose_oks` | X | 판정에 안 쓰지만 **보내주세요.** 안 보내면 산식 비교 데이터가 안 쌓입니다 |
| `pose_scale_basis` | O | ⚠️ **레퍼런스와 같아야 합니다.** 다르면 422 |

⚠️ **레퍼런스가 먼저 등록돼 있어야 합니다.** 없으면 409 `PRECONDITION_NOT_MET`

### ⚠️ 업로드 응답이 1~3초 걸립니다

사용자 사진은 저장 전에 **AI가 "이 두 장으로 비교가 되는가"를 판정**합니다.
로딩 표시가 필요합니다.

그 자리에서 재촬영을 유도하는 것이 목적이라 일부러 동기로 만들었습니다.
비동기면 사용자가 다음 화면으로 넘어간 뒤에 "다시 찍으세요"가 뜹니다.

### ⚠️ 거부된 사진은 저장되지 않습니다

422를 받으면 서버에 아무것도 안 남습니다. **재촬영 UI가 반드시 필요합니다.**
"나중에 다시 시도" 같은 흐름은 불가능합니다.

### ⚠️ 재업로드는 교체입니다

이전 사진과 그 분석 결과가 통째로 사라지고, 진행 중이던 작업도 취소됩니다
(`FAILED`, `error: "사진이 교체되어 취소되었습니다."`).
폴링 중이었다면 **새 `job_id` 로 갈아타세요.**

---

## 7. 거울 촬영 처리

거울로 찍은 사진은 좌우가 **물리적으로 뒤집혀** 있습니다. 그대로 두면 왼팔 진단이
오른팔에 붙습니다. **에러는 하나도 안 납니다.**

`is_mirrored=true` 를 받으면 서버가 저장 직전에 이미지와 랜드마크를 되돌립니다.
응답의 `pose_landmarks` 는 **되돌린 뒤** 값이라 촬영 화면 기준값으로 그대로 씁니다.

| 촬영 방식 | `is_mirrored` |
|---|---|
| 앱 내 웹캠 촬영 (`CAPTURE`) | **항상 `false`** |
| 파일 업로드 (`UPLOAD`) | 알 수 없음 → **체크박스로 사용자에게 물어보기** (기본 꺼짐) |

⚠️ **웹캠 미러링은 CSS로만 하세요.** 화면에는 거울처럼 보여주되, **서버로 보내는
이미지와 랜드마크는 반전되지 않은 카메라 원본**이어야 합니다.

```css
/* 화면에 보여주는 것만 뒤집기 */
video, canvas.overlay { transform: scaleX(-1); }
```

```js
// ❌ 이렇게 하면 안 됩니다 — 뒤집힌 픽셀이 그대로 서버로 갑니다
ctx.scale(-1, 1);
ctx.drawImage(video, -w, 0);
```

캔버스에서 뒤집어 보내면 `is_mirrored=false` 인데 실제로는 뒤집힌 사진이 들어와
좌우가 통째로 어긋납니다. **에러는 하나도 안 나고, 왼팔 진단이 오른팔에 붙습니다.**
이 프로젝트에서 **아무도 못 잡는 유일한 오류**입니다.

---

## 8. 작업 진행 확인

### `GET /api/v1/jobs/{job_id}`

**1.5초 간격** 권장. `status` 는 `PENDING` → `PROCESSING` → `DONE` / `FAILED`.

`DONE` 이면 `result` 에 요약이 들어옵니다.

```json
{ "segmentation_id": "f1e2...", "photo_kind": "USER",
  "detected": 12, "valid_comparable": 7,
  "retake_recommended": false, "min_comparable_parts": 3,
  "invalid": [ { "class_name": "Left_Lower_Leg", "reason": "TOO_SMALL" } ] }
```

- `detected` — **배경을 뺀** 검출 부위 수. 팔레트 항목 수와 같습니다
- `retake_recommended` — 서버가 판단해 내려줍니다. **프론트에서 임계값을 계산하지 마세요**
- `invalid` — 세그 완료 즉시 **"왼쪽 종아리는 노출이 부족합니다"** 안내를 낼 수 있습니다

⚠️ `job.status`(작업 실행 상태)와 결과 안의 `status`(화면에 써도 되는지)는 **다릅니다.**

### `GET /api/v1/sessions/{session_id}/jobs` — 세션의 작업 목록

진행률 표시용입니다. `?kind=SEG_USER` · `?status=PENDING` 으로 거를 수 있습니다.

```json
{ "items": [
    { "job_id": "...", "kind": "SEG_REFERENCE", "status": "DONE",
      "attempts": 1, "created_at": "..." }
] }
```

⚠️ 사진을 재업로드하면 **옛 작업이 `FAILED` 로 남습니다**(취소된 것). 목록에서
실패가 보인다고 지금 사진이 실패한 건 아닙니다. **가장 최근 것만** 보세요.

---

## 9. 부위별 오버레이 그리기

### `GET /api/v1/photos/{photo_id}/segmentation`

결과는 **부위별 이미지 N장이 아니라 라벨 맵 PNG 1장**입니다.
8-bit 그레이스케일이고 **픽셀 값이 곧 부위 번호**입니다.

`palette` 가 함께 옵니다. **번호 ↔ 부위명 ↔ 색 ↔ 유효성 ↔ 통계를 서버가 합쳐서
내려주므로 따로 조회할 필요가 없습니다.**

```json
{
  "map_url": "https://.../map.png?token=...",
  "map_width": 768, "map_height": 1024,
  "photo_url": "https://.../user.jpg?token=...",
  "photo_width": 1080, "photo_height": 1440,
  "model": { "name": "sapiens2", "version": "sapiens2-seg-5b" },
  "person_area_ratio": 0.28,
  "palette": [
    { "label_value": 22, "class_name": "Torso", "name_ko": "몸통",
      "color_hex": "#4C6EF5", "is_comparable": true, "is_valid": true,
      "invalid_reason": null, "is_truncated": false,
      "pixel_count": 48210, "area_ratio": 0.212,
      "bbox": { "x": 210, "y": 180, "w": 340, "h": 420 } }
  ],
  "signed_url_expires_at": "..."
}
```

세그가 아직 안 끝났으면 **404**입니다. `GET /jobs/{job_id}` 로 완료를 확인하고 부르세요.

### ⚠️ 꼭 지킬 것 3가지

**1. `crossOrigin = "anonymous"` 없으면 픽셀을 못 읽습니다.**
signed URL은 다른 오리진이라, 이게 없으면 캔버스가 오염돼 `getImageData()` 가
`SecurityError` 를 던집니다. **여기서 제일 먼저 막힙니다.**

**2. 맵을 JS로 리샘플하지 마세요.**
보간이 라벨 값을 섞어 **존재하지 않는 부위를 만들어냅니다.** 크기 조정은 CSS로만
하고 `image-rendering: pixelated` 를 함께 주세요.

**3. `palette` 를 하드코딩하지 마세요.**
모델 버전이 바뀌면 `label_value` 가 재배열됩니다. 응답의 `palette` 를 그대로 쓰세요.
`color_hex` 가 `null` 인 항목(배경·옷·머리)은 **칠하지 않습니다.**

```js
const map = new Image();
map.crossOrigin = "anonymous";          // ⚠️ 필수
map.src = seg.map_url;
await map.decode();

const c = document.createElement("canvas");
c.width = seg.map_width; c.height = seg.map_height;
const ctx = c.getContext("2d", { willReadFrequently: true });
ctx.drawImage(map, 0, 0);

const src = ctx.getImageData(0, 0, c.width, c.height);
const out = ctx.createImageData(c.width, c.height);
const lut = {};
for (const p of seg.palette) {
  if (!p.color_hex) continue;           // 배경·옷은 투명
  lut[p.label_value] = [1, 3, 5].map(i => parseInt(p.color_hex.substr(i, 2), 16));
}
for (let i = 0; i < src.data.length; i += 4) {
  const rgb = lut[src.data[i]];         // 그레이스케일이라 R 채널 = 라벨 값
  if (!rgb) continue;
  out.data[i] = rgb[0]; out.data[i+1] = rgb[1]; out.data[i+2] = rgb[2]; out.data[i+3] = 140;
}
ctx.putImageData(out, 0, 0);
// 원본 위에 CSS로 겹치기. 맵 크기 ≠ 원본 크기라 늘려서 맞춤
```

### ⚠️ `bbox` 는 맵 좌표계입니다 — x·y 배율이 서로 다릅니다

모델이 사진을 고정 크기(현재 768×1024)로 리사이즈해 추론하므로 **원본과 가로세로
비율이 다릅니다.**

```
원본 700×1049 (1.50)  →  맵 768×1024 (1.33)   ← 세로로 눌림
```

**하나의 배율로 곱하면 어긋납니다.** x와 y를 각각 계산하세요.

```js
const sx = seg.photo_width  / seg.map_width;
const sy = seg.photo_height / seg.map_height;   // ⚠️ sx 와 다르다

const box = { x: p.bbox.x * sx, y: p.bbox.y * sy,
              w: p.bbox.w * sx, h: p.bbox.h * sy };
```

색칠 오버레이 자체는 CSS로 늘리면(`width:100%; height:100%`) 자동으로 맞습니다.
**박스·좌표를 직접 계산할 때만** 주의하세요.

### `is_truncated` — 잘렸을 수 있다는 표시

⚠️ **무효 처리는 하지 않습니다.** 전신 사진은 발이 바닥 경계에 닿는 게 정상이라
잘림만으로 빼면 너무 많이 빠집니다. **굵기 값의 신뢰도가 낮다**는 신호로 쓰세요.

---

## 10. 좌우 비교 화면

### `GET /api/v1/sessions/{session_id}/segmentation`

레퍼런스·사용자를 한 번에 받습니다. 아직 세그가 안 끝난 쪽은 `null` 입니다
(404가 아닙니다 — 한쪽만 끝나도 화면을 그릴 수 있게).

```json
{
  "reference": { "...9번과 동일한 구조..." },
  "user": { "...동일..." },
  "comparable": {
    "class_names": ["Left_Upper_Arm", "Torso"],
    "count": 2, "sufficient": false, "min_required": 3,
    "reference_only": ["Right_Upper_Arm"], "user_only": [],
    "excluded": [
      { "class_name": "Right_Upper_Arm", "name_ko": "오른팔 상완", "side": "USER",
        "reason": "TOO_SMALL",
        "message": "오른팔 상완이 거의 보이지 않습니다. 옷에 가려졌을 수 있어요." }
    ]
  }
}
```

**`excluded` 가 재촬영 안내의 재료입니다.** `message` 는 그대로 보여줘도 되게
쓰여 있습니다. `side` 로 어느 사진이 문제인지 알 수 있습니다
(`REFERENCE` / `USER` / `BOTH`).

**`sufficient: false`** 면 비교 가능한 부위가 부족합니다. 이 단계에서 `excluded` 를
보여주고 재촬영을 유도하세요.

---

## 11. 이미지 URL 재발급

### `POST /api/v1/storage/signed-urls`

이미지 URL은 **1시간 뒤 만료**됩니다. 화면을 오래 열어두면 깨지므로 다시 받으세요.

```json
{ "items": [ { "bucket": "segmentations", "path": "8f14.../3c9a.../user/map.png" } ],
  "expires_in": 3600 }
```

한 번에 최대 30개. `photos` / `segmentations` / `body-parts` 버킷만 발급됩니다.

---

## 12. 에러 전체 목록

모든 에러는 같은 형태입니다.

```json
{ "error": { "code": "POSE_MISMATCH",
             "message": "사용자에게 그대로 보여줘도 되는 문구",
             "detail": { "pose_similarity": 62.4, "threshold": 70.0, "reason": "POSE" } } }
```

`message` 는 그대로 노출해도 되게 쓰여 있습니다.

| HTTP | code | 화면에서 할 일 |
|---|---|---|
| 401 | `MISSING_USER_ID` | 헤더 자체가 없음 |
| 401 | `INVALID_USER_ID` | 헤더 형식이 UUID가 아님 → **재발급** |
| 404 | `NOT_FOUND` | 없거나 **남의 것**. 403을 주지 않는 건 의도된 설계입니다 |
| 404 | `NO_ACTIVE_SESSION` | 진행 중 분석 없음. **오류가 아니라 상태** |
| 409 | `ACTIVE_SESSION_EXISTS` | `detail.session_id` 로 이어서, 또는 archive 후 재생성 |
| 409 | `PRECONDITION_NOT_MET` | 선행 단계 미완료 (레퍼런스 없이 사용자 사진 등) |
| 413 | `FILE_TOO_LARGE` | 10MB 초과 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 이미지로 열리지 않는 파일 (해상도 과대 포함) |
| 422 | `POSE_MISMATCH` | 자세 판정 미달 (아래 참조) |
| 422 | `UNSUITABLE_PHOTO` | 사진 적합성 미달 (아래 참조) |
| 422 | `MULTI_PERSON` | "혼자 나오도록 촬영해주세요" |
| 400 | `INVALID_REQUEST` | 값 형식·범위 오류. `detail` 에 필드명이 있습니다 |
| 405 | `METHOD_NOT_ALLOWED` | 요청 메서드 오류 (개발 중 실수) |
| 500 | `INTERNAL_ERROR` | 서버 오류. `message` 를 보여주고 재시도 안내 |
| 503 | `SCREENING_UNAVAILABLE` | 2차 검사기 장애 — **같은 사진으로 잠시 후 재시도** 안내. ⚠️ 422처럼 "재촬영"으로 보여주면 안 됩니다 (사진 문제가 아님) |

### `POSE_MISMATCH` 의 `detail.reason`

| reason | 사용자가 해야 할 행동 | 예시 문구 |
|---|---|---|
| `POSE` | 자세를 바꿔야 함 | "레퍼런스와 포즈를 맞춰주세요" |
| `FRAMING` | 촬영 거리가 너무 다름 | "비슷한 거리에서 다시 촬영해주세요" |
| `NO_PERSON` | 사람이 안 잡힘 | "전신이 보이도록 다시 촬영해주세요" |

셋은 **서로 다른 지시**입니다. 하나로 뭉뚱그리면 사용자가 뭘 고쳐야 할지 모릅니다.

> `FACING`(몸 방향)은 2026-08-14 에 뺐습니다 — `facing_delta` 는 보내면 저장만
> 되고 거부 사유로는 더 이상 나오지 않습니다 (docs/pose-scoring.md R 절).

### `UNSUITABLE_PHOTO` — 사진으로 비교가 안 되는 경우

```json
{ "error": { "code": "UNSUITABLE_PHOTO",
             "message": "옷이 헐렁해 몸의 윤곽이 보이지 않습니다. 몸에 붙는 옷으로 다시 촬영해주세요.",
             "detail": { "reason": "LOOSE_CLOTHING", "confidence": "HIGH" } } }
```

`message` 를 **그대로 보여주면 됩니다.**

| reason | 사용자가 해야 할 일 |
|---|---|
| `LOOSE_CLOTHING` | 몸에 붙는 옷으로 갈아입고 재촬영 |
| `PERSPECTIVE_MISMATCH` | 촬영 거리 조정 |
| `CROPPED` | 몸통·팔·다리가 다 담기게 재촬영 |
| `NO_PERSON` / `MULTI_PERSON` | 혼자 나오게 재촬영 |
| `TOO_DARK` | 밝은 곳으로 이동 |
| `BLURRY` | 초점 맞추고 흔들리지 않게 재촬영 |
| `OTHER` | (message 만 표시) |

⚠️ **`reason` 으로 화면을 분기하지 마세요.** 같은 사진인데도 `CROPPED` 와
`PERSPECTIVE_MISMATCH` 처럼 **둘 다 맞는 코드** 사이를 오갑니다(실측 확인).
통과·거부 자체는 흔들리지 않습니다. 화면에는 `message` 를 쓰고, `reason` 은
로그·통계용으로만 보세요.

💡 **머리나 발이 잘린 사진은 반려되지 않습니다.** 크기 정규화를 어깨~골반으로
하기 때문에 머리·발은 필요 없습니다. 목 아래만 나온 사진도 통과합니다.

---

## 13. 이미지 규칙 요약

- **형식** — `jpeg` / `png` / **`heic`(아이폰 원본)** / `webp`. 10MB 이하
  - 서버가 `Content-Type` 헤더를 보지 않고 **실제로 열리는지**로 판단합니다
  - **프론트에서 미리 JPEG로 변환할 필요 없습니다.** 원본 그대로 보내세요
- **EXIF 회전은 서버가 처리합니다.** 미리 돌릴 필요 없습니다
- 긴 변 4096px 초과 시 서버가 축소해 저장합니다
- 8천만 화소를 넘으면 415로 거부됩니다
- 이미지는 전부 private 버킷에 있고 **signed URL** 로만 접근합니다 (만료 1시간)

---

## 부록 — 부위 마스터

### `GET /api/v1/body-parts` (헤더 불필요)

부위 이름·한글명·색·비교 대상 여부의 전체 목록입니다.
오버레이에서는 `palette` 가 이미 합쳐져 오므로 보통 따로 부를 일이 없습니다.

---

## 로컬 서버

```bash
uvicorn app.main:app --reload --port 8000
```

Swagger: http://localhost:8000/docs

막히는 게 있으면 **화면에 뜬 에러 코드와 `detail` 을 그대로** 알려주세요.
