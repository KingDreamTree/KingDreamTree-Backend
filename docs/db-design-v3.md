# DB 설계

> 🚫 **이 문서는 `docs/db-design-v4.md`로 대체되었습니다.** 구현은 v4를 보세요. 이 문서는 각 결정의 배경 설명을 위해 남겨둡니다.

| | |
|---|---|
| **버전** | v3 (superseded) |
| **최종 수정일** | 2026-08-13 |
| **대상** | Supabase (PostgreSQL) — DB + Storage만 사용, **Auth 미사용** |
| **테이블 수** | 15개 |
| **v2 대비 변경** | 전 테이블 PK를 UUID로 / Auth 연동 컬럼 제거 / Storage `bucket`+`path` 분리 / 삭제 정책 확정 / RLS 방침 추가 |

---

## 0. 전제 — 이 설계가 깔고 가는 것

| 항목 | 내용 |
|---|---|
| 백엔드 | Python + FastAPI |
| DB / Storage | Supabase (PostgreSQL). **Auth·RLS 정책 기반 인증 미사용** |
| 로그인 | **없음.** 요청마다 `X-User-Id` 헤더로 `user_id`를 받아 식별 |
| DB 접근 주체 | FastAPI 서버만. `service_role` 키 사용 (프론트는 Supabase에 직접 붙지 않음) |
| 버킷 공개 범위 | **private.** 조회 시 백엔드가 signed URL 발급 |
| 스키마 소유 | Supabase 콘솔에서 관리 + `schema.sql`을 레포에 커밋 |

**⚠️ 이 구조의 보안 전제 — 문서에 남겨둘 것**

- 인증이 없으므로 `user_id`를 아는 사람은 그 유저의 데이터에 접근할 수 있습니다. 로그인이 없는 이상 **구조적으로 못 막습니다.**
- `user_id`는 만료도 무효화도 없어서 한 번 새면 영구적으로 접근 가능합니다.
- 따라서 방어선은 **"추측 불가능하게 만들기"** 하나뿐이고, 그게 UUID v4 PK입니다.
- **실서비스 전환 시 로그인 필수.** (가장 잊어버리기 쉬운 항목)

---

## 1. 공통 규칙

| 항목 | 규칙 | 비고 |
|---|---|---|
| PK | `UUID`, DEFAULT `gen_random_uuid()` | ⚠️ v2의 `BIGSERIAL`에서 전면 변경 |
| FK | `UUID` | |
| Storage | `*_bucket VARCHAR(63)` + `*_path VARCHAR(500)` 2컬럼 | **전체 URL 저장 금지** |
| 진행 상태 | `VARCHAR(20)` + CHECK, **대문자** | ⚠️ `job`도 대문자로 통일 |
| 시각 | `TIMESTAMPTZ` NOT NULL DEFAULT `now()` | |
| 반정형 데이터 | `JSONB` | |
| 부위 식별자 | `class_name` (문자열) | `class_id`(정수) 아님 |
| 삭제 | **hard delete + ON DELETE CASCADE** | soft delete 미사용 |

### ⚠️ PK를 전부 UUID로 바꾼 이유

`user_id`가 순차 정수(1, 2, 3…)면 남의 `user_id`를 그냥 찍어서 맞출 수 있습니다. 인증이 없는 이 구조에서 **제일 중요한 한 가지**입니다.

자식 테이블(`session_id`, `inbody_id` 등)까지 UUID로 통일한 이유는 별개입니다. 이 값들은 API 경로(`GET /sessions/{session_id}`)에 노출되는데, 소유권 검증 코드를 한 군데라도 빠뜨리면 순차 ID는 그 즉시 전수 조회가 됩니다. UUID면 코드 실수 하나가 곧바로 사고로 이어지지 않습니다.

> 자식 테이블만 `BIGSERIAL`로 되돌려도 **모든** 엔드포인트에서 소유권 검증을 하면 안전합니다. 다만 인증이 없는 상태에서 그 "모든"을 보장하기 어려워 UUID를 권장합니다.

### ⚠️ Storage를 `bucket` + `path` 2컬럼으로 나눈 이유

전체 URL을 통째로 저장하면 버킷을 옮길 때 전부 깨집니다. URL은 **조회 시점에 백엔드가 조립**하고 signed URL로 발급합니다.

버킷 이름은 테이블마다 고정이라 값이 항상 같습니다. 그럼에도 컬럼으로 둔 이유는 **버킷을 옮길 때 기존 행이 옛 버킷을 계속 가리킬 수 있어야** 하기 때문입니다. 버킷을 절대 안 옮길 거면 애플리케이션 상수로 빼고 `path`만 남겨도 됩니다.

### ⚠️ 삭제 정책 — hard delete + CASCADE

사람 사진을 다루므로 삭제 요청이 오면 **실제로 지워야** 합니다. soft delete는 행만 숨기고 Storage 파일은 그대로 남아서, "지웠다고 했는데 사진은 남아 있는" 상태가 됩니다.

- `users` 삭제 → 아래 전 계층 CASCADE
- **⚠️ Storage 파일은 FK CASCADE로 지워지지 않습니다.** 애플리케이션에서 `{user_id}/` prefix를 통째로 삭제해야 합니다. 경로 네임스페이스를 `{user_id}/`로 나눈 이유가 이것입니다.
- **⚠️ `month_routine` 행은 개별 삭제하지 않습니다.** `is_active = false`로 비활성화만 합니다. (`workout_log`가 CASCADE로 딸려 삭제되므로)
- 시연 후 데이터 삭제 정책은 **미정** — MVP라도 정해두는 게 좋습니다.

### RLS 방침

`service_role` 키는 RLS를 **전부 우회**하므로 RLS가 실질적 방어가 되지는 않습니다. 그래도 **전 테이블 RLS를 켜고 정책은 만들지 않는** 것을 권장합니다. 정책이 없으면 `anon` 키로는 아무것도 읽히지 않아, 키가 실수로 새어도 한 겹 막힙니다.

---

## 2. `users`

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `user_id` | UUID | PK, DEFAULT gen_random_uuid() | 회원 식별자. `X-User-Id` 헤더로 전달됨 |
| `is_pro_user` | BOOLEAN | NOT NULL, DEFAULT false | WIM 회원이면 true |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | `POST /users` 호출 시각 |

