# API 명세 — 기능 중심

| | |
|---|---|
| **버전** | v2 |
| **최종 수정일** | 2026-08-13 |
| **기준 DB** | `docs/db-design-v4.md` |
| **Base URL** | `/api/v1` |
| **이전 버전** | `docs/api-spec-v1.md` (엔드포인트 나열식) |

**v1 대비 변경** — 기능 단위로 재구성 / 세그멘테이션 맵 기능(F06) 추가 / 부위별 크롭 조회가 맵 + 통계 조회로 대체 / 미구현 기능(로그인 등)도 목록에 명시

---

## 기능 목록 한눈에

| # | 기능 | 상태 | 담당 | 대표 엔드포인트 |
|---|---|---|---|---|
| **F01** | **로그인 / 회원가입** | 🔴 **미구현** | — | (없음 — F02로 대체) |
| F02 | 사용자 식별자 발급 | 🟢 구현 | A | `POST /users` |
| F03 | 분석 세션 관리 | 🟢 구현 | A | `POST /sessions` |
| F04 | 레퍼런스 사진 등록 | 🟢 구현 | A | `POST /sessions/{id}/photos/reference` |
| F05 | 실시간 포즈 촬영 / 직접 업로드 | 🟢 구현 | A | `POST /sessions/{id}/photos/user` |
| **F06** | **부위별 세그멘테이션 + 시각화** | 🟢 구현 | A | `GET /photos/{id}/segmentation` |
| F07 | 인바디 결과지 인식 | 🟢 구현 | B | `POST /sessions/{id}/inbody` |
| F08 | 부위별 비교 진단 | 🟢 구현 | B | `POST /sessions/{id}/analysis` |
| F09 | 종합 진단 (유사도 점수) | 🟢 구현 | B | `GET /sessions/{id}/analysis` |
| F10 | 4주 루틴 생성 / 운동 일수 조정 | 🟢 구현 | B | `POST /sessions/{id}/routines` |
| F11 | 오늘의 루틴 | 🟢 구현 | B | `GET /sessions/{id}/routines/today` |
| F12 | 운동 완료 + 피드백 반영 | 🟢 구현 | B | `POST /sessions/{id}/workout-logs` |
| F13 | 진행 상태 조회 (폴링) | 🟢 구현 | A | `GET /jobs/{job_id}` |
| F14 | 이미지 접근 (signed URL) | 🟢 구현 | A | `POST /storage/signed-urls` |
| F15 | 데이터 삭제 | 🟢 구현 | A | `DELETE /users/me` |
| **F16** | **알림 / 푸시** | 🔴 미구현 | — | (요구 없음) |
| **F17** | **운동 통계 · 히스토리 대시보드** | 🔴 미구현 | — | (요구 없음) |
| **F18** | **레퍼런스 프리셋 갤러리** | 🟡 미정 | — | `reference_source='PRESET'` 자리만 있음 |

---

## F00. 공통 규약

### 헤더

| 헤더 | 필수 | 설명 |
|---|---|---|
| `X-User-Id` | O | `users.user_id` (UUID v4). 없으면 401 |

**예외** (헤더 불필요): `POST /users`, `GET /body-parts`

### 소유권 검증

```
X-User-Id → users 존재 확인
          → 경로의 id를 analysis_session.user_id 까지 조인해 일치 확인
```

- FastAPI 의존성 하나(`Depends(get_owned_session)`)로 몰아넣습니다. 라우터마다 개별 구현 금지.
- ⚠️ **소유권 불일치는 403이 아니라 404.** 403은 "그 id는 존재한다"를 알려줍니다.

### 에러 형식

```json
{ "error": { "code": "POSE_MISMATCH", "message": "사용자에게 보여줄 문구",
             "detail": { "pose_similarity": 71.2 } } }
```

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 형식 오류 |
| 401 | `MISSING_USER_ID` | 헤더 없음/형식 오류 |
| 404 | `NOT_FOUND` | 없음 **또는 소유자 불일치** |
| 409 | `ACTIVE_SESSION_EXISTS` | 진행 중 세션 존재 |
| 409 | `PRECONDITION_NOT_MET` | 선행 잡 미완료 |
| 409 | `ALREADY_LOGGED` | 같은 Day 중복 완료 |
| 413 | `FILE_TOO_LARGE` | 10MB 초과 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | jpeg/png 외 |
| 422 | `POSE_MISMATCH` | 포즈/프레이밍 미달 |
| 422 | `INSUFFICIENT_PARTS` | 비교 가능 부위 3개 미만 |
| 422 | `MULTI_PERSON` | 인물 2명 이상 |
| 500 | `INTERNAL_ERROR` | 그 외 |

### 비동기 규약

무거운 작업은 **202 + `job_id`**. 프론트는 `GET /jobs/{job_id}` 폴링 (세그/OCR 1.5초, VLM/루틴 2초).

⚠️ `job.status`(실행 상태)와 도메인 테이블의 `status`(화면에 써도 되는지)는 다릅니다.

### 업로드 제한

jpeg/png · 10MB/파일 · 긴 변 4096px(초과 시 서버 리사이즈) · 인바디 1건당 5장

---

# F01. 로그인 / 회원가입 🔴 미구현

**현재 상태 — 로그인 기능은 없습니다.** 대신 F02의 UUID 식별자를 `X-User-Id` 헤더로 전달해 사용자를 구분합니다.

