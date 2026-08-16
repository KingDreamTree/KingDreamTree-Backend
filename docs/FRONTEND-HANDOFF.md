# 프론트엔드 전달 — 백엔드 전체 (A + B 통합)

> **2026-08-16 기준 · 전 기능 구현 완료 · EC2 배포 완료**
> 필드 정의의 최종 근거는 항상 **Swagger** 입니다 → https://api.refit.live/docs
> 이 문서는 "순서와 의미"를, Swagger 는 "형식"을 담당합니다.

---

## 0. 공통 규칙 — 먼저 읽어주세요

### Base URL — 프로덕션이 살아 있습니다

```
프로덕션:  https://api.refit.live/api/v1     ← 해커톤 당일 이걸 씁니다 (HTTPS, main 머지 시 자동 배포)
로컬:      http://localhost:8000/api/v1
헬스체크:  https://api.refit.live/health      (prefix 없음 — 서버 살아있나 확인용)
```

### 인증 — 로그인이 없습니다

모든 요청에 **`X-User-Id` 헤더**가 필요합니다 (`POST /users`, `GET /body-parts`,
`GET /pose-criteria` 제외).

```
POST /api/v1/users          → { user_id }   최초 1회, 로컬에 저장
이후 모든 요청 헤더:  X-User-Id: <user_id>
```

### 에러 형식 — 전부 동일합니다

```json
{ "error": { "code": "POSE_MISMATCH", "message": "사용자에게 그대로 보여줄 문구", "detail": { } } }
```

`message` 는 **그대로 노출해도 되게** 쓰여 있습니다. `detail` 은 화면 분기용입니다.
404(없는 경로)·405 같은 프레임워크 에러도 같은 형태로 통일돼 있으니 `error.code` 만 읽으면 됩니다.

### 무거운 작업은 202 + 폴링

```
POST → { job_id }   →   GET /jobs/{job_id}  →  status: PENDING | PROCESSING | DONE | FAILED
```

폴링 대상: 세그멘테이션 · 인바디 OCR · 진단 · 루틴 생성
**동기(즉답)**: 사진 업로드 판정 · 코치 대화 · 루틴 조회

---

## 1. 전체 플로우

```
① POST /users                              user_id 발급 (최초 1회)
② POST /sessions                           session_id 발급
③ POST /sessions/{id}/photos/reference     레퍼런스 사진
④ POST /sessions/{id}/photos/user          사용자 사진  → 세그 잡
⑤ POST /sessions/{id}/inbody               (선택) 인바디 결과지 → OCR 잡
   PATCH /inbody/{id}                       사용자 확인·수정
⑥ POST /sessions/{id}/analysis             진단 → 잡 폴링
   GET  /sessions/{id}/analysis             결과
⑦ POST /sessions/{id}/routines             루틴 생성 → 잡 폴링
   GET  /sessions/{id}/routines/today       오늘의 운동
⑧ POST /sessions/{id}/workout-logs         완료 + 피드백
   POST /sessions/{id}/coach-chat           코치 대화 (선택)
```

`GET /sessions/active` 로 **어디까지 왔는지** 한 번에 알 수 있습니다 — 앱 재진입 시 사용.

### ⚠️ 위 ①~⑧ 은 **최초 1회 온보딩**입니다 — 그 뒤의 홈은 루틴입니다

이 서비스에서 사용자가 반복하는 행동은 **운동하고 기록하는 것**이지 사진을 다시
찍는 게 아닙니다. 루틴이 한 번 만들어지면 그 뒤 앱을 열 때마다 **오늘의 운동이
첫 화면**이어야 합니다. 진단 결과는 "다시 보기"로 들어가는 곳이지 홈이 아닙니다.

앱 진입 시 `GET /sessions/active` 한 번으로 분기하세요:

| `steps` 상태 | 가야 할 화면 |
|---|---|
| 404 `NO_ACTIVE_SESSION` | 시작 화면 (온보딩 ①부터) |
| `reference_photo.segmented` = false | 레퍼런스 촬영 (③) |
| `user_photo.segmented` = false | 사용자 촬영 (④) |
| `analysis.overall_status` ≠ DONE | 진단 로딩/폴링 (⑥) |
| `routine.active_version` = null | 루틴 생성 (⑦) |
| **`routine.status` = DONE** | ⭐ **홈 = 오늘의 운동** (`GET /routines/today`) |

