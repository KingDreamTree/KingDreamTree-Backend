# API 명세

> 🚫 **이 문서는 `docs/api-spec-v2.md`로 대체되었습니다.** 구현은 v2를 보세요.

| | |
|---|---|
| **버전** | v1 (superseded) |
| **최종 수정일** | 2026-08-13 |
| **기준 문서** | `docs/db-design-v3.md` |
| **Base URL** | `/api/v1` |
| **인증** | **없음.** 모든 요청에 `X-User-Id: <uuid>` 헤더 (단 `POST /users`, `GET /body-parts` 제외) |

---

## 0. 공통 규약

### 0.1 헤더

| 헤더 | 필수 | 설명 |
|---|---|---|
| `X-User-Id` | O | `users.user_id` (UUID v4). 없으면 401 |
| `Content-Type` | 조건부 | JSON은 `application/json`, 파일 업로드는 `multipart/form-data` |

### 0.2 소유권 검증 (⚠️ 가장 중요)

인증이 없으므로 **모든 엔드포인트**가 아래를 통과해야 합니다.

```
X-User-Id → users 존재 확인
          → 경로의 session_id / photo_id / inbody_id / month_routine_id / job_id 를
            analysis_session.user_id 까지 조인해 X-User-Id 와 일치하는지 확인
```

- FastAPI 의존성 하나(`Depends(get_owned_session)`)로 몰아넣고, 라우터마다 개별 구현하지 않습니다.
- ⚠️ **소유권 불일치는 403이 아니라 404를 반환합니다.** 403을 주면 "그 `session_id`는 존재한다"는 사실이 새어나가 열거 공격의 힌트가 됩니다.

### 0.3 에러 응답 형식

```json
{
  "error": {
    "code": "POSE_MISMATCH",
    "message": "레퍼런스와 포즈가 충분히 일치하지 않습니다.",
    "detail": { "pose_similarity": 71.2, "framing_score": 0.83 }
  }
}
```

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 본문/파라미터 형식 오류 |
| 401 | `MISSING_USER_ID` | `X-User-Id` 헤더 없음 또는 UUID 형식 아님 |
| 404 | `NOT_FOUND` | 리소스 없음 **또는 소유자 불일치** |
| 409 | `ACTIVE_SESSION_EXISTS` | 이미 진행 중인 세션이 있음 |
| 409 | `PRECONDITION_NOT_MET` | 선행 잡이 아직 `DONE`이 아님 |
| 413 | `FILE_TOO_LARGE` | 업로드 용량 초과 (기본 10MB) |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | jpeg/png 외 |
| 422 | `POSE_MISMATCH` | 포즈/프레이밍 임계값 미달 |
| 422 | `INSUFFICIENT_PARTS` | 비교 가능 부위 3개 미만 |
| 422 | `MULTI_PERSON` | 사진에 인물이 2명 이상 |
| 500 | `INTERNAL_ERROR` | 그 외 |

### 0.4 비동기 작업 규약 (폴링)

무거운 작업은 **즉시 `job_id`를 반환하고 202**를 응답합니다. 프론트는 `GET /jobs/{job_id}`를 폴링합니다.

- 권장 폴링 주기: 1.5초 (세그멘테이션/OCR), 2초 (VLM/루틴)
- `job.status`: `PENDING` → `PROCESSING` → `DONE` | `FAILED`
- ⚠️ `job.status`(실행 상태)와 도메인 테이블의 `status`(화면에 써도 되는지)는 **다릅니다.** 화면 노출 판단은 도메인 테이블 쪽을 봅니다.

### 0.5 파일 업로드 제한

| 항목 | 값 |
|---|---|
| 허용 MIME | `image/jpeg`, `image/png` |
| 최대 용량 | 10MB / 파일 |
| 최대 해상도 | 4096px (긴 변). 초과 시 서버가 리사이즈 후 저장 |
| 인바디 결과지 | 1건당 최대 5장 |

### 0.6 시각 표기

모든 타임스탬프는 **ISO 8601 UTC** (`2026-08-13T04:21:00Z`).

---

## 1. 전체 흐름과 엔드포인트 대응

```
① POST /users                                    → user_id 발급
② POST /sessions                                 → session_id 발급
③ POST /sessions/{id}/photos/reference           → 레퍼런스 저장 + landmarks 즉시 반환 + SEG_REFERENCE 큐잉
④ GET  /sessions/{id}/photos/reference           → 촬영 화면용 signed URL + landmarks
⑤ POST /sessions/{id}/photos/user                → 사용자 사진 저장(포즈 검증) + SEG_USER 큐잉
⑥ POST /sessions/{id}/inbody          (선택, n회) → OCR_INBODY 큐잉
   PATCH /inbody/{inbody_id}                     → 사용자가 추출값 확인·수정
⑦ POST /sessions/{id}/analysis                   → VLM_PART × N + VLM_OVERALL 큐잉  ← 로딩 페이지
   GET  /sessions/{id}/analysis/progress         → 완료 3/9
⑧ POST /sessions/{id}/routines                   → ROUTINE_GEN 큐잉 (운동 일수 입력)
   GET  /sessions/{id}/routines/active           → 4주 루틴 전체
⑨ GET  /sessions/{id}/routines/today             → 오늘의 루틴
⑩ POST /sessions/{id}/workout-logs               → 운동 완료 + 피드백 → ROUTINE_PATCH 큐잉
```