### ⚠️ 이 결정이 만드는 제약 (문서에 남겨둘 것)

- `user_id`를 아는 사람은 그 유저의 데이터에 접근할 수 있습니다. **로그인이 없는 이상 구조적으로 못 막습니다.**
- `user_id`는 만료도 무효화도 없어 한 번 새면 영구적으로 유효합니다.
- 방어선은 "추측 불가능하게 만들기" 하나뿐이고, 그게 UUID v4 PK입니다.
- 사용자가 브라우저 저장소를 지우거나 기기를 바꾸면 **데이터 복구 수단이 없습니다.** 프론트에 안내 필요.

### 나중에 붙일 때

`users.user_id`가 이미 UUID이고 Supabase Auth의 `auth.users.id`도 UUID입니다. 도입 시 `users.auth_user_id UUID UNIQUE`를 추가해 연결하면 됩니다. **지금 미리 컬럼을 넣지 않습니다.**

바뀔 것: `X-User-Id` 헤더 → `Authorization: Bearer <jwt>`, `get_owned_session` 의존성에서 사용자 추출 방식만. **엔드포인트 경로와 응답 형식은 그대로입니다.**

---

# F02. 사용자 식별자 발급

> **화면** — 앱 최초 진입 시 자동 호출 (사용자에게 보이지 않음)

| 엔드포인트 | 설명 |
|---|---|
| `POST /users` | 식별자 발급 (헤더 불필요) |
| `GET /users/me` | 저장된 식별자 유효성 확인 |

### `POST /users` → 201

```json
{ "user_id": "8f14e45f-ceea-467a-9b21-0c3e7d1a55b2",
  "is_pro_user": false, "created_at": "2026-08-13T04:21:00Z" }
```

### `GET /users/me` → 200 (없으면 404)

**⚠️ 프론트 주의**
- `user_id`를 로컬 스토리지에 보관하고 이후 모든 요청에 `X-User-Id`로 실어 보냅니다.
- 앱 진입 시 `GET /users/me`로 유효성을 먼저 확인하세요. DB가 초기화됐는데 로컬에 옛 id가 남아 있으면 모든 요청이 401/404로 떨어집니다.
- "기기를 바꾸거나 브라우저 데이터를 지우면 기록이 사라집니다" 안내 필요.

---

# F03. 분석 세션 관리

> **화면** — 레퍼런스 업로드 화면 진입 시 / 새로고침 후 복귀 시

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions` | 새 세션 시작 |
| `GET /sessions/active` | 진행 중 세션 + **단계별 완료 여부** |
| `GET /sessions` | 세션 목록 |
| `PATCH /sessions/{session_id}` | `ARCHIVED` 처리 |

### `POST /sessions` → 201

```json
{ "session_id": "3c9a1b7e-...", "status": "ACTIVE",
  "reference_source": "USER_UPLOAD", "created_at": "..." }
```

**409 `ACTIVE_SESSION_EXISTS`** — `detail.session_id`로 이어서 진행하면 됩니다.
(`UNIQUE (user_id) WHERE status='ACTIVE'` 제약 때문)

### `GET /sessions/active` → 200

**새로고침/재진입 시 어느 화면으로 보낼지** 판단하는 용도입니다.

```json
{
  "session_id": "3c9a1b7e-...",
  "status": "ACTIVE",
  "contraindications": ["무릎 굽힘 부하"],
  "steps": {
    "reference_photo": { "uploaded": true, "segmented": true },
    "user_photo":      { "uploaded": true, "segmented": false, "job_status": "PROCESSING" },
    "inbody":          { "count": 2, "done": 1, "failed": 0, "pending": 1 },
    "analysis":        { "part_done": 3, "part_total": 9, "overall_status": "PENDING" },
    "routine":         { "active_version": null, "status": null }
  },
  "created_at": "..."
}
```

---

# F04. 레퍼런스 사진 등록

> **화면** — 레퍼런스 업로드 → 다음 단계 버튼

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions/{session_id}/photos/reference` | 업로드 + **포즈 즉시 추출** + 세그 큐잉 |
| `GET /sessions/{session_id}/photos/reference` | 촬영 화면용 기준값 조회 |

### ⚠️ 처리 분할 — 여기가 설계의 핵심

| 처리 | 방식 | 이유 |
|---|---|---|
| MediaPipe Pose | **동기** (수백 ms) | 촬영 화면의 실시간 비교 기준값. 없으면 다음 화면으로 못 감 |
| Sapiens2 세그멘테이션 | **비동기** (수십 초) | 무거움. 촬영 중에 백그라운드로 돌리면 됨 |

둘을 한 잡에 묶으면 사용자가 세그가 끝날 때까지 촬영 화면에 못 들어갑니다.

### `POST .../photos/reference`

**Request** — `multipart/form-data`: `file` (필수)

**Response 201**
```json
{
  "photo_id": "a1b2...",
  "job_id": "9f8e...",
  "width": 1080, "height": 1440,
  "pose_scale_basis": "TORSO",
  "pose_landmarks": [ { "index": 0, "x": 0.51, "y": 0.18, "z": -0.32, "visibility": 0.99 } ],
  "signed_url": "https://...?token=...",
  "signed_url_expires_at": "2026-08-13T05:21:00Z"
}
```

