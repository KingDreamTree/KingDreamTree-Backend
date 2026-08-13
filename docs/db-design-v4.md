# DB 설계

| | |
|---|---|
| **버전** | v4 |
| **최종 수정일** | 2026-08-13 |
| **대상** | Supabase (PostgreSQL) — DB + Storage만 사용, **Auth 미사용** |
| **테이블 수** | **16개** (v3의 15개 + `segmentation`) |
| **이전 버전** | `docs/db-design-v3.md` |

---

## 0. v3 대비 변경

| # | 변경 | 이유 |
|---|---|---|
| 1 | **`segmentation` 테이블 신설** | 부위별 라벨 맵(단일 PNG)을 1급 데이터로 저장. 프론트 오버레이 시각화 요구 |
| 2 | `body_part_segment`의 부모가 `photo` → **`segmentation`** | 부위 통계는 "어느 맵에서 뽑았는지"에 매달려야 함 |
| 3 | `body_part_segment.mask_path` **제거** | 라벨 맵이 모든 부위 마스크를 담으므로 중복 |
| 4 | `body_part_segment.crop_path` **NULL 허용** (파생 캐시로 강등) | 크롭은 맵 + 원본에서 언제든 재생성 가능 |
| 5 | `body_part_segment.class_id` → **`label_value`** (NOT NULL) | 맵의 픽셀 값. "참고용"이 아니라 맵을 읽으려면 **반드시 필요한 값** |
| 6 | `body_part_segment`에 **`is_valid`** 추가, **모든 검출 클래스에 행 생성** | v3는 유효 부위만 행을 만들어 "왜 제외됐는지"를 알 수 없었음 |
| 7 | `body_part` 마스터를 **Sapiens2 전체 클래스로 확장** + `is_comparable`, `color_hex`, `display_order` | 옷·머리 등도 맵에 들어가므로 라벨 검증·시각화에 필요 |
| 8 | `segmentations` 버킷 신설 | 맵 파일 저장소 |

> **핵심 원칙 변경** — v3는 "최종 분석 결과만 남긴다"였습니다. v4는 **"추론 결과(맵)를 원본으로 남기고, 크롭·통계는 거기서 파생시킨다"** 입니다. 임계값 튜닝이나 시각화 요구가 바뀌어도 Sapiens2를 다시 돌리지 않아도 됩니다.

**v3에서 그대로인 것** — UUID PK / `X-User-Id` 인증 없음 / hard delete + CASCADE / 대문자 status / bucket+path 분리 / RLS 켜고 정책 없음. 각 결정의 근거는 v3 문서를 참조하세요.

---

## 1. ⚠️ 세그멘테이션 저장 방식 (v4의 핵심)

### 1.1 무엇을 저장하는가

**부위별 이미지 N장이 아니라, 부위 라벨이 픽셀 값으로 들어간 PNG 1장**을 저장합니다.

```
map.png  (8-bit 그레이스케일, 원본과 같은 비율)
  픽셀 값 0 = Background
          1 = Torso
          2 = Left_Upper_Arm
          3 = Right_Upper_Arm
          ...
```

그리고 그 **값 ↔ 클래스명 대응표를 `segmentation.label_map` JSONB에 행마다 저장**합니다.

```json
{ "0": "Background", "1": "Torso", "2": "Left_Upper_Arm", "17": "Upper_Clothing" }
```

### 1.2 ⚠️ `label_map`을 행마다 저장하는 이유 — 이게 제일 중요합니다

Sapiens2 모델 버전이 바뀌면 클래스 ID가 재배열됩니다. `label_map`을 코드 상수로만 두면, 모델을 올린 순간 **예전에 저장된 모든 맵이 "왼팔이 오른다리로 읽히는" 상태가 되고, 에러는 하나도 안 납니다.**

- `label_map`은 **추론 당시의 매핑을 그 행에 박제**합니다.
- `model_name` + `model_version`도 같이 저장합니다. 모델을 올린 뒤 "어떤 행을 재추론해야 하는지" 골라내려면 필요합니다.
- ⚠️ 워커는 기동 시 `label_map`을 **DB의 `body_part` 마스터와 대조**해서, 모르는 클래스명이 나오면 경고 로그를 남기고 진행하세요. 조용히 넘어가면 seed 불일치를 못 잡습니다.

### 1.3 ⚠️ 파일 포맷 규칙 (어기면 조용히 망가짐)

| 규칙 | 이유 |
|---|---|
| **PNG만.** JPEG·손실 WebP 절대 금지 | 손실 압축은 인접 라벨 값을 섞습니다. `1`과 `3` 사이에 없는 클래스 `2`가 생깁니다 |
| **8-bit 그레이스케일, 알파 채널 없음** | RGB로 저장하면 3배 커지고, 알파가 있으면 브라우저가 프리멀티플라이하며 값을 바꿉니다 |
| **ICC 프로파일 넣지 않기** | 브라우저 색 관리가 픽셀 값을 보정해버립니다 |
| **리사이즈는 nearest-neighbor만** | bilinear/bicubic 보간은 라벨을 섞어 **존재하지 않는 클래스를 만들어냅니다.** 가장 흔한 사고입니다 |
| **클래스 수는 255개 이하** | 8-bit 한계. Sapiens2는 28개 수준이라 여유 있음 |