⚠️ **`routines/today` 는 항상 활성 최신 버전을 돌려줍니다.** 피드백이나 코치
대화로 루틴이 바뀌면 새 버전(FEEDBACK)이 활성이 되고, 다음 `today` 호출부터
바뀐 내용이 나옵니다. 프론트가 버전을 들고 있을 필요가 없습니다 —
**적용 후에는 `today` 를 다시 부르기만** 하면 됩니다.

⚠️ 진행도(`progress`)는 버전이 아니라 **세션 기준**입니다. 피드백으로 버전이
갈려도 "2주기 3일차"가 그대로 이어집니다. 새 버전이 생겼다고 진행이 0 으로
돌아가면 그건 버그이니 알려주세요.

---

## 2. 사진 촬영 — 여기가 가장 손이 많이 갑니다

### MediaPipe 는 프론트가 돌립니다

> **측정은 프론트, 정책은 서버.**
> 서버는 MediaPipe 를 실행하지 않습니다. 랜드마크 추출과 점수 계산은 브라우저가 하고,
> 서버는 받은 값을 `.env` 임계값과 비교해 통과/거부만 판정합니다.
> 이유: 서버가 다시 측정하면 버전 차이로 값이 어긋나 "화면엔 92%인데 저장 거부"가 납니다.

**참고 구현이 저장소에 있습니다** — 그대로 가져다 쓰세요.

| 파일 | 내용 |
|---|---|
| `web/pose-score.js` | `poseScore()` · `framingScore()` · `facingDelta()` — **산식 원본** |
| `web/e2e-test.html` | 사진 두 장 → 진단 → 루틴 → 코치 대화까지 전 구간 |
| `web/pose-live.html` | 실시간 점수 표시 (자동 촬영: 판정 통과 상태 **약 1초 유지** 시 셔터) |
| `web/score-photos.html` | 찍어둔 사진에 점수 매기기 |

### 순서 — 레퍼런스가 먼저입니다

```
GET  /pose-criteria                          판정 기준 (헤더 불필요)
POST /sessions/{id}/photos/reference          레퍼런스 (판정 없음, 기준이 됨)
GET  /sessions/{id}/photos/reference          촬영 화면용 기준값 (랜드마크 포함)
POST /sessions/{id}/photos/user               사용자 (레퍼런스 대비 판정)
```

사용자 사진 업로드 폼 필드:

```
file                     이미지 (10MB 이하, jpeg/png/heic/webp)
capture_source           촬영 경로 (CAPTURE | UPLOAD)
pose_landmarks           MediaPipe 33개 랜드마크 JSON 배열
pose_scale_basis         크기 기준
pose_similarity          0~100  (poseScore 결과)
framing_score            0~1    (framingScore 결과)
facing_delta             몸통 방향 차이 — 저장만, 판정 안 함
pose_oks                 (선택) OKS 유사도 0~1 — 저장만, 판정 안 함
pose_person_area_ratio   (선택) 인물 면적 비율
multi_person             여러 명 감지 여부
is_mirrored              거울 촬영이면 true (서버가 좌우 되돌림)
```

응답에는 `signed_url` + `signed_url_expires_at` 이 있어 방금 올린 사진을 바로 화면에 띄울 수 있습니다.

### 판정은 2단계입니다 — 에러 코드별로 사용자가 할 일이 다릅니다

**1차 관문 (즉시, 무료): 자세·프레이밍** → `422 POSE_MISMATCH`

| `detail.reason` | 사용자에게 |
|---|---|
| `FRAMING` | "비슷한 거리에서 다시 찍어주세요" |
| `POSE` | "포즈를 맞춰주세요" |
| `NO_PERSON` | "전신이 보이게 다시 찍어주세요" |
| `MULTI_PERSON` | "혼자 나오게 찍어주세요" (코드 `MULTI_PERSON`) |
| `SCALE_BASIS_MISMATCH` | 레퍼런스와 기준이 다름 — 레퍼런스 재촬영 |
| `LOOSE_CLOTHING` | "몸에 붙는 옷으로 다시 촬영해주세요" |
| `PART_MISMATCH` | "레퍼런스와 같은 부위가 나오도록 다시 촬영해주세요" |
| `PERSPECTIVE_MISMATCH` | "레퍼런스와 비슷한 거리에서 다시 촬영해주세요" |
| `CROPPED` | "팔과 다리가 화면에 나오도록 다시 촬영해주세요" |