**주의**
- ⚠️ **재업로드는 교체입니다.** 삭제 순서: `body-parts` 크롭 → `segmentations` 맵 → `photos` 원본 → `photo` 행. 어기면 고아 파일이 남습니다.
- ⚠️ 사람 미검출 → 422 `POSE_MISMATCH`(`reason="NO_PERSON"`), 2명 이상 → 422 `MULTI_PERSON`
- ⚠️ 저장 이미지와 landmarks는 **반전되지 않은 원본 기준.** 미러링은 CSS만.

### `GET .../photos/reference` → 200

위 201과 같은 형태 + `"segmented": true|false`

---

# F05. 실시간 포즈 촬영 / 직접 업로드

> **화면** — 왼쪽 레퍼런스 / 오른쪽 웹캠. 유사도 90% 도달 시 자동 촬영. 또는 직접 업로드

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions/{session_id}/photos/user` | 촬영본/업로드본 저장 (**서버 재검증**) |

### 역할 분담

| 값 | 계산 주체 | 이유 |
|---|---|---|
| 실시간 비교 (매 프레임) | **프론트** | 서버 왕복 불가 |
| 저장 시점 P/F 확정값 | **서버 재계산** | 프론트 값은 조작 가능 + MediaPipe 버전 차로 어긋남 |

### `POST .../photos/user`

**Request** — `multipart/form-data`

| 필드 | 필수 | 설명 |
|---|---|---|
| `file` | O | 촬영본 또는 업로드본 |
| `capture_source` | O | `CAPTURE` \| `UPLOAD` |
| `client_pose_similarity` | X | 프론트 계산값 (로깅·추적용) |
| `client_framing_score` | X | 프론트 계산값 (로깅·추적용) |

**Response 201**
```json
{ "photo_id": "b2c3...", "job_id": "7d6c...",
  "pose_similarity": 92.4, "framing_score": 0.87,
  "pose_scale_basis": "TORSO", "multi_person": false }
```

**Response 422 `POSE_MISMATCH`**
```json
{ "error": { "code": "POSE_MISMATCH",
  "message": "레퍼런스와 포즈가 충분히 일치하지 않습니다. 다시 촬영해주세요.",
  "detail": { "pose_similarity": 71.2, "framing_score": 0.83,
              "threshold": 90.0, "f_min": 0.80, "reason": "POSE" } } }
```

`detail.reason` — `POSE`(포즈 불일치) \| `FRAMING`(프레이밍 불일치) \| `NO_PERSON`
> 화면 안내 문구가 달라야 하므로 나눕니다. "포즈를 맞춰주세요" vs "몸이 화면에 다 나오게 서주세요"는 다른 지시입니다.

**서버 처리 순서**
1. 레퍼런스 `pose_landmarks` / `pose_scale_basis` 로드 (없으면 409 `PRECONDITION_NOT_MET`)
2. MediaPipe 추출 → **레퍼런스와 같은 `pose_scale_basis`로** 정규화
3. `F = Jaccard(레퍼런스 인물 bbox, 사용자 인물 bbox)`, `P = 관절 각도 유사도`
4. 최종 `= (F ≥ F_MIN) ? P : 0` → `THRESHOLD` 미만이면 **저장하지 않고** 422
5. 통과 시 저장 → `photo(kind='USER')` → `job(kind='SEG_USER')` 큐잉

**주의**
- ⚠️ `THRESHOLD` / `F_MIN` / `TOL`은 `.env`로. 코드에 박지 마세요 (튜닝 대상 잠정값).
- ⚠️ **레퍼런스가 `HIP_KNEE` 기준이면 사용자도 `HIP_KNEE`로 재야 합니다.** 서버가 레퍼런스 값을 강제하고, 못 재면 422(`reason="FRAMING"`).
- ⚠️ 프론트가 90%로 촬영했는데 서버가 422를 주는 경우가 생깁니다. **`client_*` 값과 서버 값을 함께 로그에 남기세요.** 차이가 크면 임계값이 아니라 구현이 어긋난 것입니다.

---

# F06. 부위별 세그멘테이션 + 시각화 🆕

> **화면** — 분석 결과에서 원본 사진 위에 부위별 색칠 오버레이

| 엔드포인트 | 설명 |
|---|---|
| `GET /photos/{photo_id}/segmentation` | 라벨 맵 + 팔레트 + 부위별 통계 |
| `GET /sessions/{session_id}/segmentation` | 레퍼런스·사용자 두 장을 한 번에 |
| `GET /body-parts` | 마스터 (라벨·색·비교 대상 여부) |

### 저장 방식

부위별 이미지 N장이 아니라, **부위 라벨이 픽셀 값으로 들어간 PNG 1장**입니다.

```
map.png  8-bit 그레이스케일 · 픽셀 값 = label_value
         0=Background, 1=Torso, 2=Left_Upper_Arm, ...