---

## 2. `users`

### `POST /users`

회원 식별자 발급. **`X-User-Id` 불필요.**

**Request** — 본문 없음

**Response 201**
```json
{
  "user_id": "8f14e45f-ceea-467a-9b21-0c3e7d1a55b2",
  "is_pro_user": false,
  "created_at": "2026-08-13T04:21:00Z"
}
```

> ⚠️ 프론트는 이 `user_id`를 로컬 스토리지에 보관하고 이후 모든 요청의 `X-User-Id`로 씁니다. **잃어버리면 복구 수단이 없습니다.** (로그인이 없으므로) 화면에 "기기를 바꾸면 데이터가 사라집니다" 안내가 필요합니다.

### `GET /users/me`

**Response 200**
```json
{ "user_id": "...", "is_pro_user": false, "created_at": "..." }
```
없으면 404. 프론트가 앱 진입 시 저장된 `user_id`의 유효성을 확인하는 용도입니다.

### `DELETE /users/me`

**Response 204**

- DB는 `users` 삭제 → 전 계층 CASCADE
- ⚠️ **Storage는 CASCADE되지 않습니다.** 핸들러가 `photos/{user_id}/`, `body-parts/{user_id}/`, `inbody-temp/{user_id}/` prefix를 직접 삭제해야 합니다.
- ⚠️ Storage 삭제가 실패해도 DB 삭제는 이미 커밋된 상태가 될 수 있습니다. **Storage 먼저 삭제 → DB 삭제** 순서로 하고, 실패 시 500과 함께 어느 단계까지 진행됐는지 로그를 남깁니다.

---

## 3. `analysis_session`

### `POST /sessions`

**Response 201**
```json
{
  "session_id": "3c9a1b7e-...",
  "status": "ACTIVE",
  "reference_source": "USER_UPLOAD",
  "created_at": "..."
}
```

**409 `ACTIVE_SESSION_EXISTS`** — 이미 `ACTIVE` 세션이 있을 때
```json
{ "error": { "code": "ACTIVE_SESSION_EXISTS", "message": "진행 중인 세션이 있습니다.",
             "detail": { "session_id": "3c9a1b7e-..." } } }
```
> `UNIQUE (user_id) WHERE status='ACTIVE'` 제약 때문입니다. 프론트는 409를 받으면 `detail.session_id`로 이어서 진행하면 됩니다.

### `GET /sessions/active`

진행 중인 세션 + **각 단계 완료 여부**를 한 번에 반환합니다. 새로고침/재진입 시 어느 화면으로 보낼지 판단하는 용도입니다.

**Response 200**
```json
{
  "session_id": "3c9a1b7e-...",
  "status": "ACTIVE",
  "contraindications": ["무릎 굽힘 부하"],
  "steps": {
    "reference_photo": { "done": true,  "job_status": "DONE" },
    "user_photo":      { "done": true,  "job_status": "PROCESSING" },
    "inbody":          { "count": 2, "done": 1, "failed": 0, "pending": 1 },
    "analysis":        { "part_done": 3, "part_total": 9, "overall_status": "PENDING" },
    "routine":         { "active_version": null, "status": null }
  },
  "created_at": "..."
}
```

### `GET /sessions`

**Query** — `limit` (기본 20), `cursor`

**Response 200** — `{ "items": [ {session_id, status, created_at, similarity_score?} ], "next_cursor": null }`

### `PATCH /sessions/{session_id}`

**Request** — `{ "status": "ARCHIVED" }`
**Response 200** — 세션 객체

---

## 4. 사진 (`photo`, `body_part_segment`)

### 4.0 ⚠️ 포즈 값을 누가 계산하는가 — 확정 필요

| 값 | 계산 주체 | 근거 |
|---|---|---|
| **레퍼런스 landmarks** | **백엔드** (업로드 시 동기 처리) | 실시간 비교의 기준값이므로 신뢰 가능한 단일 소스여야 합니다. 프론트가 매 진입마다 재추론하지 않도록 DB에 저장합니다. |
| **사용자 P/F 점수** | **프론트가 계산해 촬영 + 백엔드가 저장 시 재검증** | 프론트 값은 조작 가능하고 MediaPipe 버전 차로 값이 어긋납니다. 저장 전에 서버가 다시 계산합니다. |