> 팔레트 PNG(P 모드)는 쓰지 마세요. 브라우저가 캔버스에 그릴 때 팔레트를 RGB로 펼치므로 값을 되돌리는 과정이 하나 더 생깁니다. **그레이스케일이 가장 안전합니다.**

### 1.4 프론트엔드 오버레이 절차

```js
// 1) 맵과 원본을 각각 로드 — crossOrigin 필수
const map = new Image();
map.crossOrigin = "anonymous";       // ⚠️ 없으면 캔버스가 오염되어 getImageData가 던집니다
map.src = segmentation.map_url;      // signed URL

// 2) 오프스크린 캔버스에 그려서 픽셀 값 읽기
const c = document.createElement("canvas");
c.width = seg.map_width; c.height = seg.map_height;
const ctx = c.getContext("2d", { willReadFrequently: true });
ctx.drawImage(map, 0, 0);
const src = ctx.getImageData(0, 0, c.width, c.height);

// 3) label_value → color_hex 로 칠하기 (label_map + body_part.color_hex 사용)
const out = ctx.createImageData(c.width, c.height);
for (let i = 0; i < src.data.length; i += 4) {
  const label = src.data[i];                 // 그레이스케일이므로 R 채널이 곧 라벨 값
  const rgba = palette[label];               // 서버가 내려준 팔레트
  if (!rgba) continue;                       // 색을 안 칠할 클래스(배경·옷)는 투명
  out.data[i]   = rgba[0]; out.data[i+1] = rgba[1];
  out.data[i+2] = rgba[2]; out.data[i+3] = 140;   // 반투명
}
ctx.putImageData(out, 0, 0);

// 4) 원본 위에 CSS로 겹치기 (맵 크기 ≠ 원본 크기일 수 있으므로 늘려서 맞춤)
//    image-rendering: pixelated 를 주면 경계가 뭉개지지 않습니다
```

**⚠️ 함정 3가지**

1. **캔버스 오염(tainted canvas).** signed URL은 다른 오리진이므로 `img.crossOrigin = "anonymous"` 없이 그리면 `getImageData()`가 `SecurityError`를 던집니다. **Supabase Storage 버킷에 CORS 설정도 필요합니다.** 프론트에서 제일 먼저 막히는 지점이니 미리 알려주세요.
2. **맵 크기 ≠ 원본 크기.** Sapiens2 추론 해상도가 원본과 다를 수 있습니다. `map_width`/`map_height`를 반드시 저장하고, 프론트는 CSS로 원본 크기에 맞춰 늘립니다. **JS로 리샘플하지 마세요** (§1.3).
3. **`image-rendering: pixelated`.** 안 주면 브라우저가 부드럽게 늘리면서 경계에 없는 색이 생깁니다. 색칠 결과만 볼 때는 상관없지만, 확대해서 보면 티가 납니다.

### 1.5 크롭 파일은 어떻게 되나

**맵이 원본, 크롭은 파생 캐시**입니다.