```

값 ↔ 클래스명 대응은 `label_map`으로 **응답에 함께 내려갑니다.** ⚠️ 모델 버전이 바뀌면 값이 재배열되므로 **프론트가 이 매핑을 하드코딩하면 안 됩니다.**

### `GET /photos/{photo_id}/segmentation` → 200

```json
{
  "segmentation_id": "f1e2...",
  "photo_id": "a1b2...",
  "kind": "REFERENCE",
  "map_url": "https://.../map.png?token=...",
  "map_width": 768, "map_height": 1024,
  "photo_url": "https://.../reference.jpg?token=...",
  "photo_width": 1080, "photo_height": 1440,
  "model": { "name": "sapiens2", "version": "sapiens2-1b-goliath" },
  "person_area_ratio": 0.28,
  "palette": [
    { "label_value": 1, "class_name": "Torso",
      "name_ko": "몸통", "color_hex": "#4C6EF5",
      "is_comparable": true, "is_valid": true,
      "pixel_count": 48210, "area_ratio": 0.212,
      "bbox": { "x": 210, "y": 180, "w": 340, "h": 420 } },
    { "label_value": 2, "class_name": "Left_Upper_Arm",
      "name_ko": "왼팔 상완", "color_hex": "#F76707",
      "is_comparable": true, "is_valid": false, "invalid_reason": "TOO_SMALL",
      "pixel_count": 620, "area_ratio": 0.003,
      "bbox": { "x": 120, "y": 340, "w": 60, "h": 90 } },
    { "label_value": 17, "class_name": "Upper_Clothing",
      "name_ko": "상의", "color_hex": null,
      "is_comparable": false, "is_valid": false, "invalid_reason": "NOT_COMPARABLE",
      "pixel_count": 91300, "area_ratio": 0.401,
      "bbox": { "x": 180, "y": 150, "w": 420, "h": 500 } }
  ]
}
```

**⚠️ 응답 설계 포인트**
- `palette`가 **`label_map` + `body_part` 마스터 + 부위별 통계를 합친 한 덩어리**입니다. 프론트가 3번 조회할 필요가 없습니다.
- `color_hex: null`인 항목(배경·옷·머리 등)은 **칠하지 않습니다.** 색을 프론트가 정하게 두면 부위가 추가될 때 어긋납니다.
- `is_valid: false` + `invalid_reason`이 있어야 **"왼팔은 노출이 부족해 비교에서 제외됐습니다"** 안내를 낼 수 있습니다. v1에서는 아예 목록에 없어서 이유를 설명할 수 없었습니다.
- `bbox`는 **맵 좌표계**입니다. 원본 위에 그리려면 `photo_width / map_width` 배율로 스케일하세요.

### 프론트엔드 오버레이 절차

```js
const map = new Image();
map.crossOrigin = "anonymous";        // ⚠️ 필수. 없으면 getImageData가 SecurityError
map.src = seg.map_url;
await map.decode();

const c = document.createElement("canvas");
c.width = seg.map_width; c.height = seg.map_height;
const ctx = c.getContext("2d", { willReadFrequently: true });
ctx.drawImage(map, 0, 0);

const src = ctx.getImageData(0, 0, c.width, c.height);
const out = ctx.createImageData(c.width, c.height);
const lut = {};                        // label_value → [r,g,b]
for (const p of seg.palette) {
  if (!p.color_hex) continue;          // 배경·옷은 투명
  lut[p.label_value] = [1,3,5].map(i => parseInt(p.color_hex.substr(i,2),16));
}
for (let i = 0; i < src.data.length; i += 4) {
  const rgb = lut[src.data[i]];        // 그레이스케일이므로 R 채널 = 라벨 값
  if (!rgb) continue;
  out.data[i]=rgb[0]; out.data[i+1]=rgb[1]; out.data[i+2]=rgb[2]; out.data[i+3]=140;
}
ctx.putImageData(out, 0, 0);
// 원본 위에 CSS로 겹치기 + image-rendering: pixelated
```

**⚠️ 프론트가 반드시 알아야 할 3가지**
1. **`crossOrigin = "anonymous"` 없으면 캔버스가 오염되어 `getImageData()`가 던집니다.** signed URL은 다른 오리진입니다. 여기서 제일 먼저 막힙니다. (서버 쪽 CORS 설정은 불필요 — Supabase Storage가 `Access-Control-Allow-Origin: *`를 이미 내려줍니다)
2. **맵을 JS로 리샘플하지 마세요.** 보간이 라벨 값을 섞어 존재하지 않는 클래스를 만듭니다. 크기 조정은 CSS로만, `image-rendering: pixelated` 함께.
3. **`label_map`을 하드코딩하지 마세요.** 응답의 `palette`를 그대로 씁니다.

### `GET /sessions/{session_id}/segmentation` → 200

```json
{ "reference": { "...F06 응답과 동일..." },
  "user": { "...동일..." },
  "comparable": {
    "class_names": ["Torso", "Right_Upper_Arm", "Right_Lower_Arm"],
    "count": 3, "sufficient": true, "min_required": 3,
    "reference_only": ["Left_Upper_Arm"], "user_only": [],
    "excluded": [ { "class_name": "Left_Upper_Arm", "side": "USER",
                    "reason": "TOO_SMALL", "message": "왼팔 상완이 옷에 가려져 있습니다" } ] } }
```

> 좌우 비교 화면 한 번에 그릴 수 있게 묶었습니다. `excluded`는 재촬영 안내 문구를 만드는 데 씁니다.

### `GET /body-parts` → 200 (헤더 불필요)

```json
{ "items": [ { "class_name": "Torso", "name_ko": "몸통", "part_group": "CORE",
               "inbody_segment": "TRUNK", "is_comparable": true,
               "color_hex": "#4C6EF5", "display_order": 1 } ] }