> MediaPipe Pose(이미지 1장)는 CPU에서 수백 ms라 t3.large에서 동기 처리해도 됩니다. **Sapiens2 세그멘테이션만 백그라운드**로 돌립니다. 이렇게 나눠야 사용자가 레퍼런스 업로드 직후 곧바로 촬영 화면으로 갈 수 있습니다(세그가 끝날 때까지 기다리지 않음).

### `POST /sessions/{session_id}/photos/reference`

**Request** — `multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `file` | file | O | 레퍼런스 이미지 |
| `capture_source` | string | X | `UPLOAD` 고정 (레퍼런스는 업로드만) |

**Response 201**
```json
{
  "photo_id": "a1b2...",
  "job_id": "9f8e...",
  "width": 1080,
  "height": 1440,
  "pose_scale_basis": "TORSO",
  "pose_landmarks": [
    { "index": 0, "x": 0.51, "y": 0.18, "z": -0.32, "visibility": 0.99 }
  ],
  "signed_url": "https://.../reference.jpg?token=...",
  "signed_url_expires_at": "2026-08-13T05:21:00Z"
}
```

**동작**
1. Storage `photos/{user_id}/{session_id}/reference.jpg` 저장
2. MediaPipe Pose **동기** 추출 → `pose_landmarks`, `pose_scale_basis`, `person_area_ratio`, `multi_person`
3. `photo` 행 생성 (`kind='REFERENCE'`)
4. `job(kind='SEG_REFERENCE')` 큐잉 → `job_id` 반환

**주의**
- ⚠️ **재업로드는 교체(upsert)입니다.** `UNIQUE(session_id, kind)` 때문에 기존 행이 있으면 (1) 기존 `body_part_segment`의 Storage 파일 삭제 → (2) `photo` 행 삭제(세그 행은 CASCADE) → (3) 새로 생성. 순서를 지키지 않으면 고아 파일이 남습니다.
- ⚠️ 사람이 안 잡히면 422 `POSE_MISMATCH`(`detail.reason="NO_PERSON"`), 2명 이상이면 422 `MULTI_PERSON`.
- ⚠️ **저장되는 이미지와 landmarks는 좌우 반전되지 않은 원본 기준**입니다. 화면 미러링은 CSS로만 합니다.

### `GET /sessions/{session_id}/photos/reference`

촬영 화면 진입 시 호출. 실시간 비교의 기준값을 받아갑니다.

**Response 200** — 위 201과 동일한 형태 + `segments_ready` (SEG_REFERENCE 완료 여부)

### `POST /sessions/{session_id}/photos/user`

**Request** — `multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `file` | file | O | 촬영본 또는 업로드본 |
| `capture_source` | string | O | `CAPTURE` \| `UPLOAD` |
| `client_pose_similarity` | number | X | 프론트가 계산한 P (참고/로깅용) |
| `client_framing_score` | number | X | 프론트가 계산한 F (참고/로깅용) |

**Response 201**
```json
{
  "photo_id": "b2c3...",
  "job_id": "7d6c...",
  "pose_similarity": 92.4,
  "framing_score": 0.87,
  "pose_scale_basis": "TORSO",
  "multi_person": false
}
```

**Response 422 `POSE_MISMATCH`**
```json
{ "error": { "code": "POSE_MISMATCH",
             "message": "레퍼런스와 포즈가 충분히 일치하지 않습니다. 다시 촬영해주세요.",
             "detail": { "pose_similarity": 71.2, "framing_score": 0.83,
                         "threshold": 90.0, "f_min": 0.80,
                         "reason": "POSE" } } }
```
`detail.reason`: `POSE`(포즈 불일치) \| `FRAMING`(프레이밍 불일치) \| `NO_PERSON` — 화면 안내 문구가 달라야 하므로 나눕니다.

**동작**
1. 레퍼런스 `pose_landmarks` / `pose_scale_basis` 로드 (없으면 409 `PRECONDITION_NOT_MET`)
2. 서버에서 MediaPipe Pose 추출 → **레퍼런스와 같은 `pose_scale_basis`로** 정규화
3. `F = Jaccard(레퍼런스 인물 bbox, 사용자 인물 bbox)`, `P = 관절 각도 유사도`
4. 최종 점수 `= (F ≥ F_MIN) ? P : 0` → `THRESHOLD` 미만이면 422 (저장하지 않음)
5. 통과 시 Storage 저장 → `photo` 행 생성(`kind='USER'`) → `job(kind='SEG_USER')` 큐잉

