# Frontend 통합 가이드

> **필드 정의의 진실의 원천은 [Swagger UI — `/docs`](http://localhost:8000/docs) 입니다.**
> 이 문서는 호출 흐름·이미지 규칙·에러 처리 등 Swagger에 담기 어려운 맥락을 보완합니다.
> 설계 근거까지 보려면 `docs/api-spec-v2.md`.

| | |
|---|---|
| **최종 수정일** | 2026-08-14 |
| **Base URL** | `/api/v1` |
| **구현 완료** | F02 사용자 · F03 세션 · F04 레퍼런스 · F05 사용자 사진 · F06 세그멘테이션 조회 · **F07 인바디** · F13 잡 폴링 · F14 signed URL · F15 삭제 · 부위 마스터 |
| **미구현** | 진단(F08·F09) · 루틴(F10~F12) |

> ⚠️ **이 문서가 다루는 범위** — 사진·세그멘테이션 파이프라인(F02~F06, F13~F15)까지입니다. **인바디(F07)는 구현돼 있지만 이 문서에 상세가 없습니다.** 엔드포인트 목록과 필드는 [Swagger](http://localhost:8000/docs)의 `inbody` 태그를 보시고, 막히면 백엔드에 물어보세요.

---

## ⚠️ 이전 버전에서 바뀐 것

이 문서의 v1 내용(`POST /analyze` → `/compare` → `/routine` 3단계)은 **전부 사라졌습니다.** 그대로 호출하면 404입니다.

| 이전 | 지금 |
|---|---|
| `POST /analyze`, `/compare`, `/routine` | 없음. 아래 새 흐름으로 대체 |
| `GET /body-parts` | `GET /api/v1/body-parts` |
| `GET /jobs/{id}` | `GET /api/v1/jobs/{id}` |
| 에러 `{"detail": "..."}` | `{"error": {"code", "message", "detail"}}` |

---

## 호출 흐름

```
앱 최초 진입   POST /users              → user_id 를 로컬에 보관
              GET  /pose-criteria      → 자세 판정 기준 (하드코딩 금지)
앱 재진입      GET  /users/me           → 저장된 id 가 유효한지 확인
              GET  /sessions/active    → steps 를 보고 어느 화면으로 갈지 결정

분석 시작      POST /sessions
처음부터 다시  POST /sessions/{id}/archive  → 그 다음 POST /sessions
레퍼런스       POST /sessions/{id}/photos/reference   → job_id (세그 시작)
촬영 화면      GET  /sessions/{id}/photos/reference   → 기준 랜드마크
사용자 사진    POST /sessions/{id}/photos/user        → job_id (세그 시작)
진행 확인      GET  /jobs/{job_id}      → 폴링 (세그 1.5초 간격 권장)
```

---

## CORS

서버가 `CORS_ORIGINS` 에 적힌 오리진만 허용합니다. 기본값은 `*` (전부 허용)입니다.

⚠️ **브라우저에서만 실패하고 Postman 에서는 되는 증상**이면 이걸 의심하세요. 배포 주소가 정해지면 백엔드에 알려주시면 목록에 넣습니다.

---

## 인증 — `X-User-Id`

로그인이 없습니다. `POST /users` 로 받은 UUID를 **모든 요청 헤더에** 실어 보냅니다.

```
X-User-Id: 8f14e45f-ceea-467a-9b21-0c3e7d1a55b2
```

**헤더가 필요 없는 유일한 엔드포인트** — `POST /users`, `GET /body-parts`

**⚠️ 프론트가 반드시 처리해야 할 것**
- `user_id` 를 로컬 스토리지에 보관합니다. **잃으면 복구 수단이 없습니다.**
- 앱 진입 시 `GET /users/me` 로 먼저 확인하세요. DB가 초기화됐는데 옛 id가 남아 있으면 이후 모든 요청이 404로 떨어집니다.
- "기기를 바꾸거나 브라우저 데이터를 지우면 기록이 사라집니다" 안내가 필요합니다.

---

## ⚠️ MediaPipe는 프론트가 전담합니다

**서버는 MediaPipe를 돌리지 않습니다.** 랜드마크 추출과 점수 계산은 전부 프론트 몫입니다.

| 하는 일 | 어디서 |
|---|---|
| 랜드마크 추출, 포즈 유사도(P)·프레이밍(F) 계산 | **프론트** — 실시간 화면과 업로드 경로 **둘 다** |
| 값 형식·범위 검사, 통과/거부 판정 | **서버** |

실시간 촬영뿐 아니라 **갤러리 업로드 경로에서도** 프론트가 MediaPipe를 돌려야 합니다. 서버는 값을 받기만 합니다.

### 무엇을 계산해서 보내나 — 세 값

| 값 | 범위 | 무엇 |
|---|---|---|
| `pose_similarity` | 0~100 | 관절 **각도** 유사도 |
| `framing_score` | 0~1 | **촬영 거리 일치도** (몸통 길이 비율). ⚠️ bbox IoU 아님 — 아래 참고 |
| `facing_delta` | 0~1 | 어깨폭/몸통길이 비율 차 — 몸이 돌아간 정도 |

**📄 산식과 근거: [`docs/pose-scoring.md`](pose-scoring.md)**
**📦 바로 쓸 수 있는 구현: `web/pose-score.js`** — 의존성 없는 ES 모듈입니다. `web/pose-score.test.html` 을 브라우저로 열면 스스로 검사합니다.

⚠️ **`framing_score` 는 bbox 겹침(IoU)이 아닙니다.** 몸통 길이 비율로 "비슷한 거리에서 찍었는가"만 봅니다. bbox 로 재면 **팔다리를 움직인 것이 프레이밍 문제로 보고돼**, 사용자에게 "몸이 다 나오게 서주세요"라는 고칠 수 없는 안내가 나갑니다 (실측 확인).

⚠️ **좌표를 직접 비교하면 안 됩니다.** 사용자가 레퍼런스보다 한 발 뒤에 서면 모든 좌표가 달라지지만 자세는 같습니다. 좌표로 점수를 매기면 자세가 아니라 **서 있는 위치를 재게 됩니다.** 그래서 각도를 씁니다 — 위치·거리·사람 크기에 불변입니다.

### ⚠️ 임계값을 하드코딩하지 마세요 — 서버가 내려줍니다

```
GET /api/v1/pose-criteria        (헤더 불필요, 앱 시작 시 한 번)

{ "tol_deg": 45, "hard_tol_deg": 60, "threshold": 70,
  "f_min": 0.65, "r_max": 0.25,
  "min_visible_angles": 4, "min_visibility": 0.5, "n_hold": 15 }
```

하드코딩하면 서버에서 조정한 순간 어긋나고, **"실시간 촬영은 자동으로 통과"라는 전제가 깨집니다** — 화면에선 통과인데 저장이 거부됩니다.

`n_hold`는 자동 촬영용입니다. 조건을 만족한 상태가 **15프레임(≈0.5초) 이어질 때** 셔터를 누르세요. 손이 지나가다 우연히 맞는 순간에 찍히면 안 됩니다.

### 진행 중 세션 종료 — `POST /sessions/{session_id}/archive`

**사용자당 진행 중 분석은 하나뿐입니다.** 이미 있는 상태에서 `POST /sessions` 를 부르면 409 입니다.

"처음부터 다시 하기" 버튼은 이렇게 만드세요.

```
POST /sessions/{id}/archive     → 200, status: "ARCHIVED"
POST /sessions                  → 201, 새 session_id
```

- 사진과 분석 결과는 **지워지지 않습니다.** 지난 분석은 그대로 남습니다
- **여러 번 눌러도 안전합니다** — 이미 종료된 세션에 다시 불러도 200 입니다

⚠️ 이 버튼이 없으면 사용자는 **첫 세션에 갇힙니다.** 레퍼런스를 잘못 올렸을 때 빠져나갈 길이 계정 삭제밖에 없습니다.

---

## 사진 업로드

둘 다 `multipart/form-data` 입니다.

### `POST /api/v1/sessions/{session_id}/photos/reference`

| 필드 | 필수 | 설명 |
|---|---|---|
| `file` | O | jpeg / png, 10MB 이하 |
| `pose_landmarks` | O | MediaPipe 33개 랜드마크 배열을 **JSON 문자열로**. 0~1 정규화 좌표 |
| `pose_scale_basis` | O | `TORSO` \| `HIP_KNEE` |
| `pose_person_area_ratio` | X | 0~1 |
| `multi_person` | X | 기본 `false` |
| `is_mirrored` | X | 기본 `false` — 아래 참조 |

랜드마크 한 개의 형태:
```json
{ "index": 0, "x": 0.51, "y": 0.18, "z": -0.32, "visibility": 0.99 }
```

**응답 201** — `photo_id` / `job_id` / `width` / `height` / `pose_scale_basis` / `was_mirrored` / `pose_landmarks` / `signed_url` / `signed_url_expires_at` / `segmented`

⚠️ **재업로드하면 이전 사진과 그 분석 결과는 통째로 사라집니다.** 진행 중이던 부위 분리 작업도 취소됩니다(`FAILED`, `error: "사진이 교체되어 취소되었습니다."`). 폴링 중이었다면 **새 `job_id` 로 갈아타세요** — 옛 job_id 를 계속 보면 실패로 뜹니다.

⚠️ 조회(`GET`) 시 `job_id` 는 **null 일 수 있습니다.** null 이면 폴링하지 마세요.

### ⚠️ 레퍼런스 복장 안내가 꼭 필요합니다

**레퍼런스에서 안 잡힌 부위는 사용자 사진이 아무리 잘 나와도 비교가 안 됩니다.** 두 사진의 교집합만 비교하기 때문입니다.

실측 — 같은 서버 설정인데 사진만 다릅니다.

| 레퍼런스 | 비교 가능 부위 |
|---|---|
| 긴팔·긴바지, 팔이 몸에 붙은 자세 | **0개** |
| 반팔·반바지, 정면 전신 | **9개 전부** |

**두 가지를 해주세요.**

1. 레퍼런스 업로드 화면에 안내 — **"반팔·반바지, 정면 전신 사진을 올려주세요"**
2. `SEG_REFERENCE` 잡이 `DONE`이 되면 `job.result` 를 확인하고, `valid_comparable` 이 3 미만이면 **그 자리에서 다른 사진을 권해주세요.** `invalid` 에 어떤 부위가 왜 빠졌는지 들어 있습니다

```json
{ "detected": 12, "valid_comparable": 2,
  "invalid": [ { "class_name": "Left_Upper_Arm", "reason": "TOO_SMALL" } ] }
```

> 💡 `detected` 는 **배경을 뺀** 검출 클래스 수입니다. 팔레트 항목 수와 같습니다.

### 부위마다 `is_truncated` 가 옵니다

팔레트의 각 항목에 `is_truncated` 가 들어 있습니다. **화면 가장자리에 닿아 잘렸을 수 있다**는 표시입니다.

⚠️ **무효 처리는 하지 않습니다.** 전신 사진은 발이 바닥 경계에 닿는 게 정상이라, 잘림만으로 빼면 너무 많이 빠집니다. 대신 **굵기 값의 신뢰도가 낮다**는 신호로 쓰세요 — 예를 들어 결과 화면에서 그 부위에만 작게 표시를 달 수 있습니다.

사용자 사진까지 다 찍게 한 다음에 "비교할 게 없습니다"라고 하면 두 번 일하게 됩니다.

(사용자 사진 업로드 화면에도 같은 자리에 **"몸에 붙는 옷을 입어주세요"** 안내가 필요합니다 — 담당 B 요청)

### `POST /api/v1/sessions/{session_id}/photos/user`

위 필드에 더해:

| 필드 | 필수 | 설명 |
|---|---|---|
| `capture_source` | O | `CAPTURE`(앱 내 촬영) \| `UPLOAD`(파일 선택) |
| `pose_similarity` | O | 0~100 |
| `framing_score` | O | 0~1 |
| `facing_delta` | X | 0~1. 안 보내면 통과 처리되지만 **몸이 돌아간 사진을 못 거릅니다** |
| `pose_scale_basis` | O | ⚠️ **레퍼런스와 같아야 합니다.** 다르면 422 |

**⚠️ 레퍼런스가 먼저 등록돼 있어야 합니다.** 없으면 409 `PRECONDITION_NOT_MET`.

---

## ⚠️ 거울 촬영 — `is_mirrored`

거울로 찍은 사진은 좌우가 **물리적으로 뒤집혀** 있습니다. 그대로 두면 왼팔 진단이 오른팔에 붙고, 인바디 좌우 수치가 교차합니다. **에러는 하나도 안 납니다.**

서버가 `is_mirrored=true` 를 받으면 저장 직전에 이미지와 랜드마크를 되돌립니다. 응답의 `pose_landmarks` 는 **되돌린 뒤** 값이므로 촬영 화면 기준값으로 그대로 쓰면 됩니다.

**값을 정하는 방법**

| 촬영 방식 | `is_mirrored` |
|---|---|
| 앱 내 웹캠 촬영 (`CAPTURE`) | **항상 `false`** |
| 파일 업로드 (`UPLOAD`) | 알 수 없음 → **체크박스로 사용자에게 물어보기** (기본 꺼짐) |

**⚠️ 웹캠 미러링은 CSS로만 하세요.** 화면에는 거울처럼 보여주되, **서버로 보내는 이미지와 랜드마크는 반전되지 않은 카메라 원본**이어야 합니다. 캔버스에서 뒤집어 보내면 `is_mirrored=false` 인데 실제로는 뒤집힌 사진이 들어와 좌우가 통째로 어긋납니다.

---

## 에러 처리

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
| 401 | `INVALID_USER_ID` | 헤더는 있는데 UUID 형식이 아님 → **저장된 값이 깨진 것.** `POST /users` 로 재발급 |
| 404 | `NOT_FOUND` (세션 생성 시) | **저장된 `user_id` 가 서버에 없음.** DB 초기화 등. `POST /users` 로 재발급 |
| 405 | `METHOD_NOT_ALLOWED` | 요청 메서드가 틀림 (개발 중 실수) |
| 500 | `INTERNAL_ERROR` | 서버 오류. `message` 를 그대로 보여주고 재시도 안내 |
| 404 | `NOT_FOUND` | 없거나 **남의 것**. 403을 주지 않는 건 의도된 설계입니다 |
| 404 | `NO_ACTIVE_SESSION` | 진행 중인 분석 없음. **오류가 아니라 상태입니다** — 시작 화면으로 |
| 409 | `ACTIVE_SESSION_EXISTS` | `detail.session_id` 로 **이어서 진행**하거나, 처음부터 다시 하려면 archive 후 재생성 |
| 409 | `PRECONDITION_NOT_MET` | 선행 단계 미완료 (예: 레퍼런스 없이 사용자 사진) |
| 413 | `FILE_TOO_LARGE` | 10MB 초과 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 이미지로 열리지 않는 파일 (해상도가 과도하게 큰 경우 포함) |
| 422 | `POSE_MISMATCH` | **1차 검사** 미달. `detail.reason` 별로 문구를 나눌 것 (아래) |
| 422 | `UNSUITABLE_PHOTO` | **2차 검사** 미달. `message`를 그대로 보여주고 재촬영 유도 |
| 422 | `MULTI_PERSON` | "혼자 나오도록 촬영해주세요" |

**`POSE_MISMATCH` 의 `detail.reason`**

| reason | 사용자가 해야 할 행동 | 예시 문구 |
|---|---|---|
| `POSE` | 자세를 바꿔야 함 | "레퍼런스와 포즈를 맞춰주세요" |
| `FRAMING` | 카메라와의 거리·위치를 바꿔야 함 | "몸이 화면에 다 나오도록 서주세요" |
| `FACING` | 몸이 옆으로 돌아감 | "정면을 보고 서주세요" |
| `NO_PERSON` | 사람이 안 잡힘 | "전신이 보이도록 다시 촬영해주세요" |

넷은 **서로 다른 지시**입니다. 하나로 뭉뚱그리면 사용자가 뭘 고쳐야 할지 모릅니다.

### 2차 검사 — `UNSUITABLE_PHOTO`

1차(자세)를 통과해도 **옷에 몸이 묻히거나 레퍼런스와 촬영 거리가 딴판이면** 부위 비교가 무의미합니다. 서버가 업로드 시점에 AI로 판정합니다.

```json
{ "error": { "code": "UNSUITABLE_PHOTO",
             "message": "옷이 헐렁해 몸의 윤곽이 보이지 않습니다. 몸에 붙는 옷으로 다시 촬영해주세요.",
             "detail": { "reason": "LOOSE_CLOTHING", "confidence": "HIGH" } } }
```

`message`를 **그대로 보여주면 됩니다.** `detail.reason` 은 아래 중 하나입니다.

| reason | 사용자가 해야 할 일 |
|---|---|
| `LOOSE_CLOTHING` | 몸에 붙는 옷으로 갈아입고 재촬영 |
| `PERSPECTIVE_MISMATCH` | 촬영 거리 조정 |
| `CROPPED` | 몸통·팔·다리가 다 담기게 재촬영 |
| `NO_PERSON` / `MULTI_PERSON` | 혼자 나오게 재촬영 |
| `TOO_DARK` | 밝은 곳으로 이동 |
| `BLURRY` | 초점 맞추고 흔들리지 않게 재촬영 |
| `OTHER` | (message 만 표시) |

⚠️ **`reason` 으로 분기하지 마세요.** 같은 사진인데도 `CROPPED` 와 `PERSPECTIVE_MISMATCH` 처럼 둘 다 맞는 코드 사이를 오갑니다(실측 확인). 통과/반려 자체는 흔들리지 않습니다. 화면에는 `message` 를 그대로 쓰고, `reason` 은 로그·통계용으로만 보세요.

> 💡 **머리나 발이 잘린 사진은 반려되지 않습니다.** 크기 정규화를 어깨~골반(또는 골반~무릎)으로 하기 때문에 머리·발은 필요 없습니다. 실측으로 확인했습니다 — 목 아래만 나온 사진도 통과합니다.

⚠️ **이 검사 때문에 업로드 응답이 2~5초 걸립니다.** AI 호출을 기다리기 때문입니다. 로딩 표시가 필요합니다 — 그 자리에서 재촬영을 유도하는 게 목적이라 일부러 동기로 만들었습니다. (실측 1.1~3.6초)

**⚠️ 판정에 실패한 사진은 저장되지 않습니다.** 재촬영 UI가 반드시 필요합니다.

---

## 이미지 규칙

- **형식**: `jpeg` / `png` / **`heic`(아이폰 원본)** / `webp` 등. 크기 10MB 이하
  - 서버가 `Content-Type` 헤더를 보지 않고 **실제로 열리는지**로 판단합니다. 브라우저가 HEIC에 이상한 타입을 붙여 보내도 통과합니다
  - **프론트에서 미리 JPEG로 변환할 필요 없습니다.** 원본 그대로 보내면 서버가 변환해 저장합니다
- **EXIF 회전은 서버가 처리합니다.** 프론트에서 미리 돌릴 필요 없습니다
- 긴 변 4096px 초과 시 서버가 축소해 저장합니다
- **재업로드는 교체입니다.** 기존 세그멘테이션 결과도 함께 지워집니다
- 이미지는 전부 private 버킷에 있고 **signed URL** 로만 접근합니다 (만료 1시간)

---

## 화면 복귀 — `GET /api/v1/sessions/active`

새로고침·재진입 시 어느 화면으로 보낼지 판단하는 용도입니다.

```json
{
  "session_id": "3c9a1b7e-...",
  "status": "ACTIVE",
  "contraindications": [],
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

진행 중 세션이 없으면 404입니다.

---

## 비동기 작업 폴링 — `GET /api/v1/jobs/{job_id}`

사진 업로드 응답의 `job_id` 로 세그멘테이션 진행 상황을 확인합니다. **1.5초 간격** 권장.

`status` 는 `PENDING` → `PROCESSING` → `DONE` / `FAILED`.

`DONE` 이면 `result` 에 요약이 들어옵니다.

```json
{ "segmentation_id": "f1e2...", "photo_kind": "USER",
  "detected": 12, "valid_comparable": 7,
  "invalid": [ { "class_name": "Left_Lower_Leg", "reason": "TOO_SMALL" } ] }
```

`invalid` 를 쓰면 세그 완료 즉시 **"왼쪽 종아리는 노출이 부족합니다. 반바지 차림으로 다시 찍어주세요"** 같은 안내를 낼 수 있습니다.

> ⚠️ `job.status`(작업 실행 상태)와 도메인 테이블의 `status`(화면에 써도 되는지)는 **다릅니다.**

---

## 부위별 색칠 오버레이 — `GET /api/v1/photos/{photo_id}/segmentation`

세그멘테이션 결과는 **부위별 이미지 N장이 아니라 라벨 맵 PNG 1장**입니다. 8-bit 그레이스케일이고 **픽셀 값이 곧 부위 번호**입니다.

응답에 `palette` 가 함께 옵니다. **번호 ↔ 부위명 ↔ 색 ↔ 유효성 ↔ 통계를 서버가 합쳐서 내려주므로 따로 조회할 필요가 없습니다.**

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
      "pixel_count": 48210, "area_ratio": 0.212,
      "bbox": { "x": 210, "y": 180, "w": 340, "h": 420 } }
  ],
  "signed_url_expires_at": "..."
}
```

세그가 아직 안 끝났으면 **404**입니다. `GET /jobs/{job_id}` 로 완료를 확인하고 부르세요.

### ⚠️ 오버레이 그릴 때 꼭 지킬 것 3가지

**1. `crossOrigin = "anonymous"` 없으면 픽셀을 못 읽습니다.**
signed URL은 다른 오리진이라, 이게 없으면 캔버스가 오염돼서 `getImageData()`가 `SecurityError`를 던집니다. **여기서 제일 먼저 막힙니다.**

**2. 맵을 JS로 리샘플하지 마세요.**
보간이 라벨 값을 섞어서 **존재하지 않는 부위를 만들어냅니다.** 크기 조정은 CSS로만 하고 `image-rendering: pixelated` 를 함께 주세요.

**3. `palette` 를 하드코딩하지 마세요.**
모델 버전이 바뀌면 `label_value` 가 재배열됩니다. 응답의 `palette` 를 그대로 쓰세요. `color_hex` 가 `null` 인 항목(배경·옷·머리)은 **칠하지 않습니다.**

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
// 원본 위에 CSS로 겹치기. 맵 크기 ≠ 원본 크기일 수 있으니 늘려서 맞춤
```

### ⚠️ `bbox` 는 맵 좌표계입니다 — x·y 배율이 **서로 다릅니다**

모델이 사진을 고정 크기(현재 768×1024)로 리사이즈해서 추론하기 때문에, **원본과 가로세로 비율이 다릅니다.**

```
원본 700×1049 (1.50)  →  맵 768×1024 (1.33)   ← 세로로 눌림
```

그래서 원본 위에 박스를 그릴 때 **하나의 배율로 곱하면 어긋납니다.** x와 y를 각각 계산하세요.

```js
const sx = seg.photo_width  / seg.map_width;
const sy = seg.photo_height / seg.map_height;   // ⚠️ sx 와 다르다

const box = {
  x: p.bbox.x * sx, y: p.bbox.y * sy,
  w: p.bbox.w * sx, h: p.bbox.h * sy,
};
```

색칠 오버레이 자체는 CSS로 원본 크기에 맞춰 늘리면 (`width:100%; height:100%`) 자동으로 맞습니다 — 비율이 달라도 상관없습니다. **박스·좌표를 직접 계산할 때만** 주의하면 됩니다.

---

## 좌우 비교 화면 — `GET /api/v1/sessions/{session_id}/segmentation`

레퍼런스·사용자 두 장을 한 번에 받습니다. 아직 세그가 안 끝난 쪽은 `null` 입니다 (404가 아닙니다 — 한쪽만 끝나도 화면을 그릴 수 있게).

```json
{
  "reference": { "...위와 동일..." },
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

**`excluded` 가 재촬영 안내의 재료입니다.** `message` 는 그대로 보여줘도 되게 쓰여 있습니다. `side` 로 어느 사진이 문제인지 알 수 있습니다 (`REFERENCE` / `USER` / `BOTH`).

**`sufficient: false`** 면 비교 가능한 부위가 부족한 상태입니다. 분석을 시작해도 422로 막히니, 이 단계에서 `excluded` 를 보여주고 재촬영을 유도하세요.

---

## signed URL 재발급 — `POST /api/v1/storage/signed-urls`

URL은 1시간 뒤 만료됩니다. 화면을 오래 열어두면 이미지가 깨지므로 다시 발급받으세요.

```json
{ "items": [ { "bucket": "segmentations", "path": "8f14.../3c9a.../user/map.png" } ],
  "expires_in": 3600 }
```

한 번에 최대 30개. `photos` / `segmentations` / `body-parts` 버킷만 발급됩니다.

---

## 계정 삭제 — `DELETE /api/v1/users/me`

204를 반환합니다. **사진·세그멘테이션 결과·세션·기록이 전부 실제로 지워집니다.** 되돌릴 수 없으니 확인 다이얼로그가 필요합니다.

---

## 인바디 (F07) — 구현됨, 상세는 Swagger

인바디 결과지 업로드·OCR·수정 API가 살아 있습니다. 다만 **이 문서에는 상세를 안 적었습니다** — 사진 파이프라인 담당이 쓴 문서라서요.

```
POST   /api/v1/sessions/{session_id}/inbody     결과지 업로드 → OCR 큐잉
GET    /api/v1/sessions/{session_id}/inbody     업로드한 결과지 목록
GET    /api/v1/inbody/{inbody_id}               추출값 + 검증 등급
PATCH  /api/v1/inbody/{inbody_id}               사용자 확인·수정
DELETE /api/v1/inbody/{inbody_id}               삭제
```

요청·응답 필드는 [Swagger](http://localhost:8000/docs)의 `inbody` 태그에서 확인하세요. 업로드도 비동기라 `job_id` 폴링은 위와 같은 방식입니다.

---

## 아직 없는 것

진단(F08·F09) · 루틴(F10~F12) — 작업 중입니다. 호출하면 404입니다.

---

## 로컬 서버 실행

```bash
uvicorn app.main:app --reload --port 8000
```

Swagger: http://localhost:8000/docs