```

범례(legend) 표시용. 워커도 기동 시 이걸 읽어 `SKIN_CLASSES`를 대체합니다.

---

# F07. 인바디 결과지 인식

> **화면** — 인바디 업로드(선택) → 다음 단계 → 나중에 추출값 확인·수정

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions/{session_id}/inbody` | 결과지 업로드 → OCR 큐잉 |
| `GET /sessions/{session_id}/inbody` | 업로드한 결과지 목록 |
| `GET /inbody/{inbody_id}` | 추출값 + 검증 등급 |
| `PATCH /inbody/{inbody_id}` | 사용자 확인·수정 |
| `DELETE /inbody/{inbody_id}` | 삭제 |

### `POST .../inbody`

**Request** — `multipart/form-data`: `files` (1~5장, **한 건의 여러 페이지**), `device_type` (선택)

> **요청 1건 = 결과지 1건.** 여러 측정 건을 올릴 때는 여러 번 호출합니다.

**Response 202** — `{ "inbody_id": "c3d4...", "job_id": "6e5f...", "status": "PENDING" }`

⚠️ 임시 이미지 경로는 `job.payload`에 넣고, `DONE` **직후** 삭제합니다. `FAILED`면 재처리를 위해 남깁니다.

### `GET /inbody/{inbody_id}` → 200

```json
{
  "inbody_id": "c3d4...", "status": "DONE",
  "device_type": "InBody570", "measured_at": "2026-08-01",
  "fields": { "age": 27, "gender": "MALE", "height": 175.0, "weight": 72.4,
              "bmi": 23.6, "body_fat_mass": 14.2, "body_fat_percentage": 19.6,
              "skeletal_muscle_mass": 33.1, "fat_free_mass": 58.2, "bmr_kcal": 1642 },
  "segments": [ { "segment": "LEFT_ARM", "lean_mass": 3.1, "fat_mass": 0.9 } ],
  "validation": {
    "bmi":               { "level": "warn",  "message": "체중/신장 계산값과 0.4 차이" },
    "segments.LEFT_ARM": { "level": "warn",  "message": "좌우 팔 근육량 차이 6.5%" },
    "bmr_kcal":          { "level": "error", "message": "추출 실패" }
  },
  "verified_at": null
}
```

⚠️ 확인 화면은 `level`이 `warn`/`error`인 필드만 강조합니다. 전부 똑같이 보여주면 사용자가 대충 넘깁니다.

### `PATCH /inbody/{inbody_id}`

```json
{ "fields": { "weight": 72.0, "bmr_kcal": 1650 },
  "segments": [ { "segment": "LEFT_ARM", "lean_mass": 3.2 } ],
  "verified": true }
```

**주의**
- ⚠️ `raw_ocr`은 **덮어쓰지 않습니다.** 원본과 수정본을 구분해야 OCR 정확도를 평가할 수 있습니다.
- ⚠️ 수정 후 `validation`을 **재계산**합니다. 고친 값이 또 항등식을 깨면 다시 `warn`이 떠야 합니다.
- `status='FAILED'`인 건도 수동 입력으로 `DONE`으로 올릴 수 있게 허용합니다.

---

# F08. 부위별 비교 진단

> **화면** — 로딩 페이지 (`완료 3/9`)

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions/{session_id}/analysis` | 부위별 + 종합 VLM 큐잉 |
| `GET /sessions/{session_id}/analysis/progress` | 진행률 |

### `POST .../analysis` → 202

```json
{ "part_jobs": [ { "job_id": "...", "class_name": "Torso" } ],
  "overall_job_id": "...", "part_count": 5 }
```

**선행 조건** — `SEG_REFERENCE`, `SEG_USER` 모두 `DONE` (아니면 409 `PRECONDITION_NOT_MET`)

**422 `INSUFFICIENT_PARTS`**
```json
{ "error": { "code": "INSUFFICIENT_PARTS",
  "message": "비교 가능한 부위가 2개뿐입니다. 팔·다리가 드러나는 복장으로 다시 촬영해주세요.",
  "detail": { "count": 2, "min_required": 3,
              "excluded": [ { "class_name": "Left_Upper_Arm", "reason": "TOO_SMALL" } ] } } }
```

**주의**
- ⚠️ **중복 호출 = 요금 2배.** 이미 `PENDING`/`PROCESSING` 잡이 있으면 **새로 만들지 말고 기존 `job_id`를 반환**하세요. 새로고침 한 번에 VLM이 두 배로 돕니다.
- ⚠️ **인바디는 선행 조건이 아닙니다.** 아직 `PENDING`이면 **기다리지 말고 인바디 없이 진행**하고 그 사실을 `job.result`에 남기세요. 사용자를 로딩 화면에 무한정 세우면 안 됩니다.
- ⚠️ VLM 입력은 `body_part_segment.crop_path`(있으면) 또는 맵 + 원본에서 즉석 생성합니다. 어느 쪽을 썼는지 `part_diagnosis.vlm_input_type`에 기록합니다.

### `GET .../analysis/progress` → 200

```json
{ "part": { "done": 3, "failed": 0, "total": 9, "status": "PROCESSING" },
  "overall": { "status": "PENDING" }, "completed": false }