**주의**
- ⚠️ **`THRESHOLD`, `F_MIN`은 `.env`/`config.py`로 뺍니다.** 튜닝 대상 잠정값이라 코드에 박으면 안 됩니다. (`THRESHOLD=0.90`, `F_MIN=0.80`, `TOL=40`)
- ⚠️ **레퍼런스와 사용자는 반드시 같은 `pose_scale_basis`를 써야 합니다.** 레퍼런스가 `HIP_KNEE`인데 사용자를 `TORSO`로 재면 값이 무의미해집니다. 서버는 레퍼런스 값을 그대로 강제하고, 사용자 사진에서 그 기준을 못 재면 422 `POSE_MISMATCH`(`reason="FRAMING"`)로 처리합니다.
- ⚠️ 프론트에서 이미 90%를 넘겨 촬영했는데 서버가 422를 주는 경우가 생깁니다(버전/구현 차). **`client_*` 값과 서버 값을 함께 로그에 남겨** 차이를 추적하세요. 차이가 크면 임계값이 아니라 구현이 어긋난 것입니다.
- 재업로드는 레퍼런스와 동일하게 교체(upsert) 동작입니다.

### `GET /sessions/{session_id}/photos/{photo_id}/segments`

**Response 200**
```json
{
  "photo_id": "a1b2...",
  "kind": "REFERENCE",
  "segments": [
    {
      "segment_id": "...",
      "class_name": "Left_Upper_Arm",
      "name_ko": "왼팔 상완",
      "part_group": "UPPER",
      "pixel_count": 4820,
      "area_ratio": 0.031,
      "bbox": { "x": 120, "y": 340, "w": 180, "h": 260 },
      "is_truncated": false,
      "crop_url": "https://...?token=...",
      "mask_url": "https://...?token=..."
    }
  ]
}
```

### `GET /sessions/{session_id}/segments/comparable`

비교 대상(레퍼런스 ∩ 사용자 유효 부위)을 계산해 반환합니다. `POST /analysis` 전에 프론트가 "몇 개 부위를 비교할지" 미리 보여주는 용도입니다.

**Response 200**
```json
{
  "comparable": ["Torso", "Left_Upper_Arm", "Right_Upper_Arm"],
  "reference_only": ["Left_Lower_Leg"],
  "user_only": [],
  "count": 3,
  "sufficient": true,
  "min_required": 3
}
```

---

## 5. 인바디 (`inbody`, `inbody_segment`)

### `POST /sessions/{session_id}/inbody`

**Request** — `multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `files` | file[] | O | 결과지 이미지 1~5장 (**한 건의 여러 페이지**) |
| `device_type` | string | X | 사용자가 고를 수 있으면 전달. 없으면 OCR이 추정 |

> **요청 1건 = 결과지 1건.** 결과지를 여러 장(여러 측정) 올릴 때는 이 API를 여러 번 호출합니다.

**Response 202**
```json
{ "inbody_id": "c3d4...", "job_id": "6e5f...", "status": "PENDING" }
```

**동작**
1. `inbody` 행 생성 (`status='PENDING'`)
2. Storage `inbody-temp/{user_id}/{inbody_id}_{n}.jpg` 저장
3. `job(kind='OCR_INBODY')` 큐잉. **`payload`에 임시 이미지의 `bucket`/`path` 배열을 넣습니다.**
4. ⚠️ 워커는 `DONE` 처리 **직후** 임시 이미지를 삭제합니다. `FAILED`면 재처리를 위해 남겨둡니다.

### `GET /sessions/{session_id}/inbody`

**Response 200** — `{ "items": [ {inbody_id, device_type, measured_at, status, verified_at, created_at} ] }`

### `GET /inbody/{inbody_id}`

**Response 200**
```json
{
  "inbody_id": "c3d4...",
  "status": "DONE",
  "device_type": "InBody570",
  "measured_at": "2026-08-01",
  "fields": {
    "age": 27, "gender": "MALE", "height": 175.0, "weight": 72.4,
    "bmi": 23.6, "body_fat_mass": 14.2, "body_fat_percentage": 19.6,
    "skeletal_muscle_mass": 33.1, "fat_free_mass": 58.2, "bmr_kcal": 1642
  },
  "segments": [
    { "segment": "LEFT_ARM",  "lean_mass": 3.1, "fat_mass": 0.9 },
    { "segment": "RIGHT_ARM", "lean_mass": 3.3, "fat_mass": 0.9 },
    { "segment": "TRUNK",     "lean_mass": 27.4, "fat_mass": 6.8 },
    { "segment": "LEFT_LEG",  "lean_mass": 9.2, "fat_mass": 2.3 },
    { "segment": "RIGHT_LEG", "lean_mass": 9.4, "fat_mass": 2.3 }
  ],
  "validation": {
    "weight":            { "level": "ok" },
    "bmi":               { "level": "warn",  "message": "체중/신장 계산값(23.6)과 0.4 차이" },
    "segments.LEFT_ARM": { "level": "warn",  "message": "좌우 팔 근육량 차이 6.5%" },
    "bmr_kcal":          { "level": "error", "message": "추출 실패" }
  },
  "verified_at": null
}
```

> ⚠️ 확인 화면은 `validation.level`이 `warn`/`error`인 필드만 강조합니다. 전 항목을 똑같이 보여주면 사용자가 대충 넘깁니다.

**Response 200 (`status: "FAILED"`)**
```json
{ "inbody_id": "...", "status": "FAILED",
  "validation_error": "결과지에서 수치를 읽지 못했습니다. 밝은 곳에서 다시 촬영해주세요." }