**설계 메모**

- ⚠️ **`auth_user_id` 제거** (v2에 있었음). Supabase Auth를 쓰지 않기로 했으므로 연결할 대상이 없습니다.
- ⚠️ **비밀번호·이메일·세션 관련 컬럼 없음.** `password_hash`, `email`, `last_login`, `refresh_token` 등은 로그인이 없으므로 넣지 않습니다.
- ⚠️ **기존 설계에서 제거한 컬럼 4종**
  - `front/side/back_picture_path` → 새 요구사항은 사용자 사진 1장. `photo` 테이블로 대체
  - `reference_picture_path` → `photo` 테이블로 이동 (세션마다 달라짐)
  - `exercise_days_per_week` → `month_routine`으로 이동 (조정할 때마다 새 버전이 생김)
- **⚠️ 이 테이블에 뭘 더 넣을지는 미정입니다.** 로그인이 없는 상태에서 "유저"를 무엇으로 정의할지가 안 정해졌습니다. 지금은 `POST /users`로 UUID 하나 발급받는 게 전부입니다. 닉네임·나이·성별 같은 걸 받을지는 화면 요구가 나와야 결정됩니다.
  - 참고: 나이·성별은 인바디 결과지에서 OCR로 추출되어 `inbody`에 이미 들어갑니다. 인바디를 안 올린 사용자에게도 필요하면 그때 `users`에 추가하면 됩니다.

**나중에 로그인을 붙일 때**

`user_id`가 이미 UUID이므로 마이그레이션이 간단합니다. Supabase Auth를 도입하면 `auth.users.id`도 UUID라, `users.auth_user_id UUID UNIQUE` 컬럼을 그때 추가해 연결하면 됩니다. **지금 미리 넣어둘 필요는 없습니다.**

---

## 3. `analysis_session`

레퍼런스 업로드 ~ 루틴 생성까지의 1회 사이클.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `session_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `user_id` | UUID | FK → users, NOT NULL, **ON DELETE CASCADE** | |
| `reference_source` | VARCHAR(20) | NOT NULL, DEFAULT 'USER_UPLOAD', CHECK ('USER_UPLOAD','PRESET') | 레퍼런스 출처 |
| `contraindications` | JSONB | NOT NULL, DEFAULT '[]' | 금기 동작 누적 목록 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'ACTIVE', CHECK ('ACTIVE','ARCHIVED') | 진행 중 세션 구분 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**제약 / 인덱스**

- UNIQUE INDEX `(user_id) WHERE status = 'ACTIVE'` — 사용자당 진행 중 세션 1개
- INDEX `(user_id, created_at DESC)` — 세션 목록 조회

**설계 메모**

- **⚠️ 소유권 검증의 기준점입니다.** 인증이 없으므로 자식 테이블 조회는 전부 `analysis_session.user_id = X-User-Id` 확인을 거쳐야 합니다. 이 조인을 빠뜨리면 `session_id`만 알면 남의 데이터가 열립니다. FastAPI 의존성(`Depends`)으로 한 군데에 몰아넣는 걸 권장합니다.
- `reference_source` — `PRESET`이면 세그멘테이션 결과를 재사용해 GPU 부하가 절반이 됩니다. 워커가 이 값을 보고 잡을 건너뜁니다. (레퍼런스 출처 정책은 아직 **미정**)
- `contraindications` — 금기 동작은 **세션 단위로 누적**하고 이후 모든 루틴 생성·수정에 제약으로 전달합니다. 루틴 버전마다 복사하면 누적본이 흩어집니다. 증분은 `routine_revision.contraindications_added`에 남습니다.
- `status`를 2값으로만 둔 이유 — 진행률은 `job` 테이블이 소스입니다. 여기서 중복 관리하지 않습니다.

---

## 4. `photo`

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `photo_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `session_id` | UUID | FK → analysis_session, NOT NULL, **CASCADE** | |
| `kind` | VARCHAR(20) | NOT NULL, CHECK ('REFERENCE','USER') | 사진 종류 |
| `storage_bucket` | VARCHAR(63) | NOT NULL, DEFAULT 'photos' | 버킷 이름 |
| `storage_path` | VARCHAR(500) | NOT NULL | `{user_id}/{session_id}/reference.jpg` 형태 |
| `width` | INT | NULL 허용 | 원본 가로 픽셀 |
| `height` | INT | NULL 허용 | 원본 세로 픽셀 |
| `capture_source` | VARCHAR(20) | NULL 허용, CHECK ('CAPTURE','UPLOAD') | 실시간 촬영 / 직접 업로드 |
| `pose_landmarks` | JSONB | NULL 허용 | MediaPipe Pose 33개 랜드마크 (정규화 좌표 + visibility) |
| `pose_scale_basis` | VARCHAR(20) | NULL 허용, CHECK ('TORSO','HIP_KNEE') | 스케일 정규화 기준 |
| `pose_similarity` | NUMERIC(5,2) | NULL 허용, CHECK (0~100) | 촬영 시점 포즈 유사도 P (%) |
| `framing_score` | NUMERIC(4,3) | NULL 허용, CHECK (0~1) | 프레이밍 일치도 F (Jaccard) |
| `person_area_ratio` | REAL | NULL 허용, CHECK (0~1) | 인물 픽셀 / 전체 픽셀 |
| `multi_person` | BOOLEAN | NOT NULL, DEFAULT false | 다중 인물 감지 경고 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**제약**

- UNIQUE `(session_id, kind)` — 세션당 레퍼런스 1장 / 사용자 1장

**설계 메모**

