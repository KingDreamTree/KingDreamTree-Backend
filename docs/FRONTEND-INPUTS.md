# 프론트 → 백엔드: 보내야 하는 값 전부

> **2026-08-16 기준.** [FRONTEND-HANDOFF.md](FRONTEND-HANDOFF.md) 가 "응답을 어떻게 읽는지"라면,
> 이 문서는 반대 방향 — **프론트가 백엔드로 보내거나 알려줘야 하는 것**만 모았습니다.
> 형식의 최종 근거는 항상 Swagger → https://api.refit.live/docs

---

## 0. API 아님 — 말로 알려줘야 하는 것

| 무엇 | 왜 | 언제 |
|---|---|---|
| **프론트 배포 주소(오리진)** — `https://도메인[:포트]` | 서버 `.env` 의 `CORS_ORIGINS` 에 등록해야 합니다. ⚠️ **프로덕션은 이미 잠겨 있습니다** — 허용 오리진은 `http://localhost:5173` 과 `https://www.refit.live` 뿐 (2026-08-16 실측). Vercel 프리뷰 URL 등 다른 오리진에서는 모든 호출이 "Failed to fetch"로 죽으니, 프리뷰로 테스트하려면 그 도메인을 먼저 알려줘야 합니다 | **배포 주소 확정 즉시.** 바뀌면 그때마다 다시 |
| 로컬 개발 오리진 (예: `http://localhost:3000`) | 위와 동일 | 개발 시작할 때 |
| 데모 당일 쓸 기기·브라우저 | MediaPipe 가 프론트에서 돌기 때문에, 그 환경에서 `web/pose-live.html` 이 도는지 미리 확인해두고 싶습니다 | 시연 전 아무 때나 |

---

## 1. 모든 요청 공통

- **`X-User-Id` 헤더** — 예외는 딱 3개: `POST /users`, `GET /body-parts`, `GET /pose-criteria`
- `user_id` 는 `POST /users` (바디 없음) 1회로 발급받아 **로컬에 저장**하세요.
  로그인이 없어서 이 값을 잃으면 데이터에 다시 접근할 방법이 없습니다.

---

## 2. 사진 업로드 — 프론트가 **계산해서** 보내는 값 (가장 중요)

측정은 프론트, 판정은 서버입니다. 산식은 `web/pose-score.js` 를 **그대로** 쓰세요
(직접 구현하면 서버 임계값과 어긋납니다).

### 레퍼런스 — `POST /sessions/{id}/photos/reference` (multipart)

| 필드 | 필수 | 값 |
|---|---|---|
| `file` | ✅ | 이미지 10MB 이하 (jpeg/png/heic/webp) |
| `pose_landmarks` | ✅ | MediaPipe 33개 랜드마크 **JSON 문자열** (`[{index,x,y,z,visibility},…]`, 좌표 0~1 정규화) |
| `pose_scale_basis` | ✅ | 크기 기준 — 사용자 사진과 같은 값을 써야 합니다 |
| `pose_person_area_ratio` | | 인물 면적 비율 |
| `multi_person` | | 기본 false |
| `is_mirrored` | | 거울 촬영이면 true |

### 사용자 사진 — `POST /sessions/{id}/photos/user` (multipart)

| 필드 | 필수 | 값 | 출처 |
|---|---|---|---|
| `file` | ✅ | 이미지 10MB 이하 | |
| `capture_source` | ✅ | `CAPTURE` \| `UPLOAD` | |
| `pose_landmarks` | ✅ | 33개 랜드마크 JSON 문자열 | MediaPipe |
| `pose_similarity` | ✅ | 0~100 | `poseScore()` |
| `framing_score` | ✅ | 0~1 | `framingScore()` |
| `pose_scale_basis` | ✅ | **레퍼런스와 다르면 422** `SCALE_BASIS_MISMATCH` | |
| `facing_delta` | | 기본 0.0 — 저장만, 판정 안 함 | `facingDelta()` |
| `pose_oks` | | 0~1 — 저장만, 판정 안 함 | |
| `pose_person_area_ratio` | | | |
| `multi_person` | | 기본 false — true 면 422 | |
| `is_mirrored` | | 기본 false | |

**⚠️ 세 가지만 지켜주세요:**

1. **판정 기준은 `GET /pose-criteria` 를 받아서 쓰세요. 하드코딩·캐시 금지.**
   기준 페이로드가 8/15에 바뀌었습니다 (`r_max` 삭제, `min_seg_ratio` 신설).
   옛 값을 넣으면 `pose-score.js` 의 `requireCriteria` 가 **일부러** 에러를 던집니다
   — 조용히 틀리는 것보다 낫기 때문입니다. 연동 첫 화면이 안 뜨면 이것부터 의심하세요.