**2차 관문 (VLM, 레퍼런스와 함께 판단): 이 사진으로 비교 진단이 되는가** → `422 UNSUITABLE_PHOTO`

| `detail.reason` | 사용자에게 |
|---|---|
| `LOOSE_CLOTHING` | 옷에 몸이 가려짐 — 몸에 붙는 옷으로 재촬영 |
| `PART_MISMATCH` | 레퍼런스에서 비교하는 부위가 사진에 없음 — 같은 부위가 나오게 재촬영 |
| `PERSPECTIVE_MISMATCH` | 촬영 각도/거리가 레퍼런스와 다름 |
| `CROPPED` | 팔·다리 잘림 — 전신이 나오게 재촬영 |

> ⚠️ **422 면 저장이 안 됩니다.** 재촬영 UI 로 돌아가야 합니다. `message` 를 그대로 보여주면 됩니다.

**⚠️ `503 SCREENING_UNAVAILABLE` 은 422 와 완전히 다릅니다 (신규, 8/16).**
사진이 아니라 **검사기가 잠깐 죽은 것**입니다. "사진이 부적합합니다"로 보여주면
멀쩡한 사진을 다시 찍게 만듭니다. **"잠시 후 같은 사진으로 다시 시도"** 로 안내하세요.

```
422 = 사진 문제 → 다시 찍는다
503 = 서버 문제 → 같은 사진으로 다시 올린다
```

### 🔴 503 은 «재촬영» 이 아닙니다 — «같은 사진으로 재시도»

업로드는 2차 검사(사진 적합성)를 **동기로** 거칩니다. 그 판정이 외부 모델
장애·타임아웃으로 **불가능하면 503** 이 나갑니다.

    422  사진에 문제가 있다        → 재촬영 UI 로
    503  사진을 판정하지 못했다     → **같은 사진 그대로 재시도**

⚠️ 503 을 422 처럼 처리해 "다시 찍어주세요" 를 띄우면, 사용자는 멀쩡한 사진을
   들고 몇 번이고 다시 찍게 됩니다. **사진 문제가 아니라 서버 사정입니다.**

권장 UI — 촬영 화면으로 돌려보내지 말고, 방금 찍은 사진을 **화면에 그대로 둔 채**
[다시 시도] 버튼을 띄우세요.

```
"일시적인 문제로 사진을 확인하지 못했어요. 잠시 후 다시 시도해주세요."
                                         [다시 시도]
```

⚠️ 자동 재시도를 넣는다면 **간격을 두고 2~3회까지만.** 즉시 반복하면 장애 중인
   외부 모델을 더 밀어붙이게 됩니다.

> 이 동작은 의도된 것입니다 (fail-closed). 판정 없이 통과시키면 헐렁한 옷처럼
> 뒤 단계가 못 잡는 사진이 그대로 들어와, 진단이 조용히 나빠집니다.

### 촬영 안내 문구 (필수)

```
· 정면으로 서고, 머리부터 발까지 나오게
· 팔을 몸에서 15~30도 벌려주세요        ← 팔 인식의 핵심
· 몸에 붙는 옷을 입어주세요             ← 2차 관문(LOOSE_CLOTHING)의 핵심
· 배경이 단순한 곳에서
```

2번이 특히 중요합니다. 팔이 몸통에 붙으면 **팔이 아예 검출되지 않아** 진단에서 빠집니다.

---

## 3. 세그멘테이션 결과 — 오버레이 그리기

```
GET /photos/{photo_id}/segmentation      맵 + 팔레트 + 부위별 통계
GET /sessions/{id}/segmentation          두 장 + 비교 가능 부위
```

⚠️ **bbox·pixel_count 는 맵 좌표계입니다.** 원본 위에 그리려면 배율을 곱하세요.

```js
sx = photo.width  / segmentation.map_width
sy = photo.height / segmentation.map_height   // ⚠️ x·y 를 따로! 종횡비가 다릅니다
```