- `pose_scale_basis` — 하체만 나온 프레임에서는 어깨가 안 보여 몸통 길이를 못 구하므로 골반~무릎 거리를 대체 기준으로 씁니다. **레퍼런스와 사용자가 같은 기준을 써야** 하므로 어느 기준을 썼는지 값으로 남깁니다.
- `pose_similarity`(P)와 `framing_score`(F)를 나눠 저장 — 최종 점수가 `(F ≥ F_MIN) ? P : 0` 구조라, 합치면 "포즈가 틀린 건지 프레이밍에서 걸린 건지"를 사후에 못 나눕니다. 두 임계값 모두 튜닝 대상이라 이 분리가 필요합니다.
- `pose_landmarks` (레퍼런스) — 촬영 화면에서 프론트가 실시간 비교에 사용. 매 진입마다 재추론하지 않기 위함 + 유사도 로직 튜닝 시 재현용.
- `width` / `height` — `body_part_segment`의 bbox가 픽셀 좌표라, 원본 크기가 없으면 상대 위치를 복원할 수 없습니다.
- ⚠️ `visibility ≥ 0.5` 랜드마크 집합은 별도 컬럼으로 두지 않았습니다. `pose_landmarks`에 visibility가 들어 있어 파생 가능합니다.
- ⚠️ **좌우 반전 규칙** — 여기 저장되는 사진과 랜드마크는 **반전되지 않은 카메라 원본** 기준이어야 합니다. 화면 표시만 CSS로 반전합니다. 어기면 왼팔↔오른팔이 뒤바뀐 채 **에러 없이 조용히** 진행됩니다. DB로 막을 수 없으니 구현 직후 육안 검증 필요.

---

## 5. `body_part` (마스터 · seed 데이터)

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `class_name` | VARCHAR(40) | PK | Sapiens2 클래스명 |
| `name_ko` | VARCHAR(40) | NOT NULL | 화면 표시용 한글 라벨 |
| `part_group` | VARCHAR(20) | NOT NULL, CHECK ('UPPER','CORE','LOWER') | 상체/코어/하체 |
| `inbody_segment` | VARCHAR(20) | NULL 허용, CHECK ('LEFT_ARM','RIGHT_ARM','TRUNK','LEFT_LEG','RIGHT_LEG') | 인바디 부위별 분석과의 매핑 |

**seed 데이터 (비교 대상 = 맨살 9개 클래스)**

| class_name | name_ko | part_group | inbody_segment |
|---|---|---|---|
| `Torso` | 몸통 | CORE | TRUNK |
| `Left_Upper_Arm` | 왼팔 상완 | UPPER | LEFT_ARM |
| `Left_Lower_Arm` | 왼팔 전완 | UPPER | LEFT_ARM |
| `Right_Upper_Arm` | 오른팔 상완 | UPPER | RIGHT_ARM |
| `Right_Lower_Arm` | 오른팔 전완 | UPPER | RIGHT_ARM |
| `Left_Upper_Leg` | 왼쪽 허벅지 | LOWER | LEFT_LEG |
| `Left_Lower_Leg` | 왼쪽 종아리 | LOWER | LEFT_LEG |
| `Right_Upper_Leg` | 오른쪽 허벅지 | LOWER | RIGHT_LEG |
| `Right_Lower_Leg` | 오른쪽 종아리 | LOWER | RIGHT_LEG |

**설계 메모**

- ⚠️ **이 테이블만 UUID PK가 아닙니다.** 마스터 코드 테이블이고 값 자체가 식별자이므로 `class_name`이 PK입니다. 유저 데이터가 아니라 추측 위험도 없습니다.
- `inbody_segment` — 인바디 부위별 근육분석 5부위와 Sapiens2 부위의 매핑입니다. 이게 있어야 부위별 VLM 프롬프트에 "시각 정보 + 실측 수치"를 함께 넣을 수 있습니다. 팔 4클래스 → 인바디 팔 2부위로 N:1이라 마스터에 두는 게 맞습니다.
- ⚠️ **제외 클래스는 넣지 않았습니다** — `Apparel`, `Upper_Clothing`, `Lower_Clothing`, `Shoe`, `Sock`, `Eyeglasses`, `Hair`, `Face_Neck`, `Lip`, `Teeth`, `Tongue`, `Hand`, `Foot`. 이 테이블은 "비교 대상 부위 목록"이지 "Sapiens2 전체 클래스 목록"이 아닙니다. 워커의 `SKIN_CLASSES` 상수와 값이 같아야 하므로, 워커가 기동 시 이 테이블을 읽어 쓰는 편이 안전합니다.
- ⚠️ **`class_name` 값은 확정이 아닙니다.** Sapiens2의 정확한 클래스 이름·개수를 확인하지 못했고, v1(28개)에서 재배열됐을 가능성이 높습니다. 실제 추론 결과 라벨을 찍어본 뒤 seed를 확정해야 합니다.

---

## 6. `body_part_segment`