2. **랜드마크는 반전 안 된 카메라 원본 기준**입니다. 거울(전면 카메라) 모드라도
   좌표를 뒤집어 보내지 말고 `is_mirrored: true` 만 주세요 — 되돌리는 건 서버가 합니다.
3. 판정 실패(422)와 검사기 장애(503)의 사용자 안내가 다릅니다 — 처리 방법은
   [FRONTEND-HANDOFF.md §2](FRONTEND-HANDOFF.md) 참조.

---

## 3. 인바디 (선택 기능)

| 요청 | 보내는 것 |
|---|---|
| `POST /sessions/{id}/inbody` (multipart) | `files` — 결과지 이미지 **1~5장** (한 건의 여러 페이지) |
| `PATCH /inbody/{id}` | `fields`(수정한 값들) · `segments`(부위별 수정) · `verified`(사용자가 확인을 마쳤으면 true → 상태 DONE) |

- `smi` 는 보내지 마세요 — 서버 계산값(읽기 전용)이라 무시됩니다.
- 확인 화면에서 사용자가 값을 고치지 않았어도 **`verified: true` 는 보내야** 다음 단계가 깔끔합니다.

---

## 4. 루틴 생성 — `POST /sessions/{id}/routines`

```json
{ "exercise_days_per_week": 3 }
```

정수 1~7, **이거 하나뿐입니다.** 즉, **주당 운동 일수를 사용자에게 묻는 UI 가 필요합니다.**
`month_routine_id` 는 보내지 않습니다 — 서버가 활성 버전을 정합니다.

---

## 5. 운동 완료·피드백 — `POST /sessions/{id}/workout-logs`

```json
{ "day_order": 1, "cycle_no": 1, "feedback_text": "무릎이 좀 아팠어요" }
```

| 필드 | 규칙 |
|---|---|
| `day_order` (1~7) · `cycle_no` (1~4) | **직접 계산하지 말고** `GET …/routines/today` 나 `progress` 의 `next_day_order`·`cycle_no` 를 그대로 되돌려 보내세요 |
| `feedback_text` | 선택, 최대 1000자. 있으면 루틴 패치 잡이 돌고(LLM 과금), 없으면 기록만 남습니다 |

---

## 6. 코치 대화 — messages 왕복이 프론트 책임

서버는 대화를 저장하지 않습니다(stateless). **직전 응답의 `messages` 를 그대로 + 새 user
발화 하나를 append** 해서 보내세요. 가공·요약·삭제 금지, 최대 64개.

```
POST /sessions/{id}/coach-chat        { "messages": [ …직전 응답 그대로…, {"role":"user","content":"…"} ] }
POST /sessions/{id}/coach-chat/apply  { "messages": [ …전체 히스토리… ] }   ← [적용] 버튼
```

첫 턴은 user 발화 1개, 또는 빈 배열(코치가 먼저 인사)로 시작합니다.

---

## 7. 바디 없이 호출하는 POST (헷갈리기 쉬움)

`POST /users` · `POST /sessions` · `POST /sessions/{id}/archive` · `POST /sessions/{id}/analysis`
전부 **빈 바디**입니다. analysis 는 쿼리 `?force=true` 로 재실행만 선택 가능.

`POST /storage/signed-urls` 만 바디가 있습니다:

```json
{ "items": [ { "bucket": "…", "path": "…" } ], "expires_in": 3600 }
```

(`bucket`·`path` 는 이전 응답에 있던 값을 그대로. `expires_in` 은 선택, 상한 3600초.)

---

## 요약 체크리스트 — 프론트가 준비할 것

- [ ] **배포 오리진을 백엔드에 알려줬는가** (CORS 등록)
- [ ] `X-User-Id` 를 모든 요청에 싣는가 · `user_id` 를 로컬에 저장하는가
- [ ] MediaPipe + `web/pose-score.js` 를 촬영 화면에 붙였는가
- [ ] `/pose-criteria` 를 매번 받아 쓰는가 (하드코딩 금지)
- [ ] 거울 모드에서 좌표를 안 뒤집고 `is_mirrored` 만 보내는가
- [ ] 주당 운동 일수를 묻는 UI 가 있는가
- [ ] `day_order`·`cycle_no` 를 서버 값 그대로 되돌려 보내는가
- [ ] 코치 대화 `messages` 를 그대로 왕복시키는가
- [ ] 인바디 확인 화면에서 `verified: true` 를 보내는가