```

### `PATCH /inbody/{inbody_id}`

사용자가 확인·수정한 값을 확정합니다.

**Request**
```json
{
  "fields": { "weight": 72.0, "bmr_kcal": 1650 },
  "segments": [ { "segment": "LEFT_ARM", "lean_mass": 3.2 } ],
  "verified": true
}
```

**Response 200** — `GET /inbody/{id}`와 동일 형태

**동작**
- 전달된 필드만 갱신(부분 업데이트). `verified: true`면 `verified_at = now()`
- ⚠️ `raw_ocr`은 **덮어쓰지 않습니다.** 원본 추출값과 사용자 수정본을 구분할 수 있어야 나중에 OCR 정확도를 평가할 수 있습니다.
- ⚠️ 수정 후 `validation`을 **재계산**합니다. 사용자가 고친 값이 또 항등식을 깨면 다시 `warn`이 떠야 합니다.
- `status='FAILED'`인 건도 수동 입력으로 `DONE`으로 올릴 수 있게 허용합니다.

### `DELETE /inbody/{inbody_id}`

**Response 204** — 행 삭제 + `inbody_segment` CASCADE + 임시 이미지가 남아 있으면 삭제

---

## 6. 분석 (VLM)

### `POST /sessions/{session_id}/analysis`

로딩 페이지 진입 시 호출. 부위별 VLM + 종합 VLM을 한 번에 큐잉합니다.

**Request** — 본문 없음

**Response 202**
```json
{
  "part_jobs": [ { "job_id": "...", "class_name": "Torso" } ],
  "overall_job_id": "...",
  "part_count": 5
}
```

**선행 조건** (미충족 시 409 `PRECONDITION_NOT_MET`)
- `SEG_REFERENCE` 잡이 `DONE`
- `SEG_USER` 잡이 `DONE`
```json
{ "error": { "code": "PRECONDITION_NOT_MET", "message": "세그멘테이션이 아직 진행 중입니다.",
             "detail": { "blocking": [ { "kind": "SEG_USER", "status": "PROCESSING" } ] } } }
```

**Response 422 `INSUFFICIENT_PARTS`**
```json
{ "error": { "code": "INSUFFICIENT_PARTS",
             "message": "비교 가능한 부위가 2개뿐입니다. 팔·다리가 드러나는 복장으로 다시 촬영해주세요.",
             "detail": { "count": 2, "min_required": 3,
                         "comparable": ["Torso", "Left_Upper_Arm"] } } }
```

**주의**
- ⚠️ 인바디는 **선행 조건이 아닙니다.** 없어도 분석은 진행됩니다(선택 업로드). 다만 `VLM_PART` 프롬프트에 인바디 수치를 넣으려면 해당 세션의 `OCR_INBODY` 잡이 종결(`DONE`/`FAILED`)돼 있어야 합니다. **인바디가 아직 `PENDING`이면 대기하지 말고 인바디 없이 진행하고, 그 사실을 `job.result`에 남깁니다.** (사용자를 로딩 화면에 무한정 세워두지 않기 위함)
- ⚠️ 중복 호출 시 이미 `PENDING`/`PROCESSING`인 잡이 있으면 **새로 만들지 말고 기존 `job_id`를 그대로 반환**합니다. 새로고침 한 번에 VLM이 두 배로 호출되면 요금이 두 배입니다.

### `GET /sessions/{session_id}/analysis/progress`

**Response 200**
```json
{
  "part": { "done": 3, "failed": 0, "total": 9, "status": "PROCESSING" },
  "overall": { "status": "PENDING" },
  "completed": false
}
```
> 프론트는 `완료 3/9` 형태로 보여줍니다. 부위별 VLM은 개수가 많아 체감 대기가 깁니다.

### `GET /sessions/{session_id}/analysis`

**Response 200**
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
    {
      "class_name": "Left_Upper_Arm",
      "name_ko": "왼팔 상완",
      "part_group": "UPPER",
      "differences": ["상완 둘레가 얇음", "삼두 라인이 흐림"],
      "assessment": "레퍼런스 대비 상완 볼륨이 부족합니다.",
      "gap_level": "MODERATE",
      "priority": 2,
      "confidence": "HIGH",
      "status": "DONE",
      "reference_crop_url": "https://...?token=...",
      "user_crop_url": "https://...?token=..."
    },
    { "class_name": "Torso", "status": "FAILED" }
  ]
}
```