Sapiens2 부위별 크롭. **노출된(맨살) 유효 부위만** 행을 만듭니다.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `segment_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `photo_id` | UUID | FK → photo, NOT NULL, **CASCADE** | 어느 사진에서 나온 크롭인지 |
| `class_name` | VARCHAR(40) | FK → body_part, NOT NULL | 부위 |
| `class_id` | INT | NULL 허용 | 추론 당시 모델의 클래스 ID (참고용) |
| `storage_bucket` | VARCHAR(63) | NOT NULL, DEFAULT 'body-parts' | |
| `crop_path` | VARCHAR(500) | NOT NULL | RGBA 크롭 PNG 경로 |
| `mask_path` | VARCHAR(500) | NULL 허용 | 마스크 PNG 경로 |
| `pixel_count` | INT | NOT NULL | 해당 부위 마스크 픽셀 수 |
| `area_ratio` | REAL | NOT NULL | pixel_count / person_area |
| `bbox_x` | INT | NOT NULL | 원본 내 크롭 좌표 |
| `bbox_y` | INT | NOT NULL | |
| `bbox_w` | INT | NOT NULL | |
| `bbox_h` | INT | NOT NULL | |
| `is_truncated` | BOOLEAN | NOT NULL, DEFAULT false | bbox가 이미지 경계에 접함 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**제약 / 인덱스**

- UNIQUE `(photo_id, class_name)`

**설계 메모**

- ⚠️ **`session_id` / `source` 컬럼을 두지 않았습니다** — `photo_id` 하나로 세션과 REFERENCE/USER 구분이 모두 결정됩니다. 세션 단위 조회는 `photo`를 경유합니다.
- `class_id`를 참고용으로만 둔 이유 — 모델 버전이 바뀌면 ID가 재배열되므로 이 값으로 조인하면 안 됩니다. "어느 버전으로 뽑은 결과인지" 역추적용입니다.
- `pixel_count` / `area_ratio` — 유효 부위 판정 기준(`MIN_PIXELS` 1,500px, `MIN_RATIO` 0.5%). ⚠️ 둘 다 튜닝 대상 잠정값이라, 임계값을 나중에 올렸을 때 기존 데이터를 재판정할 수 있도록 원값을 저장합니다.
- **비교 대상 = 레퍼런스 유효 부위 ∩ 사용자 유효 부위** (최대 9개). 이 교집합이 VLM 호출 횟수를 결정하며 3개 미만이면 복장 안내 + 재촬영입니다. 쿼리로 계산하므로 별도 컬럼은 없습니다.
- ⚠️ 옷에 가려진 부위는 애초에 행을 만들지 않으므로 `is_exposed` 같은 컬럼은 없습니다.

---

## 7. `inbody`

인바디 / WIM 3D 바디스캔 결과지. **원본 이미지는 저장하지 않습니다.**

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `inbody_id` | UUID | PK, DEFAULT gen_random_uuid() | 인바디 측정 건 |
| `session_id` | UUID | FK → analysis_session, NOT NULL, **CASCADE** | |
| `device_type` | VARCHAR(30) | NULL 허용 | 'InBody570', 'WIM3D', 'unknown' 등 |
| `measured_at` | DATE | NULL 허용 | 결과지에 인쇄된 측정일 |
| `age` | INT | NULL 허용, CHECK (1~120) | 나이 |
| `gender` | VARCHAR(10) | NULL 허용, CHECK ('MALE','FEMALE') | 성별 |
| `height` | NUMERIC(5,1) | NULL 허용, CHECK (120~220) | 키(cm) |
| `weight` | NUMERIC(5,1) | NULL 허용, CHECK (25~250) | 몸무게(kg) |
| `bmi` | NUMERIC(4,1) | NULL 허용, CHECK (10~60) | BMI |
| `body_fat_mass` | NUMERIC(5,1) | NULL 허용, CHECK (0~150) | 체지방량(kg) |
| `body_fat_percentage` | NUMERIC(4,1) | NULL 허용, CHECK (1~70) | 체지방률(%) |
| `skeletal_muscle_mass` | NUMERIC(5,1) | NULL 허용, CHECK (10~60) | 골격근량(kg, SMM) |
| `fat_free_mass` | NUMERIC(5,1) | NULL 허용, CHECK (10~150) | 제지방량(kg, FFM) |
| `bmr_kcal` | INT | NULL 허용, CHECK (500~5000) | 기초대사량 |
| `raw_ocr` | JSONB | NULL 허용 | VLM 추출 결과 전체 |
| `validation` | JSONB | NULL 허용 | 필드별 `ok`/`warn`/`error` 등급 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') | 추출 진행 상태 |
| `validation_error` | TEXT | NULL 허용 | FAILED 사유 |
| `verified_at` | TIMESTAMPTZ | NULL 허용 | 사용자가 확인·수정을 완료한 시각 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | 업로드 시각 |

**인덱스**

- INDEX `(session_id, measured_at DESC NULLS LAST)`

**`status` 값의 의미**

- `PENDING` — 사진은 올라갔고 추출은 아직. 행 생성 시 기본값
- `DONE` — 추출 + 검증 통과. 이 상태여야 분석·루틴 생성에 쓸 수 있음
- `FAILED` — 추출 실패 또는 재시도까지 검증 실패. 재업로드 필요

**설계 메모**

- ⚠️ **`user_id UNIQUE` → `session_id` (UNIQUE 제거)** — 결과지를 여러 장 올릴 수 있으므로 1인 1행이 성립하지 않습니다. `user_id`는 세션에서 조인합니다.
- ⚠️ **`image_paths` 제거** — "인바디 결과지 사진 자체는 DB에 저장 안 함". 다만 OCR 실패 시 재처리를 위해 잡이 `DONE`이 될 때까지는 파일을 유지해야 하므로, **임시 경로는 `job.payload`에 넣습니다.** 컬럼을 따로 만들면 삭제 후에도 죽은 경로가 남습니다.
- **컬럼으로 뽑은 기준** — 루틴 생성 프롬프트에 직접 들어가거나 확인 화면에 표시하는 값만 컬럼입니다. 항등식 검증 전용 항목(체수분·단백질·무기질·복부지방률·내장지방레벨·인바디점수·세포외수분비)은 **컬럼으로 만들지 않고 `raw_ocr`에서 읽어 검증만 하고 버립니다.** 검증 후에는 조회할 일이 없습니다.
- `fat_free_mass`(제지방량)만 예외로 컬럼입니다 — "부위별 근육량 합 ≈ 제지방량과 같은 자릿수"(자릿수 오독 탐지)를 `inbody_segment`와 조인해 확인해야 해서, JSONB에만 두면 검증 쿼리가 지저분해집니다.
- `skeletal_muscle_mass` — ⚠️ 기존 설계에 없던 컬럼. 기존 컬럼으로는 근육량을 역산할 수 없는데, 근력 루틴 처방에서 체지방보다 직접적인 입력값입니다.
- `validation` JSONB — 확인 화면에서 `warn`/`error` 필드만 강조 표시하기 위한 값입니다. 전 항목을 똑같이 보여주면 사용자가 대충 넘깁니다.
- `device_type` — 기종마다 인쇄 항목이 달라 "이 필드가 NULL인 게 정상인지"를 판정하려면 필요합니다.
- ⚠️ **DB CHECK는 최소 방어선일 뿐입니다.** 항등식 검증(체중 ≈ 체수분+단백질+무기질+체지방량 / BMI ≈ 체중÷신장² / 좌우 30% 대칭성)은 CHECK로 표현할 수 없으니 애플리케이션에서 수행하고 결과를 `validation`에 씁니다.
- ⚠️ **추출할 컬럼 구성은 아직 확정이 아닙니다.** 실제 결과지 샘플 5~10장을 확보한 뒤 확정해야 합니다.

---

## 8. `inbody_segment`

부위별 근육/체지방 분석 (5부위).

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `inbody_segment_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `inbody_id` | UUID | FK → inbody, NOT NULL, **CASCADE** | |
| `segment` | VARCHAR(20) | NOT NULL, CHECK ('LEFT_ARM','RIGHT_ARM','TRUNK','LEFT_LEG','RIGHT_LEG') | 부위 |
| `lean_mass` | NUMERIC(5,1) | NULL 허용, CHECK (>= 0) | 부위별 근육량(kg) |
| `fat_mass` | NUMERIC(5,1) | NULL 허용, CHECK (>= 0) | 부위별 체지방량(kg) |

