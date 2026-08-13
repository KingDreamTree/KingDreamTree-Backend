# Frontend 통합 가이드

> **필드 정의의 진실의 원천은 [Swagger UI — `/docs`](http://localhost:8000/docs) 입니다.**
> 이 문서는 호출 흐름·이미지 규칙·에러 처리 등 Swagger에 담기 어려운 맥락을 보완합니다.
> 설계 근거까지 보려면 `docs/api-spec-v2.md`.

| | |
|---|---|
| **최종 수정일** | 2026-08-13 |
| **Base URL** | `/api/v1` |
| **구현 완료** | F02 사용자 · F03 세션 · F04 레퍼런스 · F05 사용자 사진 · F13 잡 폴링 · 부위 마스터 |
| **미구현** | 세그멘테이션 조회(F06) · 인바디(F07) · 진단(F08·F09) · 루틴(F10~F12) · signed URL 배치(F14) · 삭제(F15) |

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
앱 재진입      GET  /users/me           → 저장된 id 가 유효한지 확인
              GET  /sessions/active    → steps 를 보고 어느 화면으로 갈지 결정

분석 시작      POST /sessions
레퍼런스       POST /sessions/{id}/photos/reference   → job_id (세그 시작)
촬영 화면      GET  /sessions/{id}/photos/reference   → 기준 랜드마크
사용자 사진    POST /sessions/{id}/photos/user        → job_id (세그 시작)
진행 확인      GET  /jobs/{job_id}      → 폴링 (세그 1.5초 간격 권장)
```

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

**⚠️ 임계값을 프론트에 하드코딩하지 마세요.** `THRESHOLD`(90) / `F_MIN`(0.80)은 서버 `.env`에 있는 튜닝 대상입니다. 하드코딩하면 서버에서 값을 조정한 순간 두 곳이 어긋납니다. 422 응답의 `detail.threshold` / `detail.f_min` 으로 함께 내려가니 그 값을 쓰세요.

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

### `POST /api/v1/sessions/{session_id}/photos/user`

위 필드에 더해:

| 필드 | 필수 | 설명 |
|---|---|---|
| `capture_source` | O | `CAPTURE`(앱 내 촬영) \| `UPLOAD`(파일 선택) |
| `pose_similarity` | O | 0~100 |
| `framing_score` | O | 0~1 |
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
             "detail": { "pose_similarity": 71.2, "threshold": 90.0, "reason": "POSE" } } }
```

`message` 는 그대로 노출해도 되게 쓰여 있습니다.

| HTTP | code | 화면에서 할 일 |
|---|---|---|
| 401 | `MISSING_USER_ID` | 헤더 누락. `POST /users` 로 재발급 |
| 404 | `NOT_FOUND` | 없거나 **남의 것**. 403을 주지 않는 건 의도된 설계입니다 |
| 409 | `ACTIVE_SESSION_EXISTS` | `detail.session_id` 로 **이어서 진행** |
| 409 | `PRECONDITION_NOT_MET` | 선행 단계 미완료 (예: 레퍼런스 없이 사용자 사진) |
| 413 | `FILE_TOO_LARGE` | 10MB 초과 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | jpeg/png 외 |
| 422 | `POSE_MISMATCH` | **`detail.reason` 별로 문구를 나눌 것** (아래) |
| 422 | `MULTI_PERSON` | "혼자 나오도록 촬영해주세요" |

**`POSE_MISMATCH` 의 `detail.reason`**

| reason | 사용자가 해야 할 행동 | 예시 문구 |
|---|---|---|
| `POSE` | 자세를 바꿔야 함 | "레퍼런스와 포즈를 맞춰주세요" |
| `FRAMING` | 카메라와의 거리·위치를 바꿔야 함 | "몸이 화면에 다 나오도록 서주세요" |
| `NO_PERSON` | 사람이 안 잡힘 | "전신이 보이도록 다시 촬영해주세요" |

셋은 **서로 다른 지시**입니다. 하나로 뭉뚱그리면 사용자가 뭘 고쳐야 할지 모릅니다.

**⚠️ 판정에 실패한 사진은 저장되지 않습니다.** 재촬영 UI가 반드시 필요합니다.

---

## 이미지 규칙

- **형식**: `jpeg` / `png`. 크기 10MB 이하
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

## 아직 없는 것

아래는 서버에 구현되기 전이라 호출하면 404입니다. 순서대로 붙습니다.

- `GET /photos/{id}/segmentation` · `GET /sessions/{id}/segmentation` — 라벨 맵 + 팔레트 (부위별 색칠 오버레이)
- `POST /storage/signed-urls` — signed URL 배치 발급
- `DELETE /users/me` — 계정·데이터 삭제
- 인바디 · 진단 · 루틴 (담당 B)

**부위별 오버레이(F06)를 붙일 때 미리 알아두실 것** — 맵은 8-bit 그레이스케일 PNG이고 픽셀 값이 곧 부위 번호입니다. 캔버스로 픽셀을 읽어야 해서 `img.crossOrigin = "anonymous"` 가 필수이고, 맵을 JS로 리샘플하면 안 됩니다(보간이 없는 부위를 만들어냅니다). 자세한 절차는 `docs/api-spec-v2.md` F06.

---

## 로컬 서버 실행

```bash
uvicorn app.main:app --reload --port 8000
```

Swagger: http://localhost:8000/docs