**주의**
- ⚠️ **부위 하나가 `FAILED`여도 200을 반환합니다.** 전체를 500으로 만들면 나머지 8개 결과가 버려집니다. 프론트는 `status != "DONE"`인 항목을 그냥 목록에서 빼면 됩니다.
- ⚠️ `strengths`/`cautions`는 화면 요구가 확정되지 않았습니다. 안 쓸 거면 응답에서 빼세요(DB 컬럼도 제거 가능).

---

## 7. 루틴

### `POST /sessions/{session_id}/routines`

운동 일수 입력 → 4주 루틴 생성. **운동 일수 조정도 같은 엔드포인트**입니다(새 버전 생성).

**Request**
```json
{ "exercise_days_per_week": 4 }
```

**Response 202**
```json
{
  "month_routine_id": "d4e5...",
  "job_id": "5f4e...",
  "version": 1,
  "generation_type": "INITIAL",
  "status": "PENDING"
}
```

**동작**
- 기존 활성 버전이 있으면 `is_active=false`로 내리고 `version+1`로 새 행 생성 (`generation_type='DAYS_CHANGED'`)
- ⚠️ **기존 버전 행을 삭제하지 않습니다.** 삭제하면 `workout_log`가 CASCADE로 딸려 사라집니다.
- ⚠️ 새 버전이 `FAILED`로 끝나면 사용자가 볼 활성 루틴이 없어집니다. **`DONE`이 된 시점에만 `is_active`를 넘기고, 그전까지는 이전 버전을 활성으로 둡니다.**
- LLM 입력: `part_diagnosis[]` + `overall_diagnosis` + `inbody`(있으면) + `analysis_session.contraindications` + `exercise_days_per_week`

**선행 조건** — `VLM_OVERALL` 잡이 `DONE` (아니면 409)

### `GET /sessions/{session_id}/routines/active`

**Response 200**
```json
{
  "month_routine_id": "d4e5...",
  "version": 2,
  "generation_type": "FEEDBACK",
  "exercise_days_per_week": 4,
  "goal": "어깨·팔·다리 중심으로 주 4일 플랜으로 근육 발달을 극대화합니다.",
  "focus_areas": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
  "start_date": null,
  "status": "DONE",
  "weeks": [
    {
      "week_number": 1,
      "days": [
        { "day_routine_id": "...", "day_number": 1, "is_rest": false,
          "title": "상체 - 밀기", "estimated_duration_min": 55, "completed": true },
        { "day_routine_id": "...", "day_number": 2, "is_rest": true,
          "title": null, "estimated_duration_min": null, "completed": false }
      ]
    }
  ]
}
```
> 주차 토글 화면용입니다. **운동 종목까지는 넣지 않습니다** — 28일 × 평균 6종목이면 응답이 커집니다. 상세는 아래 엔드포인트로.

`status`가 `PENDING`이면 200 + `{"status":"PENDING","weeks":[]}`, 활성 루틴이 없으면 404.

### `GET /routines/{month_routine_id}/days/{day_number}`

**Response 200**
```json
{
  "day_routine_id": "...",
  "day_number": 3,
  "week_number": 1,
  "is_rest": false,
  "title": "하체 - 스쿼트 중심",
  "estimated_duration_min": 60,
  "exercises": [
    { "order_index": 1, "name": "바벨 스쿼트", "equipment": "바벨",
      "target_muscle": "대퇴사두근", "sets": 4, "reps": 10,
      "weight_kg": 40.0, "rest_sec": 90,
      "note": "무릎이 발끝을 넘지 않도록 주의" },
    { "order_index": 2, "name": "플랭크", "equipment": null,
      "target_muscle": "복직근", "sets": 3, "reps": null,
      "weight_kg": null, "rest_sec": 60, "note": "1회 60초 유지" }
  ],
  "disclaimer": "제시된 중량은 참고값입니다. 본인에게 맞게 조절하세요."
}
```

> ⚠️ `weight_kg`는 LLM 추정치입니다. **`disclaimer` 문구를 응답에 포함시켜 프론트가 반드시 노출하도록** 합니다. 프론트 구현에 맡기면 빠집니다.
> ⚠️ 시간 기반 종목은 `reps=null` + `note`에 기록됩니다. 이런 종목이 많아지면 `duration_sec` 컬럼 추가가 맞습니다.

### `GET /sessions/{session_id}/routines/today`

**Response 200** — `GET /routines/{id}/days/{n}`과 같은 형태 + 아래 필드

```json
{
  "day_number": 5,
  "day_source": "COUNT",
  "total_completed": 4,
  "month_routine_id": "...",
  "already_logged": false,
  "...": "(days/{n} 응답과 동일)"
}
```

**오늘의 Day 계산** — `day_number = min(COUNT(workout_log WHERE session_id=?) + 1, 28)`