```

---

# F09. 종합 진단 (유사도 점수)

> **화면** — 분석 결과 페이지

| 엔드포인트 | 설명 |
|---|---|
| `GET /sessions/{session_id}/analysis` | 종합 + 부위별 진단 전체 |

### `GET .../analysis` → 200

```json
{
  "overall": {
    "similarity_score": 68,
    "score_source": "VLM",
    "score_rationale": "상체 근육량 격차가 크고 하체는 근접",
    "summary": "상체 중심 개선 필요: 어깨, 팔 근육을 강화시키는 것이 가장 우선입니다.",
    "priority_parts": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
    "strengths": ["하체 균형이 좋습니다"],
    "cautions": ["좌우 팔 근육량 차이가 있어 균형 운동을 권합니다"],
    "status": "DONE"
  },
  "parts": [
    { "class_name": "Left_Upper_Arm", "name_ko": "왼팔 상완", "part_group": "UPPER",
      "color_hex": "#F76707",
      "differences": ["상완 둘레가 얇음", "삼두 라인이 흐림"],
      "assessment": "레퍼런스 대비 상완 볼륨이 부족합니다.",
      "gap_level": "MODERATE", "priority": 2, "confidence": "HIGH",
      "vlm_input_type": "CROP", "status": "DONE",
      "reference_crop_url": "https://...?token=...",
      "user_crop_url": "https://...?token=..." },
    { "class_name": "Torso", "status": "FAILED" }
  ]
}
```

**주의**
- ⚠️ **부위 하나가 `FAILED`여도 200입니다.** 전체를 500으로 만들면 나머지 8개 결과가 버려집니다.
- `color_hex`를 함께 내려 F06 오버레이와 결과 목록의 색을 맞춥니다. 두 화면에서 같은 부위가 다른 색이면 안 됩니다.
- ⚠️ `strengths`/`cautions`는 화면 요구가 확정되지 않았습니다. 안 쓸 거면 응답·컬럼 모두 제거 가능합니다.

---

# F10. 4주 루틴 생성 / 운동 일수 조정

> **화면** — 운동 일수 입력 → 로딩 → 4주 루틴. 나중에 '운동 일수 조정' 버튼

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions/{session_id}/routines` | 생성 **및 운동 일수 조정** (새 버전) |
| `GET /sessions/{session_id}/routines/active` | 활성 루틴 (주차 토글용) |
| `GET /routines/{month_routine_id}/days/{day_number}` | Day 상세 |
| `GET /sessions/{session_id}/routines` | 버전 이력 |

### `POST .../routines`

**Request** — `{ "exercise_days_per_week": 4 }`
**Response 202** — `{ "month_routine_id": "...", "job_id": "...", "version": 1, "generation_type": "INITIAL", "status": "PENDING" }`

**선행 조건** — `VLM_OVERALL` 잡이 `DONE`

**주의**
- ⚠️ **운동 일수 조정도 이 엔드포인트입니다.** 기존 활성 버전을 `is_active=false`로 내리고 `version+1` 생성 (`generation_type='DAYS_CHANGED'`).
- ⚠️ **기존 버전 행을 삭제하지 마세요.** `workout_log`가 CASCADE로 딸려 사라집니다.
- ⚠️ **`DONE`이 된 시점에만 `is_active`를 넘기세요.** 생성 중에 넘기면 `FAILED`일 때 사용자가 볼 활성 루틴이 사라집니다.
- LLM 입력: `part_diagnosis[]` + `overall_diagnosis` + `inbody`(있으면) + `contraindications` + `exercise_days_per_week`

### `GET .../routines/active` → 200

```json
{
  "month_routine_id": "d4e5...", "version": 2, "generation_type": "FEEDBACK",
  "exercise_days_per_week": 4,
  "goal": "어깨·팔·다리 중심으로 주 4일 플랜으로 근육 발달을 극대화합니다.",
  "focus_areas": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
  "start_date": null, "status": "DONE",
  "weeks": [ { "week_number": 1, "days": [
      { "day_routine_id": "...", "day_number": 1, "is_rest": false,
        "title": "상체 - 밀기", "estimated_duration_min": 55, "completed": true },
      { "day_routine_id": "...", "day_number": 2, "is_rest": true,
        "title": null, "estimated_duration_min": null, "completed": false } ] } ]
}
```

> 주차 토글용이라 **운동 종목은 넣지 않습니다.** 28일 × 평균 6종목이면 응답이 커집니다.
> `status='PENDING'`이면 200 + `weeks: []`, 활성 루틴이 없으면 404.

### `GET /routines/{id}/days/{day_number}` → 200

```json
{
  "day_routine_id": "...", "day_number": 3, "week_number": 1,
  "is_rest": false, "title": "하체 - 스쿼트 중심", "estimated_duration_min": 60,
  "exercises": [
    { "order_index": 1, "name": "바벨 스쿼트", "equipment": "바벨",
      "target_muscle": "대퇴사두근", "sets": 4, "reps": 10,
      "weight_kg": 40.0, "rest_sec": 90, "note": "무릎이 발끝을 넘지 않도록 주의" },
    { "order_index": 2, "name": "플랭크", "equipment": null,
      "target_muscle": "복직근", "sets": 3, "reps": null,
      "weight_kg": null, "rest_sec": 60, "note": "1회 60초 유지" }
  ],
  "disclaimer": "제시된 중량은 참고값입니다. 본인에게 맞게 조절하세요."
}
```