**제약**

- UNIQUE `(inbody_id, segment)`

**설계 메모**

- 이 서비스의 결과물이 "부위별" 처방이므로, 부위별 근육량은 사진 판단을 보정할 유일한 수치 근거입니다. `body_part.inbody_segment`를 통해 Sapiens2 크롭과 조인되어 VLM 프롬프트에 함께 들어갑니다.
- 컬럼으로 펼치면 10개(근육 5 + 지방 5)가 되어 테이블로 분리했습니다.
- ⚠️ **부위별 범위 검증(팔 0.5~8kg / 다리 2~20kg)은 CHECK로 넣지 않았습니다** — `segment` 값에 따라 범위가 달라 식이 복잡해지고, 위반 시 INSERT가 실패해 "OCR이 이상한 값을 뽑았다"는 사실 자체가 기록되지 않습니다. 애플리케이션에서 검증하고 `inbody.validation`에 `warn`으로 남깁니다.
- ⚠️ **좌우 대칭성 30% 차이는 경고만, 자동 수정 금지** (실제 심한 비대칭일 수 있음).

---

## 9. `part_diagnosis`

부위별 VLM 비교 진단. 교집합 부위 수만큼(최대 9행) 병렬 생성.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `part_diagnosis_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `session_id` | UUID | FK → analysis_session, NOT NULL, **CASCADE** | |
| `class_name` | VARCHAR(40) | FK → body_part, NOT NULL | 진단 대상 부위 |
| `reference_segment_id` | UUID | FK → body_part_segment, NULL 허용, **SET NULL** | 입력으로 쓴 레퍼런스 크롭 |
| `user_segment_id` | UUID | FK → body_part_segment, NULL 허용, **SET NULL** | 입력으로 쓴 사용자 크롭 |
| `differences` | JSONB | NULL 허용 | 차이점 문자열 배열 |
| `assessment` | TEXT | NULL 허용 | 비교 분석 결과 서술 |
| `gap_level` | VARCHAR(20) | NULL 허용, CHECK ('NONE','SLIGHT','MODERATE','SIGNIFICANT') | 격차 정도 |
| `priority` | SMALLINT | NULL 허용, CHECK (1~5) | 개선 우선순위 (1이 최우선) |
| `confidence` | VARCHAR(10) | NULL 허용, CHECK ('LOW','MEDIUM','HIGH') | VLM 자기 확신도 |
| `raw_response` | JSONB | NULL 허용 | VLM 원본 응답 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**제약**

- UNIQUE `(session_id, class_name)`

**설계 메모**

- `differences`를 TEXT가 아니라 JSONB 배열로 둔 이유 — VLM 출력 스키마가 문자열 배열이고 화면에서 항목별로 나열해야 합니다. 이어붙이면 분리가 불가능해집니다.
- `gap_level` / `priority` — 유사도 점수를 "규칙 기반 합산"으로 낼 경우 이 값들이 계산 입력이 됩니다. 서술만 저장하면 그 선택지가 막힙니다.
- `confidence` — 이미지 품질이 낮으면 `LOW`. 낮은 신뢰도 진단을 루틴 생성 입력에서 제외할지 판단하는 근거입니다.
- `reference_segment_id` / `user_segment_id` — 어떤 크롭으로 낸 진단인지 역추적용. 임계값 튜닝으로 유효 부위 판정이 바뀌면 재진단 대상을 골라내야 합니다.
- ⚠️ **부위 하나가 실패해도 전체를 중단하지 않습니다.** `status='FAILED'` 행이 남고 해당 부위만 결과에서 제외됩니다. 그래서 status가 필요합니다.
- ⚠️ `gap_level` / `confidence` 값을 대문자로 통일했습니다 (v2는 소문자였음).

---

## 10. `overall_diagnosis`

종합 VLM 진단. 세션당 1건.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `overall_diagnosis_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `session_id` | UUID | FK → analysis_session, NOT NULL, UNIQUE, **CASCADE** | |
| `similarity_score` | SMALLINT | NULL 허용, CHECK (0~100) | 레퍼런스 대비 유사도 점수 |
| `score_source` | VARCHAR(20) | NOT NULL, DEFAULT 'VLM', CHECK ('VLM','RULE') | 점수 산출 방식 |
| `score_rationale` | TEXT | NULL 허용 | 점수 근거 한 줄 |
| `summary` | TEXT | NULL 허용 | 전반적인 요약 |
| `priority_parts` | JSONB | NULL 허용 | 우선 개선 부위 배열 |
| `strengths` | JSONB | NULL 허용 | 잘 되어 있는 점 |
| `cautions` | JSONB | NULL 허용 | 주의사항 |
| `raw_response` | JSONB | NULL 허용 | VLM 원본 응답 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**설계 메모**

- `score_source` — 점수 산출 방식(VLM 직접 / 규칙 기반 합산)이 **미정**입니다. 두 방식은 점수 분포가 달라서 섞여 저장되면 나중에 "왜 이 사람만 점수가 낮지"를 설명할 수 없습니다. **방식이 확정되면 이 컬럼은 제거해도 됩니다.**
- `priority_parts` — `body_part.class_name` 값들의 배열. 루틴 생성 프롬프트의 핵심 입력이라 `raw_response`에만 두지 않고 꺼냈습니다.
- ⚠️ `strengths` / `cautions`는 화면 표시 요구사항에 아직 없습니다. **화면에 안 쓸 거면 `raw_response`로 충분하니 빼도 됩니다.**

---

## 11. `month_routine`