> ⚠️ **진행 기준이 "수행 횟수"인지 "날짜 고정"인지 미확정입니다.** 지금 명세는 수행 횟수 기준(`day_source: "COUNT"`)이고, 날짜 기준으로 바꾸면 `month_routine.start_date`가 필수가 되며 이 계산식이 바뀝니다. **`day_source`를 응답에 넣어둔 이유가 이것입니다** — 나중에 바꿔도 프론트가 어느 규칙으로 계산된 값인지 알 수 있습니다.
> `day_number`가 28을 넘으면(4주 완주) `{"completed": true}`를 반환하고 프론트는 완료 화면을 띄웁니다.

### `GET /sessions/{session_id}/routines`

버전 이력. **Response 200** — `{ "items": [ {month_routine_id, version, generation_type, is_active, status, created_at} ] }`

---

## 8. 운동 기록 / 피드백

### `POST /sessions/{session_id}/workout-logs`

'운동 마치기' + 피드백.

**Request**
```json
{ "day_number": 5, "feedback_text": "운동 이후 무릎이 시큰거려요." }
```

**Response 201**
```json
{
  "workout_log_id": "e5f6...",
  "day_number": 5,
  "completed_at": "...",
  "patch_job_id": "4e3d..."
}
```

**동작**
1. `month_routine_id` = **현재 활성 버전**을 서버가 채웁니다 (클라이언트가 보내지 않음)
2. `feedback_text`가 있으면 `job(kind='ROUTINE_PATCH')` 큐잉 → `patch_job_id` 반환. 없으면 `null`
3. ⚠️ `UNIQUE(session_id, day_number)` — 같은 Day를 두 번 완료하면 409

**주의**
- ⚠️ 패치는 **변경분만** 만들고 전체 재생성이 아닙니다. 결과로 새 `month_routine` 버전(`generation_type='FEEDBACK'`)과 `routine_revision` 행이 생깁니다.
- ⚠️ **통증·부상 피드백은 안전 처리가 필요합니다.** 해당 부위 부하 운동을 즉시 제외하고, 추가된 금기 동작을 `analysis_session.contraindications`에 누적합니다. 응답/화면에 "통증이 지속되면 운동을 중단하고 전문가 상담을 권합니다" 안내를 노출합니다.
- ⚠️ 패치 잡이 도는 동안 사용자는 이전 버전을 계속 봅니다. `DONE`이 된 뒤에야 `is_active`가 넘어갑니다.

### `GET /sessions/{session_id}/workout-logs`

**Response 200** — `{ "items": [ {workout_log_id, day_number, completed_at, feedback_text, month_routine_id} ], "total_completed": 4 }`

### `GET /sessions/{session_id}/revisions`

"왜 루틴이 바뀌었는지" 화면용.

**Response 200**
```json
{
  "items": [
    {
      "routine_revision_id": "...",
      "from_version": 1,
      "to_version": 2,
      "source_feedback": "운동 이후 무릎이 시큰거려요.",
      "interpretation": "무릎 부하가 큰 종목에서 통증이 발생한 것으로 판단됩니다.",
      "changes": [
        { "day_number": 8, "action": "REPLACE",
          "from": "바벨 스쿼트", "to": "레그 익스텐션(경량)",
          "reason": "무릎 굴곡 부하 감소" }
      ],
      "contraindications_added": ["깊은 무릎 굽힘"],
      "created_at": "..."
    }
  ]
}
```
> `source_feedback`은 `workout_log.feedback_text`를 **조인해서** 채웁니다. `routine_revision`에 중복 저장하지 않습니다.

---

## 9. 잡 (폴링)

### `GET /jobs/{job_id}`

**Response 200**
```json
{
  "job_id": "9f8e...",
  "session_id": "3c9a...",
  "kind": "SEG_REFERENCE",
  "status": "DONE",
  "attempts": 1,
  "result": { "segment_count": 7, "skipped": ["Left_Lower_Leg"] },
  "error": null,
  "started_at": "...", "finished_at": "...", "created_at": "..."
}
```

**`status='FAILED'`**
```json
{ "status": "FAILED", "attempts": 3,
  "error": "SEGMENTATION_FAILED: 인물을 찾지 못했습니다." }
```

> ⚠️ **이 조회도 소유권 검증이 필요합니다** (`job → analysis_session → user_id`). 빠뜨리면 `job_id`만 알면 남의 진행 상황이 보입니다.
> ⚠️ `error`는 사용자에게 그대로 노출해도 되는 문구만 담습니다. 스택 트레이스·모델 경로·API 키가 섞이면 안 됩니다. 내부 상세는 서버 로그로.

### `GET /sessions/{session_id}/jobs`

**Query** — `kind`, `status` (선택)
**Response 200** — `{ "items": [ {job_id, kind, status, attempts, created_at} ] }`

---

## 10. 마스터 / 스토리지

### `GET /body-parts`

`body_part` 마스터. **`X-User-Id` 불필요** (유저 데이터 아님). 프론트 한글 라벨용, 워커 기동 시 `SKIN_CLASSES` 로드용.

**Response 200**
```json
{ "items": [
  { "class_name": "Torso", "name_ko": "몸통", "part_group": "CORE", "inbody_segment": "TRUNK" }
] }
```