⚠️ `disclaimer`를 **응답에 담아** 프론트가 반드시 노출하게 합니다. 프론트 구현에 맡기면 빠집니다.
⚠️ 시간 기반 종목은 `reps=null` + `note`. 이런 종목이 많아지면 `duration_sec` 컬럼 추가가 맞습니다.

---

# F11. 오늘의 루틴

> **화면** — '오늘의 루틴'

| 엔드포인트 | 설명 |
|---|---|
| `GET /sessions/{session_id}/routines/today` | 오늘 해야 할 Day 상세 |

### `GET .../routines/today` → 200

F10의 Day 상세 응답 + 아래 필드

```json
{ "day_number": 5, "day_source": "COUNT", "total_completed": 4,
  "month_routine_id": "...", "already_logged": false, "...": "(Day 상세와 동일)" }
```

**Day 계산** — `min(COUNT(workout_log WHERE session_id=?) + 1, 28)`
하루 건너뛰어도 루틴이 밀리지 않습니다.

> ⚠️ **진행 기준이 "수행 횟수"인지 "날짜 고정"인지 미확정입니다.** `day_source`를 응답에 넣어둔 이유가 이것 — 나중에 규칙을 바꿔도 프론트가 어느 방식으로 계산된 값인지 알 수 있습니다. 날짜 기준으로 바꾸면 `month_routine.start_date`가 필수가 됩니다.
> 28일을 넘기면 `{"completed": true}` → 완료 화면.

---

# F12. 운동 완료 + 피드백 반영

> **화면** — '운동 마치기' → 피드백 입력 → 루틴 자동 갱신

| 엔드포인트 | 설명 |
|---|---|
| `POST /sessions/{session_id}/workout-logs` | 완료 + 피드백 → 패치 큐잉 |
| `GET /sessions/{session_id}/workout-logs` | 수행 기록 |
| `GET /sessions/{session_id}/revisions` | "왜 루틴이 바뀌었는지" |

### `POST .../workout-logs`

**Request** — `{ "day_number": 5, "feedback_text": "운동 이후 무릎이 시큰거려요." }`
**Response 201** — `{ "workout_log_id": "...", "day_number": 5, "completed_at": "...", "patch_job_id": "4e3d..." }`

- `month_routine_id`는 **서버가 현재 활성 버전으로 채웁니다** (클라이언트가 보내지 않음)
- `feedback_text`가 없으면 `patch_job_id: null`
- 같은 Day 중복 완료 → 409 `ALREADY_LOGGED`

**주의**
- ⚠️ 패치는 **변경분만** 만듭니다. 전체 재생성 금지. 결과로 새 버전(`generation_type='FEEDBACK'`) + `routine_revision` 행이 생깁니다.
- ⚠️ **통증·부상 피드백은 안전 처리 필수.** 해당 부위 부하 운동 즉시 제외 + `contraindications` 누적 + "통증이 지속되면 운동을 중단하고 전문가 상담을 권합니다" 안내.
- ⚠️ 패치가 도는 동안 사용자는 이전 버전을 계속 봅니다. `DONE` 후에야 `is_active`가 넘어갑니다.

### `GET .../revisions` → 200

```json
{ "items": [ {
  "routine_revision_id": "...", "from_version": 1, "to_version": 2,
  "source_feedback": "운동 이후 무릎이 시큰거려요.",
  "interpretation": "무릎 부하가 큰 종목에서 통증이 발생한 것으로 판단됩니다.",
  "changes": [ { "day_number": 8, "action": "REPLACE",
                 "from": "바벨 스쿼트", "to": "레그 익스텐션(경량)",
                 "reason": "무릎 굴곡 부하 감소" } ],
  "contraindications_added": ["깊은 무릎 굽힘"], "created_at": "..." } ] }
```

`source_feedback`은 `workout_log`에서 **조인해서** 채웁니다. 중복 저장 금지.

---

# F13. 진행 상태 조회 (폴링)

| 엔드포인트 | 설명 |
|---|---|
| `GET /jobs/{job_id}` | 단일 잡 상태 |
| `GET /sessions/{session_id}/jobs` | 세션의 잡 목록 (`kind`, `status` 필터) |

### `GET /jobs/{job_id}` → 200

```json
{ "job_id": "9f8e...", "session_id": "3c9a...", "kind": "SEG_REFERENCE",
  "status": "DONE", "attempts": 1,
  "result": { "segmentation_id": "f1e2...", "detected": 12, "valid_comparable": 7,
              "invalid": [ { "class_name": "Left_Lower_Leg", "reason": "TOO_SMALL" } ] },
  "error": null,
  "started_at": "...", "finished_at": "...", "created_at": "..." }
```

**주의**
- ⚠️ **이 조회도 소유권 검증이 필요합니다** (`job → analysis_session → user_id`). 빠뜨리면 `job_id`만 알면 남의 진행 상황이 보입니다.
- ⚠️ `error`는 사용자에게 그대로 보여도 되는 문구만. 스택 트레이스·모델 경로·API 키 금지.
- `SEG_*`의 `result.invalid`를 쓰면 세그 완료 즉시 "왼쪽 종아리 노출 부족" 안내를 낼 수 있습니다.