4주 루틴. 버전 관리.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `month_routine_id` | UUID | PK, DEFAULT gen_random_uuid() | 루틴 번호 |
| `session_id` | UUID | FK → analysis_session, NOT NULL, **CASCADE** | |
| `version` | INT | NOT NULL, DEFAULT 1 | 버전 |
| `exercise_days_per_week` | INT | NOT NULL, CHECK (1~7) | 1주에 며칠 운동 가능한지 |
| `goal` | TEXT | NULL 허용 | 4주간 핵심 목표 |
| `focus_areas` | JSONB | NULL 허용 | 중점 부위 배열 |
| `start_date` | DATE | NULL 허용 | Day 1 기준일 |
| `generation_type` | VARCHAR(20) | NOT NULL, DEFAULT 'INITIAL', CHECK ('INITIAL','DAYS_CHANGED','FEEDBACK') | 이 버전이 생긴 이유 |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true | 현재 활성 버전 |
| `raw_response` | JSONB | NULL 허용 | LLM 원본 응답 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') | 생성 진행 상태 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**제약**

- UNIQUE `(session_id, version)`
- UNIQUE INDEX `(session_id) WHERE is_active` — 세션당 활성 루틴 1개

**`status` 값의 의미**

- `PENDING` — 생성 요청 접수, 결과는 아직. LLM 호출 전/중 모두 포함
- `DONE` — 응답 파싱과 `day_routine` / `day_routine_exercise` 저장까지 성공. 이 상태여야 화면에 노출
- `FAILED` — LLM 호출 실패 또는 파싱·검증 실패. 재시도 필요

**설계 메모**

- ⚠️ **`user_id UNIQUE` → `session_id` + `version`** — 운동 일수 조정과 피드백 반영이 모두 재생성이라 1인 1행이 성립하지 않습니다.
- ⚠️ **7일 × 4회 분할 생성**을 택하면 1주차 저장 후에도 `status`는 `PENDING`을 유지해야 합니다. 4주차까지 끝난 시점에만 `DONE`으로 바꿉니다. 안 그러면 화면에 7일짜리 루틴이 노출됩니다. (분할 여부는 **미정**)
- ⚠️ **이 테이블의 행은 삭제하지 않습니다.** `is_active = false`로만 비활성화합니다. 삭제하면 `workout_log`가 CASCADE로 딸려 사라집니다.
- `start_date` — 루틴 진행 기준을 "수행 횟수"로 하면 진행도 계산에 안 쓰이고 표시용으로만 남습니다. "날짜 고정"이면 필수 컬럼입니다. (**미정**)
- ⚠️ **`contraindications`는 여기 두지 않았습니다** (→ `analysis_session`). 세션 단위 누적이라 버전마다 복사하면 누적본이 흩어집니다.
- ⚠️ **`job.status`와의 중복** — `job(kind='ROUTINE_GEN')`에도 상태가 있습니다. 역할을 나눠 쓰세요. `job.status`는 **실행 상태**(재시도 횟수·에러·워커 소유권), `month_routine.status`는 **이 데이터를 화면에 써도 되는지**. 하나로 합치려면 `job`을 남기고 이쪽을 지우는 게 맞지만, 그러면 루틴 조회 때마다 `job`을 조인해야 합니다.

---

## 12. `day_routine`

Day 1 ~ Day 28. 휴식일 포함 항상 28행.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `day_routine_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `month_routine_id` | UUID | FK → month_routine, NOT NULL, **CASCADE** | |
| `day_number` | SMALLINT | NOT NULL, CHECK (1~28) | |
| `week_number` | SMALLINT | GENERATED ALWAYS AS `((day_number-1)/7)+1` STORED | 1~4주차 토글용 |
| `is_rest` | BOOLEAN | NOT NULL, DEFAULT false | 휴식일 |
| `title` | VARCHAR(100) | NULL 허용 | 예: "상체 - 밀기" |
| `estimated_duration_min` | SMALLINT | NULL 허용, CHECK (> 0) | 총 예상 운동 시간(분) |

**제약**

- UNIQUE `(month_routine_id, day_number)`

**설계 메모**

- `week_number`는 `day_number`에서 파생되므로 생성 컬럼으로 고정했습니다. 애플리케이션이 따로 계산해 넣으면 어긋날 수 있습니다.
- `title` — 주차 토글에서 "Day N에 어떤 부위를 운동하는지"를 보여주는 값입니다. ⚠️ 부위 코드 배열도 검토했으나 화면 요구가 라벨 표시뿐이고 `day_routine_exercise.target_muscle`에서 집계 가능해 넣지 않았습니다. **부위로 필터링/검색할 계획이 있으면 배열 컬럼이 필요합니다.**
- `estimated_duration_min` — 세트·횟수·휴식으로 역산하면 세트 수행 시간을 가정해야 해서 부정확하므로 LLM 응답값을 저장합니다.
- ⚠️ **`completed_at`을 여기 두지 않았습니다** (→ `workout_log`). 피드백으로 새 버전이 생기면 `day_routine` 행이 통째로 새로 만들어져 **수행 기록이 이전 버전에 갇힙니다.**

---

## 13. `day_routine_exercise`

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `day_routine_exercise_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `day_routine_id` | UUID | FK → day_routine, NOT NULL, **CASCADE** | |
| `order_index` | SMALLINT | NOT NULL | 화면 표시 순서 |
| `name` | VARCHAR(100) | NOT NULL | 운동 이름 |
| `equipment` | VARCHAR(50) | NULL 허용 | 헬스 기구 |
| `target_muscle` | VARCHAR(50) | NULL 허용 | 자극 근육 |
| `sets` | SMALLINT | NOT NULL, CHECK (> 0) | 세트 수 |
| `reps` | SMALLINT | NULL 허용, CHECK (> 0) | 세트당 횟수 |
| `weight_kg` | NUMERIC(5,1) | NULL 허용, CHECK (>= 0) | 중량 |
| `rest_sec` | SMALLINT | NULL 허용, CHECK (>= 0) | 세트 간 휴식(초) |
| `note` | TEXT | NULL 허용 | 수행 팁 / 주의사항 |

**제약**

- UNIQUE `(day_routine_id, order_index)`

**설계 메모**