- VLM 입력 형식이 "부위 크롭"으로 확정되면(미확정 #8), 워커가 맵 + 원본에서 크롭을 잘라 `body-parts` 버킷에 저장하고 `crop_path`를 채웁니다.
- "원본 + 하이라이트"로 확정되면 크롭 파일은 **아예 만들지 않습니다.** `crop_path`는 계속 NULL.
- 어느 쪽이든 DB 스키마는 그대로입니다. **이게 크롭을 NULL 허용으로 강등한 이유입니다.**
- 크롭을 지워도 맵이 있으면 언제든 재생성됩니다. 무료 티어 용량이 빠듯해지면 크롭부터 버리세요.

---

## 2. 공통 규칙

| 항목 | 규칙 |
|---|---|
| PK | `UUID`, DEFAULT `gen_random_uuid()` (단 `body_part`는 `class_name`) |
| FK | `UUID` |
| Storage | `*_bucket VARCHAR(63)` + `*_path VARCHAR(500)`. **전체 URL 저장 금지** |
| 진행 상태 | `VARCHAR(20)` + CHECK, **대문자** |
| 시각 | `TIMESTAMPTZ` NOT NULL DEFAULT `now()` |
| 반정형 | `JSONB` |
| 부위 식별자 | **DB 조인은 `class_name`, 맵 픽셀 해석은 `label_value`** |
| 삭제 | hard delete + ON DELETE CASCADE |

> ⚠️ **`label_value`로 테이블을 조인하지 마세요.** 맵을 읽을 때만 쓰는 값이고, 모델 버전이 다르면 같은 숫자가 다른 부위입니다. 조인 키는 항상 `class_name`입니다.

---

## 3. `users` — v3에서 변경 없음

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `user_id` | UUID | PK, DEFAULT gen_random_uuid() |
| `is_pro_user` | BOOLEAN | NOT NULL, DEFAULT false |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

⚠️ 로그인이 없어 `user_id`를 잃으면 복구 수단이 없습니다. 실서비스 전환 시 로그인 필수. 넣을 컬럼은 여전히 **미정**(미확정 #1).

---

## 4. `analysis_session` — v3에서 변경 없음

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `session_id` | UUID | PK |
| `user_id` | UUID | FK → users, NOT NULL, CASCADE |
| `reference_source` | VARCHAR(20) | NOT NULL, DEFAULT 'USER_UPLOAD', CHECK ('USER_UPLOAD','PRESET') |
| `contraindications` | JSONB | NOT NULL, DEFAULT '[]' |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'ACTIVE', CHECK ('ACTIVE','ARCHIVED') |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

- UNIQUE INDEX `(user_id) WHERE status = 'ACTIVE'`
- INDEX `(user_id, created_at DESC)`

⚠️ **모든 소유권 검증의 기준점.** 자식 테이블 조회는 전부 여기까지 조인해 `user_id = X-User-Id`를 확인합니다.

---

## 5. `photo` — 컬럼 1개 의미 변경

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `photo_id` | UUID | PK | |
| `session_id` | UUID | FK → analysis_session, NOT NULL, CASCADE | |
| `kind` | VARCHAR(20) | NOT NULL, CHECK ('REFERENCE','USER') | |
| `storage_bucket` | VARCHAR(63) | NOT NULL, DEFAULT 'photos' | |
| `storage_path` | VARCHAR(500) | NOT NULL | `{user_id}/{session_id}/reference.jpg` |
| `width` | INT | NULL 허용 | 저장된 원본 가로 |
| `height` | INT | NULL 허용 | 저장된 원본 세로 |
| `capture_source` | VARCHAR(20) | NULL 허용, CHECK ('CAPTURE','UPLOAD') | |
| `pose_landmarks` | JSONB | NULL 허용 | MediaPipe 33개 랜드마크 |
| `pose_scale_basis` | VARCHAR(20) | NULL 허용, CHECK ('TORSO','HIP_KNEE') | |
| `pose_similarity` | NUMERIC(5,2) | NULL 허용, CHECK (0~100) | |
| `framing_score` | NUMERIC(4,3) | NULL 허용, CHECK (0~1) | |
| `pose_person_area_ratio` | REAL | NULL 허용, CHECK (0~1) | ⚠️ **MediaPipe 기준 추정치** (프레이밍 판정용) |
| `multi_person` | BOOLEAN | NOT NULL, DEFAULT false | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

- UNIQUE `(session_id, kind)`

**변경점**

- ⚠️ v3의 `person_area_ratio` → **`pose_person_area_ratio`로 개명.** 정확한 인물 면적은 이제 `segmentation.person_pixel_count`에 있습니다. 이름이 같으면 `area_ratio`의 분모로 어느 쪽을 쓸지 헷갈립니다. **`body_part_segment.area_ratio`의 분모는 항상 `segmentation.person_pixel_count`입니다.**
- ⚠️ **좌우 반전 규칙 유지** — 저장 이미지·랜드마크·맵 전부 반전되지 않은 카메라 원본 기준. 미러링은 프론트 CSS만.

---

## 6. `segmentation` 🆕

사진 1장의 Sapiens2 추론 결과. **부위별 라벨 맵 1장.**

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `segmentation_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `photo_id` | UUID | FK → photo, NOT NULL, **UNIQUE**, CASCADE | 사진 1장 = 맵 1장 |
| `storage_bucket` | VARCHAR(63) | NOT NULL, DEFAULT 'segmentations' | |
| `map_path` | VARCHAR(500) | NOT NULL | 8-bit 그레이스케일 PNG 경로 |
| `map_width` | INT | NOT NULL, CHECK (> 0) | 맵 가로 (원본과 다를 수 있음) |
| `map_height` | INT | NOT NULL, CHECK (> 0) | 맵 세로 |
| `label_map` | JSONB | NOT NULL | `{"1":"Torso", ...}` ⚠️ 추론 당시 매핑 |
| `model_name` | VARCHAR(50) | NOT NULL | `'sapiens2'` |
| `model_version` | VARCHAR(50) | NOT NULL | 체크포인트 식별자 |
| `person_pixel_count` | INT | NOT NULL, CHECK (>= 0) | 배경 아닌 픽셀 수 |
| `person_area_ratio` | REAL | NOT NULL, CHECK (0~1) | `person_pixel_count / (map_width*map_height)` |
| `detected_class_count` | SMALLINT | NOT NULL | 맵에 실제로 등장한 클래스 수 |
| `inference_ms` | INT | NULL 허용 | 추론 소요 시간 (성능 튜닝용) |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**설계 메모**

- ⚠️ **`status` 컬럼이 없습니다.** 워커가 성공했을 때만 행을 만듭니다. 즉 **행의 존재 = 세그멘테이션 완료**입니다. 진행/실패 상태는 `job(kind='SEG_*')`가 소스입니다. (v3의 "job.status는 실행 상태, 도메인 status는 화면 노출 가능 여부" 원칙에서, 이 테이블은 후자가 불필요합니다.)
- ⚠️ **`UNIQUE(photo_id)`** — 사진 1장당 맵 1장. 모델을 올려 재추론할 때는 기존 행을 **교체**합니다(파일 삭제 → 행 삭제 → 재생성).
  - 나중에 여러 모델 버전의 결과를 나란히 비교하고 싶어지면 UNIQUE를 빼고 `is_active BOOLEAN`을 추가하세요. **지금은 넣지 않습니다.**
- `inference_ms` — t3.large CPU에서 수십 초가 걸리므로, 최적화 전후 비교와 타임아웃 값 결정에 실측치가 필요합니다.
- ⚠️ **맵 파일 삭제는 CASCADE로 안 됩니다.** 사진 교체 시 `body_part_segment`의 크롭 파일 + 맵 파일을 애플리케이션에서 먼저 지워야 합니다.

---

## 7. `body_part` (마스터 · seed) — 대폭 확장

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `class_name` | VARCHAR(40) | PK | Sapiens2 클래스명 |
| `name_ko` | VARCHAR(40) | NOT NULL | 화면 표시용 한글 |
| `part_group` | VARCHAR(20) | NOT NULL, CHECK ('UPPER','CORE','LOWER','OTHER') | ⚠️ `OTHER` 추가 |
| `inbody_segment` | VARCHAR(20) | NULL 허용, CHECK ('LEFT_ARM','RIGHT_ARM','TRUNK','LEFT_LEG','RIGHT_LEG') | 인바디 매핑 |
| `is_comparable` | BOOLEAN | NOT NULL, DEFAULT false | 🆕 VLM 비교 대상인가 |
| `color_hex` | CHAR(7) | NULL 허용 | 🆕 오버레이 색 (`#RRGGBB`). NULL이면 칠하지 않음 |
| `display_order` | SMALLINT | NOT NULL, DEFAULT 0 | 🆕 범례/목록 정렬 |

**seed — 비교 대상 9개 (`is_comparable = true`)**

| class_name | name_ko | part_group | inbody_segment | color_hex |
|---|---|---|---|---|
| `Torso` | 몸통 | CORE | TRUNK | `#4C6EF5` |
| `Left_Upper_Arm` | 왼팔 상완 | UPPER | LEFT_ARM | `#F76707` |
| `Left_Lower_Arm` | 왼팔 전완 | UPPER | LEFT_ARM | `#FFA94D` |
| `Right_Upper_Arm` | 오른팔 상완 | UPPER | RIGHT_ARM | `#2F9E44` |
| `Right_Lower_Arm` | 오른팔 전완 | UPPER | RIGHT_ARM | `#69DB7C` |
| `Left_Upper_Leg` | 왼쪽 허벅지 | LOWER | LEFT_LEG | `#AE3EC9` |
| `Left_Lower_Leg` | 왼쪽 종아리 | LOWER | LEFT_LEG | `#DA77F2` |
| `Right_Upper_Leg` | 오른쪽 허벅지 | LOWER | RIGHT_LEG | `#E03131` |
| `Right_Lower_Leg` | 오른쪽 종아리 | LOWER | RIGHT_LEG | `#FF8787` |

**seed — 나머지 클래스 (`is_comparable = false`, `part_group = 'OTHER'`, `color_hex = NULL`)**

`Background` · `Apparel` · `Upper_Clothing` · `Lower_Clothing` · `Shoe` · `Sock` · `Eyeglasses` · `Hair` · `Face_Neck` · `Lip` · `Teeth` · `Tongue` · `Hand` · `Foot` (⚠️ 실제 목록은 확인 후 확정)

**설계 메모**

- ⚠️ **v3와 달리 전체 클래스를 넣습니다.** 맵에 모든 클래스가 들어가므로, 마스터가 9개만 알고 있으면 `label_map` 검증도 못 하고 `body_part_segment`의 FK도 걸 수 없습니다.
- `is_comparable` — 비교 대상 판정은 이제 **`is_comparable AND is_valid`** 두 조건입니다. 워커의 `SKIN_CLASSES` 상수는 없애고 이 테이블을 기동 시 읽으세요.
- `color_hex` — ⚠️ **프론트에 색을 하드코딩하게 두지 마세요.** 부위가 추가되거나 이름이 바뀌면 색과 라벨이 어긋납니다. 서버가 팔레트를 내려줍니다.
  - 좌/우가 비슷한 색 계열(주황↔초록, 보라↔빨강)인 이유는 **좌우 반전 사고를 눈으로 잡기 위해서**입니다. 색이 좌우 대칭으로 뒤집혀 보이면 §5의 반전 규칙이 깨진 것입니다.
- ⚠️ **`class_name` 값은 여전히 확정이 아닙니다** (미확정 #2). 실제 추론 라벨을 찍어본 뒤 seed를 확정하세요.

---

## 8. `body_part_segment` — 구조 변경

맵에서 파생된 **부위별 통계**. ⚠️ v3와 달리 **검출된 모든 클래스에 행을 만듭니다.**

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `segment_id` | UUID | PK, DEFAULT gen_random_uuid() | |
| `segmentation_id` | UUID | FK → segmentation, NOT NULL, CASCADE | ⚠️ v3의 `photo_id`를 대체 |
| `class_name` | VARCHAR(40) | FK → body_part, NOT NULL | 조인 키 |
| `label_value` | SMALLINT | NOT NULL, CHECK (0~255) | ⚠️ **이 맵에서의 픽셀 값** |
| `pixel_count` | INT | NOT NULL, CHECK (>= 0) | |
| `area_ratio` | REAL | NOT NULL, CHECK (0~1) | `pixel_count / segmentation.person_pixel_count` |
| `bbox_x` | INT | NOT NULL | ⚠️ **맵 좌표계 기준** |
| `bbox_y` | INT | NOT NULL | |
| `bbox_w` | INT | NOT NULL | |
| `bbox_h` | INT | NOT NULL | |
| `is_truncated` | BOOLEAN | NOT NULL, DEFAULT false | bbox가 이미지 경계에 접함 |
| `is_valid` | BOOLEAN | NOT NULL | 🆕 유효 부위 판정 통과 여부 |
| `invalid_reason` | VARCHAR(30) | NULL 허용, CHECK ('TOO_SMALL','TOO_SMALL_RATIO','TRUNCATED','NOT_COMPARABLE') | 🆕 |
| `crop_bucket` | VARCHAR(63) | NULL 허용 | 🆕 파생 크롭 (없으면 NULL) |
| `crop_path` | VARCHAR(500) | NULL 허용 | ⚠️ v3에서 NOT NULL → NULL 허용 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

- UNIQUE `(segmentation_id, class_name)`
- INDEX `(segmentation_id) WHERE is_valid` — 비교 대상 조회

**설계 메모**

- ⚠️ **`mask_path` 제거.** 라벨 맵에서 `pixel == label_value`로 마스크를 만들면 되므로 별도 파일이 낭비입니다.
- ⚠️ **모든 검출 클래스에 행을 만드는 이유** — v3는 유효 부위만 행을 만들어서, 결과 화면에서 "왼팔은 왜 빠졌지?"에 답할 수 없었습니다. 이제 `is_valid=false` + `invalid_reason='TOO_SMALL'` 행이 남아 **"왼팔이 옷에 가려져 노출이 부족합니다"** 같은 안내를 낼 수 있습니다.
- ⚠️ **`bbox_*`는 맵 좌표계입니다.** 원본 위에 그리려면 `map_width`/`map_height` → `photo.width`/`photo.height`로 스케일해야 합니다. v3는 원본 좌표라고 적혀 있었는데, 맵 해상도가 원본과 다를 수 있으므로 **맵 기준으로 통일**합니다. 혼동하면 박스가 어긋납니다.
- `pixel_count` / `area_ratio` 원값 저장은 v3와 동일한 이유 — 임계값(`MIN_PIXELS` 1,500 / `MIN_RATIO` 0.5%)이 튜닝 대상이라 나중에 재판정할 수 있어야 합니다. **`is_valid`는 캐시일 뿐, 원값이 진실입니다.**
- **비교 대상 계산**
  ```sql
  -- 레퍼런스 ∩ 사용자, 둘 다 유효하고 비교 대상인 부위
  SELECT bps.class_name
  FROM body_part_segment bps
  JOIN segmentation s  ON s.segmentation_id = bps.segmentation_id
  JOIN photo p         ON p.photo_id = s.photo_id
  JOIN body_part bp    ON bp.class_name = bps.class_name
  WHERE p.session_id = $1 AND bps.is_valid AND bp.is_comparable
  GROUP BY bps.class_name
  HAVING COUNT(DISTINCT p.kind) = 2;
  ```

---

## 9. `inbody` — v3에서 변경 없음

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `inbody_id` | UUID | PK |
| `session_id` | UUID | FK → analysis_session, NOT NULL, CASCADE |
| `device_type` | VARCHAR(30) | NULL 허용 |
| `measured_at` | DATE | NULL 허용 |
| `age` | INT | NULL, CHECK (1~120) |
| `gender` | VARCHAR(10) | NULL, CHECK ('MALE','FEMALE') |
| `height` | NUMERIC(5,1) | NULL, CHECK (120~220) |
| `weight` | NUMERIC(5,1) | NULL, CHECK (25~250) |
| `bmi` | NUMERIC(4,1) | NULL, CHECK (10~60) |
| `body_fat_mass` | NUMERIC(5,1) | NULL, CHECK (0~150) |
| `body_fat_percentage` | NUMERIC(4,1) | NULL, CHECK (1~70) |
| `skeletal_muscle_mass` | NUMERIC(5,1) | NULL, CHECK (10~60) |
| `fat_free_mass` | NUMERIC(5,1) | NULL, CHECK (10~150) |
| `bmr_kcal` | INT | NULL, CHECK (500~5000) |
| `raw_ocr` | JSONB | NULL 허용 |
| `validation` | JSONB | NULL 허용 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') |
| `validation_error` | TEXT | NULL 허용 |
| `verified_at` | TIMESTAMPTZ | NULL 허용 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

- INDEX `(session_id, measured_at DESC NULLS LAST)`

⚠️ 결과지 이미지는 저장하지 않습니다. 임시 경로는 `job.payload`에. 항등식 검증은 CHECK가 아니라 애플리케이션에서 → `validation`. 컬럼 구성은 실물 샘플 확보 후 확정(미확정 #3, #4).

---

## 10. `inbody_segment` — v3에서 변경 없음

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `inbody_segment_id` | UUID | PK |
| `inbody_id` | UUID | FK → inbody, NOT NULL, CASCADE |
| `segment` | VARCHAR(20) | NOT NULL, CHECK ('LEFT_ARM','RIGHT_ARM','TRUNK','LEFT_LEG','RIGHT_LEG') |
| `lean_mass` | NUMERIC(5,1) | NULL, CHECK (>= 0) |
| `fat_mass` | NUMERIC(5,1) | NULL, CHECK (>= 0) |

- UNIQUE `(inbody_id, segment)`

⚠️ 부위별 범위 검증·좌우 대칭성은 애플리케이션에서 경고만. 자동 수정 금지.

---

## 11. `part_diagnosis` — FK 대상 변경

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `part_diagnosis_id` | UUID | PK | |
| `session_id` | UUID | FK → analysis_session, NOT NULL, CASCADE | |
| `class_name` | VARCHAR(40) | FK → body_part, NOT NULL | |
| `reference_segment_id` | UUID | FK → **body_part_segment**, NULL 허용, SET NULL | |
| `user_segment_id` | UUID | FK → **body_part_segment**, NULL 허용, SET NULL | |
| `vlm_input_type` | VARCHAR(20) | NOT NULL, DEFAULT 'CROP', CHECK ('CROP','HIGHLIGHT') | 🆕 어떤 형식으로 넣었는지 |
| `differences` | JSONB | NULL 허용 | |
| `assessment` | TEXT | NULL 허용 | |
| `gap_level` | VARCHAR(20) | NULL, CHECK ('NONE','SLIGHT','MODERATE','SIGNIFICANT') | |
| `priority` | SMALLINT | NULL, CHECK (1~5) | |
| `confidence` | VARCHAR(10) | NULL, CHECK ('LOW','MEDIUM','HIGH') | |
| `raw_response` | JSONB | NULL 허용 | |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

- UNIQUE `(session_id, class_name)`

**변경점**

- 🆕 **`vlm_input_type`** — VLM 입력 형식이 미확정(#8)이라, 두 방식을 섞어 저장하면 나중에 "왜 이 부위만 진단이 이상하지"를 설명할 수 없습니다. `overall_diagnosis.score_source`와 같은 취지의 컬럼이고, **방식이 확정되면 제거해도 됩니다.**
- ⚠️ 부위 하나가 실패해도 전체 중단 없음. `status='FAILED'` 행만 남습니다.

---

## 12. `overall_diagnosis` — v3에서 변경 없음

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `overall_diagnosis_id` | UUID | PK |
| `session_id` | UUID | FK → analysis_session, NOT NULL, UNIQUE, CASCADE |
| `similarity_score` | SMALLINT | NULL, CHECK (0~100) |
| `score_source` | VARCHAR(20) | NOT NULL, DEFAULT 'VLM', CHECK ('VLM','RULE') |
| `score_rationale` | TEXT | NULL 허용 |
| `summary` | TEXT | NULL 허용 |
| `priority_parts` | JSONB | NULL 허용 |
| `strengths` | JSONB | NULL 허용 |
| `cautions` | JSONB | NULL 허용 |
| `raw_response` | JSONB | NULL 허용 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

---

## 13. `month_routine` — v3에서 변경 없음

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `month_routine_id` | UUID | PK |
| `session_id` | UUID | FK → analysis_session, NOT NULL, CASCADE |
| `version` | INT | NOT NULL, DEFAULT 1 |
| `exercise_days_per_week` | INT | NOT NULL, CHECK (1~7) |
| `goal` | TEXT | NULL 허용 |
| `focus_areas` | JSONB | NULL 허용 |
| `start_date` | DATE | NULL 허용 |
| `generation_type` | VARCHAR(20) | NOT NULL, DEFAULT 'INITIAL', CHECK ('INITIAL','DAYS_CHANGED','FEEDBACK') |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true |
| `raw_response` | JSONB | NULL 허용 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','DONE','FAILED') |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

- UNIQUE `(session_id, version)` / UNIQUE INDEX `(session_id) WHERE is_active`

⚠️ **행을 삭제하지 않습니다.** `is_active=false`로만 내립니다 (`workout_log`가 CASCADE로 딸려 삭제되므로).

---

## 14. `day_routine` / 15. `day_routine_exercise` — v3에서 변경 없음

`day_routine`: `day_routine_id` PK / `month_routine_id` FK CASCADE / `day_number` SMALLINT CHECK(1~28) / `week_number` GENERATED `((day_number-1)/7)+1` STORED / `is_rest` BOOLEAN / `title` VARCHAR(100) / `estimated_duration_min` SMALLINT CHECK(>0) — UNIQUE `(month_routine_id, day_number)`

`day_routine_exercise`: `day_routine_exercise_id` PK / `day_routine_id` FK CASCADE / `order_index` SMALLINT / `name` VARCHAR(100) NOT NULL / `equipment` VARCHAR(50) / `target_muscle` VARCHAR(50) / `sets` SMALLINT CHECK(>0) / `reps` SMALLINT NULL CHECK(>0) / `weight_kg` NUMERIC(5,1) NULL CHECK(>=0) / `rest_sec` SMALLINT NULL / `note` TEXT — UNIQUE `(day_routine_id, order_index)`

⚠️ `completed_at`은 여기 두지 않습니다(→ `workout_log`). `target_muscle`은 `body_part` FK가 아닙니다(근육명 vs 세그멘테이션 부위, 단위가 다름).

---

## 16. `workout_log` / 17. `routine_revision` — v3에서 변경 없음

`workout_log`: `workout_log_id` PK / `session_id` FK CASCADE / `day_number` SMALLINT CHECK(1~28) / `month_routine_id` FK CASCADE / `completed_at` / `feedback_text` TEXT — UNIQUE `(session_id, day_number)`

`routine_revision`: `routine_revision_id` PK / `month_routine_id` FK CASCADE / `previous_month_routine_id` FK SET NULL / `source_log_id` FK → workout_log SET NULL / `interpretation` TEXT / `changes` JSONB / `contraindications_added` JSONB / `raw_response` JSONB / `created_at`

⚠️ 수행 기록을 `day_routine_id`가 아니라 `(session_id, day_number)`에 매단 이유 — 버전이 바뀌어도 기록이 흩어지지 않게. `feedback_text`는 `workout_log`가 원본, 중복 저장 금지.

---

## 18. `job` — `kind` 1종 추가 가능성

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `job_id` | UUID | PK |
| `session_id` | UUID | FK → analysis_session, NOT NULL, CASCADE |
| `kind` | VARCHAR(30) | NOT NULL, CHECK (아래) |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'PENDING', CHECK ('PENDING','PROCESSING','DONE','FAILED') |
| `payload` / `result` | JSONB | NULL 허용 |
| `error` | TEXT | NULL 허용 |
| `attempts` | INT | NOT NULL, DEFAULT 0 |
| `started_at` / `finished_at` | TIMESTAMPTZ | NULL 허용 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

**`kind`** — `SEG_REFERENCE` · `SEG_USER` · `OCR_INBODY` · `VLM_PART` · `VLM_OVERALL` · `ROUTINE_GEN` · `ROUTINE_PATCH`

- INDEX `(status, kind, created_at)` / INDEX `(session_id)`

**설계 메모**

- `SEG_*` 잡의 `result`에 맵 요약을 넣으세요: `{"segmentation_id": "...", "detected": 12, "valid_comparable": 7, "invalid": [{"class_name":"Left_Lower_Leg","reason":"TOO_SMALL"}]}`. 프론트가 "왼쪽 종아리는 노출이 부족합니다" 안내를 바로 낼 수 있습니다.
- ⚠️ 잡 선점은 `UPDATE ... WHERE status='PENDING' RETURNING`으로 **원자적으로**. `SELECT` 후 `UPDATE`하면 워커 2개가 같은 잡을 집습니다.
- ⚠️ `error`에 스택 트레이스·모델 경로·API 키를 넣지 마세요. 프론트에 그대로 노출됩니다.

---

## 19. Storage 경로 규칙

| 버킷 | 경로 형식 | 공개 |
|---|---|---|
| `photos` | `{user_id}/{session_id}/reference.jpg`<br>`{user_id}/{session_id}/user.jpg` | private |
| **`segmentations`** 🆕 | `{user_id}/{session_id}/reference/map.png`<br>`{user_id}/{session_id}/user/map.png` | private |
| `body-parts` | `{user_id}/{session_id}/reference/{class_name}.png`<br>`{user_id}/{session_id}/user/{class_name}.png` | private (**선택적 파생 캐시**) |
| `inbody-temp` | `{user_id}/{inbody_id}_{n}.jpg` | private |

**규칙**

- ⚠️ 전체 URL은 DB에 저장하지 않습니다. 조회 시 백엔드가 signed URL 발급(만료 1시간).
- ⚠️ **`segmentations` 버킷에 CORS 설정이 필요합니다.** 프론트가 `getImageData`로 픽셀을 읽어야 하는데, CORS가 없으면 캔버스가 오염되어 읽을 수 없습니다. `photos` 버킷은 `<img>`로 표시만 하므로 CORS가 없어도 되지만, 같이 켜두는 편이 낫습니다.
- 최상위 `{user_id}/` 네임스페이스 — 유저 삭제 시 prefix 통째 삭제가 유일한 정리 수단입니다. FK CASCADE는 Storage를 지우지 않습니다.
- ⚠️ **사진 교체 시 삭제 순서**: `body-parts` 크롭 → `segmentations` 맵 → `photos` 원본 → `photo` 행 삭제(나머지 CASCADE). 행을 먼저 지우면 어느 파일을 지울지 알 수 없게 됩니다.
- ⚠️ 인바디 임시 이미지는 `OCR_INBODY`가 `DONE`이 된 직후 삭제. `FAILED`면 재처리를 위해 남깁니다.

---

## 20. 전체 관계도

```
users
 └─(CASCADE) analysis_session
      ├─(CASCADE) photo
      │      └─(CASCADE) segmentation  🆕  ← 라벨 맵 1장
      │             └─(CASCADE) body_part_segment ─┐  ← 부위별 통계 (검출된 전부)
      ├─(CASCADE) inbody ─(CASCADE) inbody_segment │   body_part (마스터, 전체 클래스)
      ├─(CASCADE) part_diagnosis ─(SET NULL)───────┴──
      ├─(CASCADE) overall_diagnosis
      ├─(CASCADE) month_routine
      │      ├─(CASCADE) day_routine ─(CASCADE) day_routine_exercise
      │      └─(CASCADE) routine_revision
      ├─(CASCADE) workout_log
      └─(CASCADE) job
```

---

## 21. 아직 안 정해진 것

| # | 항목 | 영향 | 상태 |
|---|---|---|---|
| 1 | `users`에 들어갈 컬럼 | `users` 전체 | 미정 |
| 2 | **Sapiens2 실제 클래스명·개수** | `body_part` seed 전체, `label_map` | **미확인. 최우선** |
| 3 | WIM 3D 결과지 구조 | `inbody` 컬럼 | 실물 샘플 필요 |
| 4 | 인바디 기종별 인쇄 항목 | `inbody` NULL 여부 | 샘플 5~10장 후 확정 |
| 5 | 유사도 점수 산출 방식 | `overall_diagnosis.score_source` | 미정 |
| 6 | 루틴 진행 기준 (날짜/횟수) | `month_routine.start_date` | 미정 (권장: 수행 횟수) |
| 7 | 루틴 생성 분할 | `month_routine.status` 전환 | 미정 |
| 8 | **VLM 입력 형식 (크롭 / 원본+하이라이트)** | `crop_path` 생성 여부, `vlm_input_type` | 미정 — **맵이 있으므로 나중에 바꿔도 재추론 불필요** |
| 9 | 3방향 촬영 | `photo.kind` 값 집합 | 확인 필요 |
| 10 | 레퍼런스 프리셋 | `reference_source` | 미정 |
| 11 | 시연 후 데이터 삭제 정책 | 운영 | 미정 |
| 12 | 🆕 **맵 저장 해상도** | `map_width/height`, 전송량 | 미정 (권장: 긴 변 1024px 고정) |

**튜닝 대상 잠정값** (코드에 박지 말고 `config.py` + `.env`)

`TOL` 40° · `F_MIN` 0.80 · `THRESHOLD` 0.90 · `N_HOLD` 15프레임 · `MIN_PIXELS` 1,500px · `MIN_RATIO` 0.5% · 인바디 항등식 허용 오차 ±3~5% · 🆕 `MAP_MAX_SIDE` 1024px

---

## 22. 스키마 운영 규칙

- 스키마는 Supabase 콘솔에서 관리하고, 변경 후 **같은 날 `db/schema.sql` 커밋 + 팀원 공지.**
- 전 테이블 RLS 활성화, 정책 생성하지 않음 — `service_role` 키만 통과.
- 환경변수: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, LLM/VLM API 키. `.env`로만 관리, 커밋 금지.
- ⚠️ `service_role` 키는 RLS를 전부 우회합니다. 절대 프론트에 노출 금지.

### v3 → v4 마이그레이션 (아직 데이터가 없으면 그냥 새로 만드세요)

```sql
-- 1) 마스터 확장
ALTER TABLE body_part ADD COLUMN is_comparable BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE body_part ADD COLUMN color_hex CHAR(7);
ALTER TABLE body_part ADD COLUMN display_order SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE body_part DROP CONSTRAINT body_part_part_group_check;
ALTER TABLE body_part ADD CONSTRAINT body_part_part_group_check
  CHECK (part_group IN ('UPPER','CORE','LOWER','OTHER'));
UPDATE body_part SET is_comparable = true;   -- 기존 9개는 전부 비교 대상
-- 나머지 클래스 INSERT는 scripts/seed_body_parts.py 로

-- 2) segmentation 신설 (본문 §6)

-- 3) body_part_segment 재구성
--    ⚠️ 기존 데이터가 있으면 photo_id → segmentation_id 로 옮기는 백필이 필요합니다.
--       맵 파일이 없던 시절 데이터라 재추론이 더 빠릅니다. 그냥 지우고 다시 돌리세요.

-- 4) photo 컬럼 개명
ALTER TABLE photo RENAME COLUMN person_area_ratio TO pose_person_area_ratio;

-- 5) part_diagnosis
ALTER TABLE part_diagnosis ADD COLUMN vlm_input_type VARCHAR(20)
  NOT NULL DEFAULT 'CROP' CHECK (vlm_input_type IN ('CROP','HIGHLIGHT'));
```