### 잡 의존 관계

```
SEG_REFERENCE ─┐
SEG_USER      ─┼→ VLM_PART (교집합 부위 수만큼) → VLM_OVERALL ─┐
OCR_INBODY ────┘  (있으면 프롬프트에 포함, 없으면 그냥 진행)   ├→ ROUTINE_GEN
운동 일수 입력 ─────────────────────────────────────────────────┘

workout_log(feedback_text) → ROUTINE_PATCH → 새 month_routine 버전
```

---

# F14. 이미지 접근 (signed URL)

| 엔드포인트 | 설명 |
|---|---|
| `POST /storage/signed-urls` | 배치 발급 (최대 30개) |

### Request / Response

```json
{ "items": [ { "bucket": "segmentations",
               "path": "8f14e45f-.../3c9a1b7e-.../user/map.png" } ],
  "expires_in": 3600 }
```
```json
{ "items": [ { "bucket": "...", "path": "...", "url": "https://...?token=...",
               "expires_at": "2026-08-13T05:21:00Z" } ] }
```

**주의**
- ⚠️ **`path`가 `{X-User-Id}/`로 시작하는지 검증하는 것만으로 부족합니다.** 그 경로가 실제로 DB에 존재하는 행인지도 확인하세요. prefix 검증만 하면 임의 경로 탐색이 가능합니다.
- `expires_in` 최대 3600초. 초과 값은 잘라냅니다.
- ✅ CORS는 별도 설정이 필요 없습니다. Supabase Storage가 signed URL 응답에 `Access-Control-Allow-Origin: *`를 기본으로 내려줍니다 (F06 참조).

---

# F15. 데이터 삭제

| 엔드포인트 | 설명 |
|---|---|
| `DELETE /users/me` | 계정 + 전 데이터 삭제 |
| `DELETE /inbody/{inbody_id}` | 인바디 1건 삭제 |

### `DELETE /users/me` → 204

**⚠️ 삭제 순서 (Storage 먼저, DB 나중)**
```
1. photos/{user_id}/          prefix 삭제
2. segmentations/{user_id}/   prefix 삭제
3. body-parts/{user_id}/      prefix 삭제
4. inbody-temp/{user_id}/     prefix 삭제
5. DELETE FROM users WHERE user_id = ?   (나머지 전부 CASCADE)
```

- ⚠️ **FK CASCADE는 Storage 파일을 지우지 않습니다.** 경로를 `{user_id}/`로 나눈 이유가 이것입니다.
- ⚠️ **DB를 먼저 지우면 어느 경로를 지울지 알 수 없게 됩니다.**
- ⚠️ Storage 삭제 실패 시 DB는 건드리지 말고 500 + 어느 단계까지 진행됐는지 로그.
- 사람 사진을 다루므로 soft delete를 쓰지 않습니다. 삭제 요청은 실제로 지웁니다.

> ⚠️ **시연 후 데이터 삭제 정책은 미정입니다.** MVP라도 정해두세요 (미확정 #11).

---

## 부록 A. 미확정 항목이 API에 미치는 영향

| # | 미확정 | 영향 | 확정 전 임시 처리 |
|---|---|---|---|
| 1 | Sapiens2 실제 클래스명 | F06 `palette` 전체 | seed 9 + OTHER. 워커가 DB에서 읽음 |
| 2 | OCR 기술 선택 | F07 | 응답 형태 동일, `services/ocr.py` 내부만 교체 |
| 3 | 인바디 추출 컬럼 | F07 `fields` | 확정 전까지 `raw_ocr`도 함께 반환 |
| 4 | 유사도 산출 방식 | F09 `score_source` | `VLM` 고정 |
| 5 | 루틴 진행 기준 | F11 `day_source` | `COUNT` |
| 6 | 루틴 생성 분할 | F10 `status` 전환 | 일괄. 분할하면 4주차까지 끝나야 `DONE` |
| 7 | **VLM 입력 형식** | F08 `vlm_input_type`, `crop_path` | `CROP`. **맵이 있으므로 나중에 바꿔도 재추론 불필요** |
| 8 | 3방향 촬영 | F04/F05 `photo.kind` | 1장 |
| 9 | 레퍼런스 프리셋 | F18 | `USER_UPLOAD` 고정 |
| 10 | 맵 저장 해상도 | F06 전송량 | 긴 변 1024px 권장 |

## 부록 B. 프론트엔드에 반드시 전달할 것

1. `user_id`는 로컬 보관, **분실 시 복구 불가** (F01)
2. 카메라 미러링은 **CSS로만**. 서버로 보내는 이미지는 반전되지 않은 원본 (F04/F05)
3. 서버가 포즈를 **재검증**하므로 클라이언트가 90%를 넘겨도 422가 날 수 있음 → 재촬영 UI (F05)
4. 맵 오버레이: **`crossOrigin="anonymous"` 필수**, JS 리샘플 금지, `label_map` 하드코딩 금지 (F06)
5. `POST /analysis`는 **새로고침 시 중복 호출 방지 가드** 필요 (F08)
6. 중량은 추정치 → `disclaimer` 노출 **필수** (F10)
7. "본 루틴은 의학적 조언이 아닙니다" + 통증 지속 시 중단 안내 (F12)
8. 인바디 확인 화면은 `warn`/`error` 필드만 강조 (F07)