`retake_recommended: true` 면 재촬영 유도 UI 를 띄우세요 — 비교 가능 부위가 부족합니다.
기준은 **비교 가능 부위 2개 이상**입니다 (`min_required: 2` — 8/15에 3→2로 완화).

---

## 4. 인바디 (선택) — 없어도 전체가 돕니다

```
POST   /sessions/{id}/inbody      결과지 사진 1~5장 → OCR 잡
GET    /sessions/{id}/inbody      이 세션의 인바디 목록
GET    /inbody/{id}               추출값 + 검증 결과
PATCH  /inbody/{id}               사용자 확인·수정
DELETE /inbody/{id}               삭제
```

### 확인 화면이 반드시 필요합니다

AI 추출값이라 사용자 확인을 거쳐야 합니다. 화면 설계는 `docs/handoff-to-design.md` 참고.

응답의 `validation` 이 **필드별 경고**를 담습니다:

```json
{ "weight": { "level": "WARN", "message": "체중 항등식 불일치 — 계산값 72.52 vs 추출값 63.50" } }
```

> ⚠️ 경고는 "이 칸이 틀렸다"가 아니라 **"이 값들이 서로 안 맞는다"** 입니다.
> 실제로 틀린 건 다른 칸일 수 있으니 **관련 필드를 묶어서** 보여주세요.
> 경고가 있어도 다음 단계로 넘어갈 수 있어야 합니다.

`smi` 는 서버 계산값이라 **읽기 전용**입니다. 사용자가 골격근량·신장을 고치면 즉시 재계산됩니다.

건너뛰기 버튼을 항상 노출하세요 — **인바디는 선택**입니다. OCR 이 실패해도
전체 플로우는 계속됩니다 (진단은 인바디 없이 돕니다).

---

## 5. 진단

```
POST /sessions/{id}/analysis            → job_id
GET  /sessions/{id}/analysis/progress   로딩 화면용 진행률
GET  /sessions/{id}/analysis            결과
```

`POST` 응답의 `reused: true` 는 이미 진행 중/완료된 분석이 있어 그걸 돌려줬다는 뜻입니다
(중복 호출 가드 — 에러 아님).

### 응답 읽는 법

```json
{
  "overall": {
    "similarity_score": 68,
    "score_source": "RULE",
    "score_rationale": "판단된 9개 부위의 격차 등급을 등간 사상(등급당 25점)한 평균 — …",
    "summary": "…",
    "priority_parts": ["Left_Upper_Arm", "…"],
    "strengths": [], "cautions": [],
    "status": "DONE"
  },
  "parts": [ { "class_name", "name_ko", "part_group", "color_hex", "gap_level",
               "confidence", "assessment", "differences", "priority", "blocked_reason", "status" } ],
  "excluded": [ { "class_name", "name_ko", "reason", "side" } ],
  "inbody_id": "…또는 null",
  "disclaimer": "…"
}
```

| 필드 | 화면 |
|---|---|
| `similarity_score` + `score_rationale` | 점수 옆에 근거를 **같이** 노출하면 신뢰도가 올라갑니다 |
| `score_source` | 정상 완료면 `"RULE"` — 점수는 LLM 이 아니라 규칙 합산이 만듭니다 |
| `summary` | **"경로" 문체입니다** (8/16 변경) — 격차 나열이 아니라 "지금 → 목표까지 어떤 순서로 가는지". `priority_parts` 순서 = 루틴이 실제로 볼륨을 얹는 순서와 일치합니다 |
| `parts[].assessment` | PT 트레이너 톤 (8/16 변경). 부위마다 길이·표현이 다른 게 정상입니다 |
| `excluded` | 비교에서 빠진 부위 + 사유 — "왼팔은 왜 없지?"에 여기로 답하세요 |
| `gap_level: null` + `blocked_reason` | "이 부위는 확인이 어려웠어요" — **숨기지 말고 정직하게** |
| `gap_level` 있음 + `blocked_reason` | **배지 필요** — 아래 참조 |
| `confidence: "LOW"` | 흐리게 표시하거나 배지 |
| `strengths: []` | 빈 배열이 정상입니다. 억지로 채우지 않습니다 |
| `inbody_id` | 이 진단에 어떤 인바디가 반영됐는지. null 이면 사진만으로 진단 |