- `weight_kg` NULL 허용 — 맨몸운동. ⚠️ 초기 중량은 LLM 추정치일 뿐이므로 **"본인에게 맞게 조절하세요" 안내를 UI에 반드시 노출**해야 합니다.
- `reps` NULL 허용 — 플랭크처럼 시간 기반 종목은 `note`에 기록합니다. ⚠️ 시간 기반 종목이 많아질 것 같으면 `duration_sec` 컬럼 추가가 맞습니다.
- `target_muscle`을 `body_part` FK로 걸지 않은 이유 — 삼각근·대퇴사두근 같은 근육명이고 `body_part`는 Sapiens2 세그멘테이션 부위(9개)라 단위가 다릅니다. 억지로 매핑하면 둘 다 망가집니다.

---

## 14. `workout_log`

운동 완료 + 피드백. 루틴 버전과 무관하게 살아남는 수행 기록.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `workout_log_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `session_id` | UUID | FK → analysis_session, NOT NULL, **CASCADE** | |
| `day_number` | SMALLINT | NOT NULL, CHECK (1~28) | 수행한 Day |
| `month_routine_id` | UUID | FK → month_routine, NOT NULL, **CASCADE** | 수행 당시 활성이던 루틴 버전 |
| `completed_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | '운동 마치기' 시각 |
| `feedback_text` | TEXT | NULL 허용 | 예: "운동 이후 무릎이 시큰거려요." |

**제약**

- UNIQUE `(session_id, day_number)`

**설계 메모**

- ⚠️ **`day_routine_id`가 아니라 `(session_id, day_number)`로 잡은 이유** — 피드백으로 새 버전이 생기면 `day_routine` 행이 전부 새로 만들어집니다. 수행 기록이 거기 매달려 있으면 버전이 바뀔 때마다 흩어져서 "지금까지 며칠 수행했는지"를 셀 수 없습니다.
- **오늘의 Day 계산** (수행 횟수 기준일 때) — `COUNT(workout_log WHERE session_id=?) + 1`. 하루 건너뛰어도 루틴이 밀리지 않습니다.
- `month_routine_id` — "어떤 루틴을 하고 남긴 피드백인지". 패치 LLM에 현재 루틴을 넘길 때 기준입니다. ⚠️ CASCADE라서 루틴 버전 행을 지우면 로그가 딸려 삭제됩니다. **루틴 버전은 삭제하지 않습니다.**

---

## 15. `routine_revision`

피드백 기반 루틴 패치 이력.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `routine_revision_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `month_routine_id` | UUID | FK → month_routine, NOT NULL, **CASCADE** | 이 패치로 만들어진 **새 버전** |
| `previous_month_routine_id` | UUID | FK → month_routine, NULL 허용, **SET NULL** | 패치 이전 버전 |
| `source_log_id` | UUID | FK → workout_log, NULL 허용, **SET NULL** | 근거가 된 피드백 |
| `interpretation` | TEXT | NULL 허용 | LLM의 피드백 해석 |
| `changes` | JSONB | NULL 허용 | 변경분 목록 |
| `contraindications_added` | JSONB | NULL 허용 | 이번에 추가된 금기 동작 |
| `raw_response` | JSONB | NULL 허용 | LLM 원본 응답 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**설계 메모**

- ⚠️ **`feedback_text`를 여기 중복 저장하지 않습니다** (→ `workout_log.feedback_text`). 같은 텍스트가 두 곳에 있으면 어느 쪽이 원본인지 모호해집니다.
- `changes` — "변경분만" 출력하는 패치 방식의 산출물입니다. 이게 있어야 사용자에게 "왜 바뀌었는지" 설명할 수 있고 되돌리기도 가능합니다. **전체 재생성은 하지 않습니다.**
- `contraindications_added` — 누적본은 `analysis_session.contraindications`에 있고, 여기에는 이번 회차 증분만 남깁니다.
- ⚠️ **안전 처리는 DB 밖의 문제입니다.** 통증·부상 피드백은 해당 부위 부하 운동을 즉시 제외하고, "통증이 지속되면 운동을 중단하고 전문가 상담을 권합니다" 안내를 노출해야 합니다. 서비스 전반에 "본 루틴은 의학적 조언이 아닙니다" 고지도 필요합니다.

---

## 16. `job`

모든 백그라운드 작업의 큐 + 상태. **"작업 등록 후 결과 조회(폴링)"** 방식의 근간입니다.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `job_id` | UUID | PK, DEFAULT gen_random_uuid() | 프론트에 반환되는 폴링 키 |
| `session_id` | UUID | FK → analysis_session, NOT NULL, **CASCADE** | |
| `kind` | VARCHAR(30) | NOT NULL, CHECK (아래 7종) | 작업 종류 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','PROCESSING','DONE','FAILED') | 실행 상태 |
| `payload` | JSONB | NULL 허용 | 입력 파라미터 |
| `result` | JSONB | NULL 허용 | 산출물 요약 |
| `error` | TEXT | NULL 허용 | 실패 사유 |
| `attempts` | INT | NOT NULL, DEFAULT 0 | 시도 횟수 |
| `started_at` | TIMESTAMPTZ | NULL 허용 | |
| `finished_at` | TIMESTAMPTZ | NULL 허용 | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**`kind` 값**

`SEG_REFERENCE` · `SEG_USER` · `OCR_INBODY` · `VLM_PART` · `VLM_OVERALL` · `ROUTINE_GEN` · `ROUTINE_PATCH`

**인덱스**

- INDEX `(status, kind, created_at)` — 워커 폴링
- INDEX `(session_id)` — 프론트 진행률 조회

**의존 관계** (선행 잡이 `DONE`이어야 시작)

```
SEG_REFERENCE ─┐
SEG_USER      ─┼→ VLM_PART (부위 수만큼) → VLM_OVERALL ─┐
OCR_INBODY ────┘                                        ├→ ROUTINE_GEN
주당 운동일수 입력 ──────────────────────────────────────┘
```

**설계 메모**

- ⚠️ **`kind`·`status` 값을 대문자로 통일했습니다** (v2는 소문자였음). 다른 테이블 status가 전부 대문자라 섞이면 실수가 납니다.
- `job_id`가 폴링 키입니다. 업로드 API가 `job_id`를 반환하고, 프론트가 `GET /jobs/{job_id}`로 상태를 확인합니다. ⚠️ **이 조회도 소유권 검증이 필요합니다** — `job → analysis_session → user_id`가 `X-User-Id`와 일치하는지 확인해야 남의 진행 상황이 안 보입니다.
- `attempts` — 3 미만이면 재큐잉, 초과 시 `FAILED`. ⚠️ `VLM_PART` 하나가 실패해도 전체를 중단하지 않고 해당 부위만 결과에서 제외합니다.
- `payload` — `VLM_PART`는 어느 부위인지, `OCR_INBODY`는 **처리 후 삭제할 임시 이미지의 bucket/path**가 여기 들어갑니다.
- 부위별 VLM은 개수가 많으므로 프론트에서 `완료 3/9` 형태로 보여주면 체감 대기가 줄어듭니다.
- ⚠️ Supabase Realtime을 쓰면 폴링 대신 구독도 가능하지만, 그러려면 프론트가 Supabase에 직접 붙어야 해서 "프론트는 FastAPI만 경유" 전제와 충돌합니다. **폴링으로 갑니다.**

---

## 17. Storage 경로 규칙

| 버킷 | 경로 형식 | 공개 범위 |
|---|---|---|
| `photos` | `{user_id}/{session_id}/reference.jpg`<br>`{user_id}/{session_id}/user.jpg` | private |
| `body-parts` | `{user_id}/{session_id}/reference/{class_name}.png`<br>`{user_id}/{session_id}/user/{class_name}.png` | private |
| `inbody-temp` | `{user_id}/{inbody_id}_{n}.jpg` | private |

**규칙**

- ⚠️ **전체 URL은 DB에 저장하지 않습니다.** `bucket` + `path`만 저장하고 URL은 조회 시점에 조립합니다.
- 조회는 백엔드가 **signed URL**을 발급합니다 (만료 1시간 권장). → **signed URL 발급 API가 API 명세에 반드시 필요합니다.**
- 최상위를 `{user_id}/`로 나눈 이유 — 유저별 폴더가 갈려서 **유저 단위 통째 삭제**가 가능합니다. FK CASCADE는 Storage 파일을 지우지 않으므로 이 prefix 삭제가 유일한 정리 수단입니다.
- ⚠️ 인바디 임시 이미지는 `job(kind='OCR_INBODY')`이 `DONE`이 된 시점에 삭제합니다. 그 전에 지우면 재처리가 불가능합니다.
- ⚠️ `class_id`는 경로에 넣지 않습니다. 모델 버전에 따라 재배열될 수 있습니다.

---

## 18. 전체 관계도

```
users
 └─(CASCADE) analysis_session
      ├─(CASCADE) photo ─(CASCADE) body_part_segment ─┐
      ├─(CASCADE) inbody ─(CASCADE) inbody_segment    │  body_part
      ├─(CASCADE) part_diagnosis ─(SET NULL)──────────┴──(마스터)
      ├─(CASCADE) overall_diagnosis
      ├─(CASCADE) month_routine
      │      ├─(CASCADE) day_routine ─(CASCADE) day_routine_exercise
      │      └─(CASCADE) routine_revision
      ├─(CASCADE) workout_log
      └─(CASCADE) job