### `POST /storage/signed-urls`

여러 이미지의 signed URL을 한 번에 발급합니다. (부위별 결과 화면은 이미지가 9~18장이라 개별 발급하면 왕복이 많습니다.)

**Request**
```json
{ "items": [ { "bucket": "body-parts",
               "path": "8f14e45f-.../3c9a1b7e-.../user/Torso.png" } ],
  "expires_in": 3600 }
```

**Response 200**
```json
{ "items": [ { "bucket": "...", "path": "...",
               "url": "https://...?token=...",
               "expires_at": "2026-08-13T05:21:00Z" } ] }
```

**주의**
- ⚠️ **`path`가 `{X-User-Id}/`로 시작하는지 검증하는 것만으로는 부족합니다.** 그 경로가 실제로 DB에 존재하는 행인지도 확인하세요. prefix 검증만 하면 임의 경로 탐색이 가능합니다.
- ⚠️ `expires_in` 최대 3600초. 클라이언트가 더 큰 값을 보내면 잘라냅니다.
- ⚠️ 한 요청당 최대 30개.

---

## 11. 잡 의존 관계 (워커 스케줄링)

```
SEG_REFERENCE ─┐
SEG_USER      ─┼→ VLM_PART (교집합 부위 수만큼) → VLM_OVERALL ─┐
OCR_INBODY ────┘  (인바디는 있으면 프롬프트에 포함)            ├→ ROUTINE_GEN
운동 일수 입력 ────────────────────────────────────────────────┘

workout_log(feedback_text) → ROUTINE_PATCH → 새 month_routine 버전
```

**워커 주의**
- ⚠️ **t3.large는 GPU가 없습니다.** Sapiens2 CPU 추론은 수십 초 걸립니다. **세그멘테이션 워커 동시성은 1**로 두세요. 2개를 동시에 돌리면 메모리(8GB)가 터집니다.
- ⚠️ `VLM_PART`는 I/O 대기라 병렬로 돌려도 됩니다(권장 동시성 3~4). 다만 LLM API rate limit을 확인하세요.
- ⚠️ `attempts >= 3`이면 `FAILED`. 재시도는 지수 백오프.
- ⚠️ 잡을 집을 때 `UPDATE ... WHERE status='PENDING' RETURNING`으로 **원자적으로** 선점하세요. `SELECT` 후 `UPDATE`하면 워커 2개가 같은 잡을 집습니다.

---

## 12. 미확정 항목이 API에 미치는 영향

| # | 미확정 | 영향받는 엔드포인트 | 확정 전 임시 처리 |
|---|---|---|---|
| 1 | Sapiens2 실제 클래스명 | `GET /body-parts`, 모든 `class_name` | seed는 9개로 두되 워커가 DB에서 읽음 |
| 2 | OCR 기술 선택 (Document AI / Vision / OpenAI) | `POST /sessions/{id}/inbody` | 응답 형태는 동일. `services/ocr.py` 내부만 교체 |
| 3 | 인바디 추출 컬럼 확정 | `GET /inbody/{id}`의 `fields` | 확정 전까지 `raw_ocr` 전체를 함께 반환 |
| 4 | 유사도 점수 산출 방식 | `overall.score_source` | `VLM` 고정 |
| 5 | 루틴 진행 기준 (날짜/횟수) | `GET /routines/today` | `day_source: "COUNT"` |
| 6 | 루틴 생성 분할 (28일 일괄 / 7일×4) | `POST /routines`, `status` 전환 | 일괄. 분할하면 4주차까지 끝나야 `DONE` |
| 7 | VLM 입력 형식 (크롭 / 원본+하이라이트) | 응답 변화 없음 | 크롭 |
| 8 | 3방향 촬영 | `photo.kind` 값 집합 | 1장 (`REFERENCE`/`USER`) |
| 9 | 레퍼런스 프리셋 | `POST /sessions` | `USER_UPLOAD` 고정 |

---

## 13. 프론트엔드에 반드시 전달할 것

1. `user_id`는 로컬에 보관하며 **분실 시 복구 불가**. 화면 안내 필요
2. 카메라 미러링은 **CSS로만**. 서버로 보내는 이미지는 반전되지 않은 원본
3. 서버가 포즈를 **재검증**하므로 클라이언트가 90%를 넘겨 촬영해도 422가 날 수 있음 → 재촬영 UI 필요
4. `POST /analysis`는 **새로고침해도 중복 호출되지 않게** — 기존 `job_id`를 재사용하지만 프론트도 가드 필요
5. 중량은 추정치 → `disclaimer` 문구 노출 **필수**
6. "본 루틴은 의학적 조언이 아닙니다" 고지 + 통증 지속 시 중단 안내
7. 인바디 확인 화면은 `validation.level`이 `warn`/`error`인 필드만 강조