> ⚠️ `gap_level` 값은 `NONE | SLIGHT | MODERATE | SIGNIFICANT` 입니다.
> `confidence` 는 `LOW | MEDIUM | HIGH`. 헷갈리기 쉬우니 주의.

### ⚠️ `blocked_reason` 이 있는데 `gap_level` 도 있는 경우 — 배지를 붙여주세요

옷에 가려 **눈으로는 못 봤지만 인바디 수치로 판단한** 부위입니다. 등급도 진단문도
정상적으로 들어 있어서, 그냥 두면 **눈으로 본 진단과 화면에서 구별되지 않습니다.**

```
■ 몸통   MODERATE   [옷에 가림 · 인바디 기준]   ← 이 배지
```

`blocked_reason` 을 "assessment 가 없을 때만" 보여주면 이 케이스가 통째로 사라집니다
(실제로 `web/e2e-test.html` 이 그렇게 돼 있어 화면에서 확인이 안 됐습니다).
`assessment` 유무와 무관하게 `blocked_reason` 이 있으면 배지를 띄우세요.

근거의 종류가 다르면 사용자가 알아야 합니다 — 사진으로 본 것과 체성분 수치로
추정한 것은 신뢰도가 다르고, "왜 이 부위만 다르게 나왔지?"의 답이기도 합니다.

### 실패 처리

| 상황 | 응답 |
|---|---|
| 비교 부위 **2개 미만** | `INSUFFICIENT_PARTS` → 재촬영 유도 (8/15에 3→2로 완화) |
| 일부 부위만 실패 | **200**. 실패한 부위만 빠짐 (전체 실패 아님) |

---

## 6. 루틴 — ⚠️ 모델을 정확히 이해해야 합니다

### 1주 단위 × 4주기 반복

```
루틴 1개  =  Day 1 … Day N     (N = 주당 운동 일수)
              ↑ 이걸 4주기 반복

주 3일 선택 → Day 3개만 저장 → 4주기 = 총 12회 수행
```

**28일치가 저장되는 게 아닙니다.** `day_order` 는 요일이 아니라 **주기 내 순서**입니다.

```json
{
  "exercise_days_per_week": 3,
  "total_cycles": 4,
  "days": [ {"day_order": 1, …}, {"day_order": 2, …}, {"day_order": 3, …} ],
  "progress": { "completed_count": 5, "total_count": 12, "cycle_no": 2, "next_day_order": 2, "percent": 41 }
}
```

화면 표기 예: **"2주차 · Day 2"** = `cycle_no` 2, `day_order` 2

### 진행은 날짜가 아니라 완료 횟수 기준

하루 밀려도 안 깨집니다. `progress.day_source: "COUNT"` 로 방식을 알려줍니다.
피드백으로 **버전이 바뀌어도 진행도는 이어집니다** (0으로 리셋되지 않음 — 8/15 수정).

### 엔드포인트

```
POST /sessions/{id}/routines            { "exercise_days_per_week": 3 }  → job_id
GET  /sessions/{id}/routines/today      오늘 해야 할 Day  ← 홈 화면은 이것만 쓰면 됩니다
GET  /sessions/{id}/routines/active     전체 (Day 1..N + 진행)
GET  /sessions/{id}/routines            버전 이력
GET  /routines/{id}/days/{day_order}    Day 상세
```

### 운동 항목

```json
{
  "name": "레그프레스", "exercise_ref": "exr_…", "image_url": "https://…",
  "exercise_kind": "STRENGTH",
  "sets": 4, "reps": 10, "rest_sec": 90, "rir": 2,
  "muscle_group": "대퇴사두", "boosted_by": "Left_Upper_Leg", "note": "…"
}
```

| 필드 | 의미 |
|---|---|
| `exercise_ref` · `image_url` | ExerciseDB 원본. **지어낸 운동이 아니라는 근거** — 이미지 노출 권장 |
| `rir` | "2회 남기고 멈추는 무게". ⚠️ **중량(kg)은 제공하지 않습니다** |
| `boosted_by` | 이 운동이 어느 진단 부위 때문에 볼륨을 더 받았는지 → "왼팔이 부족해서 세트를 늘렸어요" |
| `note` | 좌우 불균형이 있으면 "약한 쪽부터 시작" 같은 수행 순서 안내가 붙습니다 |
| `exercise_kind: "CARDIO"` | `sets` 대신 `duration_min` 을 보세요 |