```

---

## 19. 아직 안 정해진 것

| # | 항목 | 영향받는 컬럼 | 상태 |
|---|---|---|---|
| 1 | **`users`에 실제로 들어갈 컬럼** | `users` 전체 | **미정.** 로그인 없는 상태에서 "유저"를 뭘로 정의할지 |
| 2 | Sapiens2 실제 클래스명·개수 | `body_part` seed, `body_part_segment.class_name` | **미확인.** 추론 결과 라벨 직접 확인 필요 |
| 3 | WIM 3D 바디스캔 결과지 구조 | `inbody` 컬럼 구성 | **확인 실패.** 실물 샘플 확보 전까지 `device_type`+`raw_ocr`로만 수용 |
| 4 | 인바디 기종별 인쇄 항목 | `inbody` 컬럼 NULL 여부 | 샘플 5~10장 확보 후 확정 |
| 5 | 유사도 점수 산출 방식 | `overall_diagnosis.score_source` (확정 시 제거 가능) | 미정 |
| 6 | 루틴 진행 기준 (날짜 / 수행 횟수) | `month_routine.start_date` 필요 여부 | 미정 (권장: 수행 횟수) |
| 7 | 루틴 생성 분할 (28일 일괄 / 7일×4) | `month_routine.status` 전환 시점 | 미정 |
| 8 | VLM 입력 형식 (크롭 / 원본+하이라이트) | `body_part_segment.bbox_*` 사용 여부 | 미정 |
| 9 | 3방향 촬영 필요 여부 | `photo.kind` 값 집합 | **확인 필요** (기존 설계에는 있었음) |
| 10 | 레퍼런스 출처 (사용자 업로드 / 프리셋) | `analysis_session.reference_source` 실사용 여부 | 미정 |
| 11 | 시연 후 데이터 삭제 정책 | 없음 (운영 정책) | **미정.** 사람 사진이라 정해두는 게 좋음 |

**튜닝 대상 잠정값** (DB에 확정값처럼 옮기지 말 것)

`TOL` 40° · `F_MIN` 0.80 · `THRESHOLD` 0.90 · `N_HOLD` 15프레임 · `MIN_PIXELS` 1,500px · `MIN_RATIO` 0.5% · 인바디 항등식 허용 오차 ±3~5%

---

## 20. 스키마 운영 규칙

- 스키마는 **Supabase 콘솔에서 관리**하고, 변경 후 `schema.sql`을 레포에 커밋합니다.
- ⚠️ **스키마를 바꾸면 반드시 팀원에게 공지합니다.** 마이그레이션 도구를 안 쓰므로 이게 유일한 안전장치입니다.
- 전 테이블 **RLS 활성화, 정책은 생성하지 않음** — `service_role` 키만 통과합니다.
- 환경변수: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, 외부 LLM/VLM API 키. **`.env`로만 관리, 커밋 금지.**
- ⚠️ `service_role` 키는 RLS를 전부 우회합니다. **절대 프론트에 노출 금지.**