`disclaimer` 를 **반드시 노출**하세요 (의학적 조언 아님).

`notice` 가 있으면 안내 배너로 — 예: 주 7일 선택 시 "6일 근력 + 1일 회복으로 구성했어요".

---

## 7. 운동 완료 · 피드백

### 방법 A — 한 방 피드백

```
POST /sessions/{id}/workout-logs
{ "day_order": 1, "cycle_no": 1, "feedback_text": "무릎이 좀 아팠어요" }
```

`feedback_text` 가 있으면 루틴 패치 잡이 큐잉되고, **새 버전이 생겨 활성 전환**됩니다.
없으면 완료 기록만 남습니다 (LLM 호출 없음 = 무료).

기록 목록은 `GET /sessions/{id}/workout-logs` 로 조회합니다.

### 방법 B — 코치 대화 (동기, 폴링 없음)

```
POST /sessions/{id}/coach-chat
{ "messages": [ { "role": "user", "content": "스쿼트 할 때 무릎이 아팠어" } ] }
```

응답:

```json
{
  "reply": "무릎이 아프셨군요. 운동을 멈출 정도였나요, 살짝 불편한 정도였나요?",
  "messages": [ … ],
  "tool_events": [ { "name": "flag_contraindication", "args": { … } } ],
  "finalized": null,
  "turn": 1, "max_turns": 8
}
```

**대화 이어가기**: 응답의 `messages` 를 **그대로** 다음 요청에 넣고 새 발화를 append.
서버는 대화를 저장하지 않습니다 (stateless). 최대 **8턴**이며 `turn / max_turns` 로 남은 턴을 보여줄 수 있습니다.

**`finalized` 가 오면 대화 끝**입니다. 요약 카드를 띄우세요:

```json
{ "summary": "무릎 부담을 줄이는 방향으로 정리했어요…",
  "changes": [ { "what": "스쿼트 → 레그프레스", "why": "무릎 불편 신고" } ] }
```

카드에 **[적용] / [그대로 둘게요]** 버튼을 놓고, [적용] 이면:

```
POST /sessions/{id}/coach-chat/apply
{ "messages": [ …전체 히스토리… ] }
```

→ 새 버전 생성 + 활성 전환. `no_change: true` 면 변경이 없었다는 뜻입니다.

> ⚠️ **금기 등록(`flag_contraindication`)은 [적용] 을 기다리지 않고 즉시 반영**됩니다.
> 안전은 버튼 뒤에 두지 않습니다. `tool_events` 로 배지를 띄워주세요.

### 왜 바뀌었는지 보여주기

```
GET /sessions/{id}/revisions     변경 이력 + 원본 피드백
```

---

## 8. 세션 관리

```
GET  /sessions/active              진행 중 세션 + 단계별 완료 여부  ← 앱 재진입 시
POST /sessions/{id}/archive        종료 (새 분석 시작하려면 필요)
GET  /users/me                     저장된 user_id 유효성 확인
DELETE /users/me                   계정 + 전 데이터 삭제
```

사용자당 진행 중 세션은 **1개**입니다. 새로 시작하려면 기존 세션을 archive 해야 합니다.

---

## 9. 전체 엔드포인트 목록 (참고용)

| 메서드·경로 | 용도 | 방식 |
|---|---|---|
| `POST /users` | user_id 발급 (헤더 불필요) | 동기 |
| `GET /users/me` · `DELETE /users/me` | 확인 / 전체 삭제 | 동기 |
| `POST /sessions` · `GET /sessions/active` · `POST /sessions/{id}/archive` | 세션 | 동기 |
| `GET /body-parts` (헤더 불필요) · `GET /pose-criteria` (헤더 불필요) | 마스터/기준값 | 동기 |
| `POST·GET /sessions/{id}/photos/reference` · `POST /sessions/{id}/photos/user` | 사진 | 동기 판정 + 세그 잡 |
| `GET /photos/{photo_id}/segmentation` · `GET /sessions/{id}/segmentation` | 세그 결과 | 동기 |
| `POST·GET /sessions/{id}/inbody` · `GET·PATCH·DELETE /inbody/{id}` | 인바디 | OCR 잡 |
| `POST·GET /sessions/{id}/analysis` · `GET …/analysis/progress` | 진단 | 잡 |
| `POST·GET /sessions/{id}/routines` · `…/routines/today` · `…/routines/active` · `GET /routines/{id}/days/{n}` | 루틴 | 생성만 잡 |
| `POST·GET /sessions/{id}/workout-logs` · `GET /sessions/{id}/revisions` | 완료·이력 | 동기 (패치는 잡) |
| `POST /sessions/{id}/coach-chat` · `…/coach-chat/apply` | 코치 대화 | 동기 |
| `GET /jobs/{job_id}` · `GET /sessions/{id}/jobs` | 잡 폴링 | 동기 |
| `POST /storage/signed-urls` | 저장 파일 서명 URL 재발급 | 동기 |

서명 URL(`signed_url`)은 **1시간** 유효합니다. 만료되면 `POST /storage/signed-urls` 로 재발급하세요.

---

## 10. 자주 틀리는 지점 — 체크리스트

- [ ] `X-User-Id` 헤더를 모든 요청에 넣었는가
- [ ] **503(SCREENING_UNAVAILABLE)을 422 처럼 "재촬영"으로 안내하지 않았는가** — 같은 사진 재시도입니다
- [ ] `day_order` 를 **요일로 착각**하지 않았는가 (주기 내 순서입니다)
- [ ] 루틴이 **28일이 아니라 N일 × 4주기**임을 화면에 반영했는가
- [ ] 세그 bbox 를 원본에 그릴 때 **sx·sy 를 따로** 곱했는가
- [ ] 중량(kg)을 표시하려 하지 않았는가 (`rir` 만 제공)
- [ ] `gap_level: null` 부위를 **숨기지 않고** 표시했는가 (+ `excluded` 도)
- [ ] `blocked_reason` 배지를 `assessment` 유무와 무관하게 띄우는가
- [ ] 인바디 **건너뛰기** 버튼이 있는가
- [ ] 코치 대화에서 `messages` 를 그대로 되돌려 보내는가
- [ ] `disclaimer` 를 노출했는가
- [ ] 422(포즈 미달·부적합 사진)에서 재촬영 UI 로 돌아가는가

---

## 11. 로컬 실행 (참고)

```bash
uvicorn app.main:app --reload --port 8000
# Swagger: http://localhost:8000/docs
```

CORS 는 열려 있습니다. **해커톤 당일은 프로덕션(`https://api.refit.live/api/v1`)을 쓰세요** —
main 에 머지되면 자동 배포되므로 프론트가 서버를 직접 띄울 일은 없습니다.

---

## 부록 — 8/14판에서 바뀐 것 (이미 반영하셨다면 이것만 보세요)

| 변경 | 내용 |
|---|---|
| **Base URL** | 프로덕션 `https://api.refit.live/api/v1` 가동 (8/15 배포, 자동 재배포) |
| **2차 검사 fail-closed** | 검사기 장애 시 `503 SCREENING_UNAVAILABLE` 신설 — 422 와 **다르게** 처리 (같은 사진 재시도) |
| **반려 사유 코드** | `UNSUITABLE_PHOTO` 의 `detail.reason` 이 `LOOSE_CLOTHING / PART_MISMATCH / PERSPECTIVE_MISMATCH / CROPPED` 4종으로 확정 |
| **최소 비교 부위** | 3개 → **2개** (`INSUFFICIENT_PARTS` 기준, `retake_recommended` 기준 동일) |
| **진단 응답 필드** | `excluded[]` (빠진 부위+사유), `inbody_id` 추가 확인 |
| **진단 문체** | 부위 진단 = PT 트레이너 톤, 종합 진단 = 격차 나열이 아니라 "경로". `priority_parts` 순서가 루틴 볼륨 순서와 일치 |
| **진행도 유지** | 피드백으로 루틴 버전이 바뀌어도 진행도가 리셋되지 않음 |
| **자동 촬영 유지 시간** | 판정 통과 0.3초 → **약 1초** 유지 시 셔터 (`web/pose-live.html`) |
