-- ============================================================================
-- KingDreamTree Backend — DB 스키마 v4
--
-- 기준 문서 : docs/db-design-v4.md
-- 대상      : Supabase (PostgreSQL 15+)
-- 테이블 수 : 16
--
-- 실행 방법 : Supabase 대시보드 → SQL Editor → 전체 붙여넣기 → Run
--
-- 메모
--   * gen_random_uuid() 는 PG13+ 기본 내장 → CREATE EXTENSION 불필요
--   * 모든 상태값은 대문자. 소문자 섞이면 CHECK 위반으로 INSERT 실패
--   * FK 때문에 CREATE 순서가 정해져 있음 (body_part 먼저, routine_revision 마지막)
--   * 전 테이블 RLS 활성화, 정책은 생성하지 않음 → secret 키만 통과
-- ============================================================================


-- ============================================================================
-- 1. body_part — 부위 마스터 (아무것도 참조하지 않으므로 제일 먼저)
-- ============================================================================
CREATE TABLE body_part (
    class_name      VARCHAR(40)  PRIMARY KEY,
    name_ko         VARCHAR(40)  NOT NULL,
    part_group      VARCHAR(20)  NOT NULL
                    CHECK (part_group IN ('UPPER', 'CORE', 'LOWER', 'OTHER')),
    inbody_segment  VARCHAR(20)
                    CHECK (inbody_segment IN ('LEFT_ARM', 'RIGHT_ARM', 'TRUNK',
                                              'LEFT_LEG', 'RIGHT_LEG')),
    is_comparable   BOOLEAN      NOT NULL DEFAULT false,
    color_hex       CHAR(7)      CHECK (color_hex ~ '^#[0-9A-Fa-f]{6}$'),
    display_order   SMALLINT     NOT NULL DEFAULT 0
);

COMMENT ON TABLE  body_part IS 'Sapiens2 전체 클래스 마스터. 비교 대상은 is_comparable=true 9개.';
COMMENT ON COLUMN body_part.color_hex IS '오버레이 색. NULL이면 칠하지 않음(배경·옷 등).';


-- ============================================================================
-- 1-2. exercise_catalog — ExerciseDB 운동 풀 캐시 (공용 마스터, body_part 와 같은 취급)
--   마이그레이션: db/migrations/2026-08-14_exercise_catalog.sql
-- ============================================================================
-- exercise_catalog — ExerciseDB 운동 풀 로컬 캐시
--
-- ⚠️ 이 파일을 routine_cycle_model.sql **보다 먼저** 실행할 것.
--    routine_day_exercise.exercise_ref 가 이 테이블을 참조한다.
--
-- 왜 DB 테이블인가 (파일이 아니라)
--   API 프로세스와 워커가 다른 기계에서 돈다. 파일이면 양쪽에 배포·동기화해야 하고
--   한쪽만 갱신되면 조용히 어긋난다. body_part 와 같은 취급이다 — 공용 마스터.
--
-- 왜 캐시인가 (매번 API 호출이 아니라)
--   사용자 요청마다 외부 API 를 부르면 비용·rate limit·응답 속도가 전부 외부 종속이
--   된다. 무엇보다 **시연 중 RapidAPI 장애가 우리 장애가 된다.**
--   운동 목록은 사실상 정적 데이터라 배치 1회로 충분하다 (fetched_at 만 남긴다).
--
-- seed: python scripts/seed_exercise_catalog.py
--       (원본 수집은 scripts/fetch_exercisedb.py fetch)


CREATE TABLE IF NOT EXISTS exercise_catalog (
    exercise_ref       VARCHAR(40)   PRIMARY KEY,   -- ExerciseDB exerciseId
    name_en            VARCHAR(200)  NOT NULL,
    name_ko            VARCHAR(200),                -- 배치 번역 (localize_exercises.py)

    body_parts         JSONB         NOT NULL,      -- ["UPPER ARMS", ...] 후보 필터 1차 기준
    target_muscles     JSONB         NOT NULL,      -- 주동근. 이두/삼두처럼 bodyPart 가
                                                    -- 같은 슬롯을 가르는 정렬 기준
    secondary_muscles  JSONB,                       -- 복합운동 판별용
    equipments         JSONB         NOT NULL,
    exercise_type      VARCHAR(20)   NOT NULL,      -- STRENGTH | CARDIO | ...
    keywords           JSONB,
    image_url          VARCHAR(500),

    -- ⚠️ **DEFAULT false 다.** 초안의 DEFAULT true 는 "아직 검토 안 함"이
    --    "초보자에게 안전함"으로 들어가는 구조였다. 새로 수집한 운동이 자동으로
    --    후보에 올라가고 LLM 이 그걸 근거로 초보자에게 처방하게 된다.
    --    clothing_pixel_count 를 nullable 로 둔 것과 같은 판단이다 —
    --    "확인함"과 "안 봄"을 구분되게 만든다.
    --    seed 스크립트가 스크리닝 결과를 명시적으로 넣는다. 안 넣으면 후보가
    --    0건이라 바로 티가 난다. 조용히 위험한 운동이 나가는 것보다 낫다.
    is_beginner_safe   BOOLEAN       NOT NULL DEFAULT false,

    fetched_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE exercise_catalog IS
    'ExerciseDB 운동 풀 캐시. LLM 은 이 안의 exercise_ref 중에서만 고른다 — '
    '후보 밖 운동을 낼 수 없으므로 환각이 구조적으로 차단된다.';

COMMENT ON COLUMN exercise_catalog.is_beginner_safe IS
    '초보자에게 처방해도 되는지. ⚠️ DEFAULT false — 검토되지 않은 행이 안전한 것으로 '
    '취급되면 안 된다. seed 스크립트가 스크리닝 결과를 명시적으로 넣는다.';

-- 슬롯 후보 필터가 매번 도는 경로다 (type + 안전 여부로 먼저 좁힌다)
CREATE INDEX IF NOT EXISTS exercise_catalog_type_safe_idx
    ON exercise_catalog (exercise_type, is_beginner_safe);

-- RLS: ENABLE 만. policy 없음 = service_role 전용 (body_part 와 동일)


-- ============================================================================
-- 2. users
-- ============================================================================
CREATE TABLE users (
    user_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    is_pro_user  BOOLEAN      NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE users IS '로그인 없음. user_id를 X-User-Id 헤더로 받아 식별한다.';


-- ============================================================================
-- 3. analysis_session — 모든 소유권 검증의 기준점
-- ============================================================================
CREATE TABLE analysis_session (
    session_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID         NOT NULL
                       REFERENCES users(user_id) ON DELETE CASCADE,
    reference_source   VARCHAR(20)  NOT NULL DEFAULT 'USER_UPLOAD'
                       CHECK (reference_source IN ('USER_UPLOAD', 'PRESET')),
    contraindications  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status             VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 사용자당 진행 중 세션 1개 (부분 인덱스라 테이블 제약으로는 표현 불가)
CREATE UNIQUE INDEX analysis_session_one_active_idx
    ON analysis_session (user_id) WHERE status = 'ACTIVE';

CREATE INDEX analysis_session_user_created_idx
    ON analysis_session (user_id, created_at DESC);


-- ============================================================================
-- 4. photo
-- ============================================================================
CREATE TABLE photo (
    photo_id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID          NOT NULL
                            REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    kind                    VARCHAR(20)   NOT NULL
                            CHECK (kind IN ('REFERENCE', 'USER')),
    storage_bucket          VARCHAR(63)   NOT NULL DEFAULT 'photos',
    storage_path            VARCHAR(500)  NOT NULL,
    width                   INT           CHECK (width > 0),
    height                  INT           CHECK (height > 0),
    capture_source          VARCHAR(20)
                            CHECK (capture_source IN ('CAPTURE', 'UPLOAD')),
    pose_landmarks          JSONB,
    pose_scale_basis        VARCHAR(20)
                            CHECK (pose_scale_basis IN ('TORSO', 'HIP_KNEE')),
    pose_similarity         NUMERIC(5,2)  CHECK (pose_similarity BETWEEN 0 AND 100),
    framing_score           NUMERIC(4,3)  CHECK (framing_score BETWEEN 0 AND 1),
    facing_delta            NUMERIC(6,3)  CHECK (facing_delta >= 0),
    pose_oks                NUMERIC(4,3)  CHECK (pose_oks BETWEEN 0 AND 1),
    pose_person_area_ratio  REAL          CHECK (pose_person_area_ratio BETWEEN 0 AND 1),
    multi_person            BOOLEAN       NOT NULL DEFAULT false,
    was_mirrored            BOOLEAN       NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT photo_session_kind_uniq UNIQUE (session_id, kind)
);

COMMENT ON COLUMN photo.facing_delta IS
    '레퍼런스와 몸 방향의 차이. 상한 없음 — 레퍼런스가 많이 돌아가 있으면 1을 넘는다.';
COMMENT ON COLUMN photo.pose_oks IS
    'OKS-inspired 유사도. 판정에 쓰지 않고 pose_similarity 와 비교하려고 모으는 중.';
COMMENT ON COLUMN photo.pose_person_area_ratio IS
    'MediaPipe 기준 추정치(프레이밍 판정용). 정확한 인물 면적은 segmentation.person_pixel_count.';
COMMENT ON COLUMN photo.pose_landmarks IS
    '반전되지 않은 카메라 원본 기준. 미러링은 프론트 CSS만.';
COMMENT ON COLUMN photo.was_mirrored IS
    '거울 촬영으로 접수돼 서버가 좌우를 되돌려 저장했는지. '
    '저장된 이미지·랜드마크는 항상 비반전 기준이므로 이 값은 추적용이다.';


-- ============================================================================
-- 5. segmentation — 라벨 맵 1장 (사진 1장당 1행)
-- ============================================================================
CREATE TABLE segmentation (
    segmentation_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    photo_id              UUID          NOT NULL UNIQUE
                          REFERENCES photo(photo_id) ON DELETE CASCADE,
    storage_bucket        VARCHAR(63)   NOT NULL DEFAULT 'segmentations',
    map_path              VARCHAR(500)  NOT NULL,
    map_width             INT           NOT NULL CHECK (map_width > 0),
    map_height            INT           NOT NULL CHECK (map_height > 0),
    label_map             JSONB         NOT NULL,
    model_name            VARCHAR(50)   NOT NULL,
    model_version         VARCHAR(50)   NOT NULL,
    person_pixel_count    INT           NOT NULL CHECK (person_pixel_count >= 0),
    person_area_ratio     REAL          NOT NULL CHECK (person_area_ratio BETWEEN 0 AND 1),
    detected_class_count  SMALLINT      NOT NULL CHECK (detected_class_count >= 0),
    inference_ms          INT           CHECK (inference_ms >= 0),
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE segmentation IS
    'status 컬럼 없음 — 행의 존재 = 세그멘테이션 완료. 진행/실패는 job(kind=SEG_*)이 소스.';
COMMENT ON COLUMN segmentation.label_map IS
    '추론 당시의 픽셀값→클래스명 매핑을 박제. {"1":"Torso",...}. 모델 버전이 바뀌면 값이 재배열됨.';


-- ============================================================================
-- 6. body_part_segment — 맵에서 파생된 부위별 통계 (검출된 모든 클래스)
-- ============================================================================
CREATE TABLE body_part_segment (
    segment_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    segmentation_id  UUID          NOT NULL
                     REFERENCES segmentation(segmentation_id) ON DELETE CASCADE,
    class_name       VARCHAR(40)   NOT NULL
                     REFERENCES body_part(class_name),
    label_value      SMALLINT      NOT NULL CHECK (label_value BETWEEN 0 AND 255),
    pixel_count      INT           NOT NULL CHECK (pixel_count >= 0),
    area_ratio       REAL          NOT NULL CHECK (area_ratio BETWEEN 0 AND 1),
    bbox_x           INT           NOT NULL CHECK (bbox_x >= 0),
    bbox_y           INT           NOT NULL CHECK (bbox_y >= 0),
    bbox_w           INT           NOT NULL CHECK (bbox_w > 0),
    bbox_h           INT           NOT NULL CHECK (bbox_h > 0),
    is_truncated     BOOLEAN       NOT NULL DEFAULT false,
    -- pixel_count 중 옷에서 흡수된 픽셀 수 (app/services/part_merge.py).
    -- ⚠️ NULL = 병합 미적용(구버전 행 또는 SEG_MERGE_CLOTHING=false)
    --    0    = 병합 적용했고 흡수분이 없음
    --    이 구분을 위해 NOT NULL DEFAULT 0 으로 만들지 말 것. 두 경우를 합치면
    --    "옷을 안 입은 것"과 "병합을 안 돌린 것"을 구분할 수 없다.
    -- ⚠️ 이 값이 크면 그 부위는 맨살이 아니라 **옷 위 실루엣**을 잰 것이다.
    --    진단 프롬프트가 이 비율을 보고 근육 윤곽 근거로 쓸지 말지 정한다.
    clothing_pixel_count INT       CHECK (clothing_pixel_count >= 0
                                      AND clothing_pixel_count <= pixel_count),
    is_valid         BOOLEAN       NOT NULL,
    invalid_reason   VARCHAR(30)
                     CHECK (invalid_reason IN ('TOO_SMALL', 'TOO_SMALL_RATIO',
                                               'TRUNCATED', 'NOT_COMPARABLE',
                                               'MOSTLY_CLOTHING')),
    crop_bucket      VARCHAR(63),
    crop_path        VARCHAR(500),
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT body_part_segment_uniq UNIQUE (segmentation_id, class_name)
);

CREATE INDEX body_part_segment_valid_idx
    ON body_part_segment (segmentation_id) WHERE is_valid;

COMMENT ON COLUMN body_part_segment.label_value IS
    '이 맵에서의 픽셀 값. ⚠️ 조인 키로 쓰지 말 것 — 조인은 항상 class_name.';
COMMENT ON COLUMN body_part_segment.bbox_x IS
    '맵 좌표계 기준. 원본 위에 그리려면 photo.width/segmentation.map_width 배율로 스케일.';
COMMENT ON COLUMN body_part_segment.crop_path IS
    'VLM 입력용 파생 캐시. 맵에서 언제든 재생성 가능하므로 NULL 허용.';


-- ============================================================================
-- 7. inbody
-- ============================================================================
CREATE TABLE inbody (
    inbody_id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            UUID          NOT NULL
                          REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    device_type           VARCHAR(30),
    measured_at           DATE,
    age                   INT           CHECK (age BETWEEN 1 AND 120),
    gender                VARCHAR(10)   CHECK (gender IN ('MALE', 'FEMALE')),
    height                NUMERIC(5,1)  CHECK (height BETWEEN 120 AND 220),
    weight                NUMERIC(5,1)  CHECK (weight BETWEEN 25 AND 250),
    bmi                   NUMERIC(4,1)  CHECK (bmi BETWEEN 10 AND 60),
    body_fat_mass         NUMERIC(5,1)  CHECK (body_fat_mass BETWEEN 0 AND 150),
    body_fat_percentage   NUMERIC(4,1)  CHECK (body_fat_percentage BETWEEN 1 AND 70),
    skeletal_muscle_mass  NUMERIC(5,1)  CHECK (skeletal_muscle_mass BETWEEN 10 AND 60),
    fat_free_mass         NUMERIC(5,1)  CHECK (fat_free_mass BETWEEN 10 AND 150),
    bmr_kcal              INT           CHECK (bmr_kcal BETWEEN 500 AND 5000),
    raw_ocr               JSONB,
    validation            JSONB,
    status                VARCHAR(20)   NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    validation_error      TEXT,
    verified_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX inbody_session_measured_idx
    ON inbody (session_id, measured_at DESC NULLS LAST);

COMMENT ON TABLE inbody IS
    '결과지 이미지는 저장하지 않음. 임시 경로는 job.payload에. 항등식 검증은 애플리케이션에서 → validation.';


-- ============================================================================
-- 8. inbody_segment — 부위별 근육/체지방 (5부위)
-- ============================================================================
CREATE TABLE inbody_segment (
    inbody_segment_id  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    inbody_id          UUID          NOT NULL
                       REFERENCES inbody(inbody_id) ON DELETE CASCADE,
    segment            VARCHAR(20)   NOT NULL
                       CHECK (segment IN ('LEFT_ARM', 'RIGHT_ARM', 'TRUNK',
                                          'LEFT_LEG', 'RIGHT_LEG')),
    lean_mass          NUMERIC(5,1)  CHECK (lean_mass >= 0),
    fat_mass           NUMERIC(5,1)  CHECK (fat_mass >= 0),

    CONSTRAINT inbody_segment_uniq UNIQUE (inbody_id, segment)
);

COMMENT ON TABLE inbody_segment IS
    '부위별 범위 검증(팔 0.5~8kg / 다리 2~20kg)은 CHECK가 아니라 애플리케이션에서 경고만.';


-- ============================================================================
-- 9. part_diagnosis — 부위별 VLM 비교 진단
-- ============================================================================
CREATE TABLE part_diagnosis (
    part_diagnosis_id     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            UUID         NOT NULL
                          REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    class_name            VARCHAR(40)  NOT NULL
                          REFERENCES body_part(class_name),
    reference_segment_id  UUID
                          REFERENCES body_part_segment(segment_id) ON DELETE SET NULL,
    user_segment_id       UUID
                          REFERENCES body_part_segment(segment_id) ON DELETE SET NULL,
    vlm_input_type        VARCHAR(20)  NOT NULL DEFAULT 'CROP'
                          CHECK (vlm_input_type IN ('CROP', 'HIGHLIGHT')),
    differences           JSONB,
    assessment            TEXT,
    gap_level             VARCHAR(20)
                          CHECK (gap_level IN ('NONE', 'SLIGHT', 'MODERATE', 'SIGNIFICANT')),
    priority              SMALLINT     CHECK (priority BETWEEN 1 AND 5),
    confidence            VARCHAR(10)
                          CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    raw_response          JSONB,
    status                VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT part_diagnosis_uniq UNIQUE (session_id, class_name)
);

COMMENT ON TABLE part_diagnosis IS
    '부위 하나가 FAILED여도 전체 중단하지 않음. 해당 행만 결과에서 제외.';


-- ============================================================================
-- 10. overall_diagnosis — 종합 진단 (세션당 1건)
-- ============================================================================
CREATE TABLE overall_diagnosis (
    overall_diagnosis_id  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            UUID         NOT NULL UNIQUE
                          REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    similarity_score      SMALLINT     CHECK (similarity_score BETWEEN 0 AND 100),
    -- ⚠️ 기본값은 RULE 이다. 점수는 코드가 규칙으로 계산하고(services/scoring.py)
    --    LLM 이 보낸 점수는 버린다. 'VLM' 은 옛 설계의 잔재로 CHECK 에만 남긴다.
    --    ⚠️ 이미 만들어진 DB 는 DEFAULT 가 'VLM' 인 채로 남아 있다 — 코드가 항상
    --       값을 명시해 쓰므로 무해하다. ALTER 는 해커톤 이후.
    score_source          VARCHAR(20)  NOT NULL DEFAULT 'RULE'
                          CHECK (score_source IN ('VLM', 'RULE')),
    score_rationale       TEXT,
    summary               TEXT,
    priority_parts        JSONB,
    strengths             JSONB,
    cautions              JSONB,
    --: F09 가 원본 사진을 직접 보고 낸 전체 형태 판단 (2026-08-17 추가).
    --  부위 카드로는 드러나지 않는 관계 — 상하체 균형, 어깨-허리 폭 비율 등.
    key_differences       JSONB,
    silhouette            TEXT,
    --: 종합 판단의 확신도. ⚠️ similarity_score 와 무관하다 — 점수는 규칙이 계산한다.
    confidence            NUMERIC(3, 2) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    raw_response          JSONB,
    status                VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);


-- ============================================================================
-- 11. month_routine — 4주 루틴 (버전 관리)
-- ============================================================================
CREATE TABLE month_routine (
    month_routine_id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID         NOT NULL
                            REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    version                 INT          NOT NULL DEFAULT 1 CHECK (version >= 1),
    exercise_days_per_week  INT          NOT NULL
                            CHECK (exercise_days_per_week BETWEEN 1 AND 7),
    goal                    TEXT,
    focus_areas             JSONB,
    start_date              DATE,
    generation_type         VARCHAR(20)  NOT NULL DEFAULT 'INITIAL'
                            CHECK (generation_type IN ('INITIAL', 'DAYS_CHANGED', 'FEEDBACK')),
    is_active               BOOLEAN      NOT NULL DEFAULT true,
    raw_response            JSONB,
    status                  VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT month_routine_version_uniq UNIQUE (session_id, version)
);

-- 세션당 활성 루틴 1개
CREATE UNIQUE INDEX month_routine_one_active_idx
    ON month_routine (session_id) WHERE is_active;

COMMENT ON TABLE month_routine IS
    '⚠️ 행을 삭제하지 말 것. is_active=false로만 내림. 삭제하면 workout_log가 CASCADE로 딸려 사라짐.';


-- ============================================================================
-- 12~15. 루틴 — 주기당 N일 × 4주 반복 모델
--   ⚠️ Day 1~28 고정 구조에서 바뀌었다. 요일 개념이 없고 휴식일 행도 없다.
--      오늘의 Day = (완료수 mod N) + 1,  주기 = (완료수 div N) + 1
--      마이그레이션: db/migrations/2026-08-14_routine_cycle_model.sql
-- ============================================================================
-- 루틴 계열 재구성 — "Day 1~28" → "주기당 N일 × 4주기 반복"
--
-- 왜 필요한가
--   루틴 단위가 확정됐다 (2026-08-14 PM). N = 사용자가 고른 주당 운동 일수(1~7).
--   Day 1..N 만 저장하고 4주기 반복은 조회 규칙으로 푼다. 요일 개념은 없다.
--   그래서 day_number(1~28) · week_number(생성 컬럼) · is_rest(휴식일 행)가
--   전부 의미를 잃었다.
--
-- ⚠️ drop+create 로 간다. 루틴 계열 전 테이블이 0행임을 확인했다 (담당 A, 2026-08-14).
--    남길 데이터가 없어 rename+alter 의 값어치가 없다.
--
-- ⚠️ DROP 은 FK 역순이다. routine_revision 이 workout_log 를 참조하므로
--    workout_log 보다 **먼저** 지워야 한다 (초안 순서에서 빠져 있던 의존성).
--      routine_revision → workout_log → day_routine_exercise → day_routine
--    month_routine 은 그대로 둔다 (exercise_days_per_week 가 곧 N).
--
-- ⚠️ 전체를 트랜잭션으로 묶는다. 중간에 실패하면 반쪽 스키마가 남는데
--    그 상태를 되돌릴 방법이 없다.
--
-- ⚠️ RLS 는 ENABLE 만 하고 policy 는 만들지 않는다. 정책이 없는 것이 곧 정책이다 —
--    service_role 로만 접근 가능한 상태가 의도된 설계다 (프론트가 Supabase 에
--    직접 붙지 않는다). body_part 와 동일한 취급.


-- ── 1. 삭제 (FK 역순) ────────────────────────────────────────────────────────


-- ── 2. routine_day — 주기 내 Day (요일 아님, 휴식일 행 없음) ────────────────
CREATE TABLE routine_day (
    routine_day_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    month_routine_id        UUID          NOT NULL
                            REFERENCES month_routine(month_routine_id) ON DELETE CASCADE,
    day_order               SMALLINT      NOT NULL CHECK (day_order BETWEEN 1 AND 7),
    title                   VARCHAR(100),
    estimated_duration_min  SMALLINT      CHECK (estimated_duration_min > 0),

    CONSTRAINT routine_day_uniq UNIQUE (month_routine_id, day_order)
);

COMMENT ON COLUMN routine_day.day_order IS
    '주기 내 순서 (1..N). **요일이 아니다** — 사용자가 언제 수행할지는 자유다. '
    '⚠️ 행 수와 month_routine.exercise_days_per_week 의 일치는 DB 가 막지 못한다. '
    '애플리케이션이 검증한다 (app/services/routine_repo.py).';


-- ── 3. routine_day_exercise ─────────────────────────────────────────────────
CREATE TABLE routine_day_exercise (
    routine_day_exercise_id  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_day_id           UUID          NOT NULL
                             REFERENCES routine_day(routine_day_id) ON DELETE CASCADE,
    order_index              SMALLINT      NOT NULL CHECK (order_index > 0),

    -- ExerciseDB 원본 참조. 이 값이 있어야 이미지·영상으로 되짚을 수 있고,
    -- "LLM 이 지어낸 운동이 아니다"를 데이터로 증명할 수 있다.
    exercise_ref             VARCHAR(40)   REFERENCES exercise_catalog(exercise_ref),
    name                     VARCHAR(100)  NOT NULL,
    image_url                VARCHAR(500),

    exercise_kind            VARCHAR(10)   NOT NULL DEFAULT 'STRENGTH'
                             CHECK (exercise_kind IN ('STRENGTH', 'CARDIO')),
    muscle_group             VARCHAR(30),
    sets                     SMALLINT      CHECK (sets > 0),
    reps                     SMALLINT      CHECK (reps > 0),
    duration_min             SMALLINT      CHECK (duration_min > 0),
    rest_sec                 SMALLINT      CHECK (rest_sec >= 0),

    -- "N회 남기고 멈추는 무게" 자가조절 처방 (Zourdos 2016).
    -- ⚠️ weight_kg 컬럼은 두지 않는다. 사진·인바디로 적정 중량을 추정할 방법이
    --    없어서 추정하지 않기로 했다 (D9). 컬럼이 있으면 언젠가 채우게 된다.
    rir                      SMALLINT      CHECK (rir BETWEEN 0 AND 5),

    -- 이 운동이 어느 진단 부위 때문에 볼륨을 더 받았는지. 화면 문구의 근거다.
    boosted_by               VARCHAR(40)   REFERENCES body_part(class_name),
    note                     TEXT,

    CONSTRAINT routine_day_exercise_uniq UNIQUE (routine_day_id, order_index),
    -- 근력이면 sets, 유산소면 duration_min 이 있어야 한다
    CONSTRAINT routine_day_exercise_kind_chk CHECK (
        (exercise_kind = 'STRENGTH' AND sets IS NOT NULL)
        OR (exercise_kind = 'CARDIO' AND duration_min IS NOT NULL)
    )
);


-- ── 4. workout_log — 주기 번호가 붙는다 ─────────────────────────────────────
CREATE TABLE workout_log (
    workout_log_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID         NOT NULL
                      REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    month_routine_id  UUID         NOT NULL
                      REFERENCES month_routine(month_routine_id) ON DELETE CASCADE,
    routine_day_id    UUID         NOT NULL
                      REFERENCES routine_day(routine_day_id) ON DELETE CASCADE,
    cycle_no          SMALLINT     NOT NULL CHECK (cycle_no BETWEEN 1 AND 4),
    completed_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    feedback_text     TEXT,

    -- 같은 주기의 같은 Day 를 두 번 완료 처리하지 않는다
    CONSTRAINT workout_log_uniq UNIQUE (month_routine_id, cycle_no, routine_day_id)
);

COMMENT ON COLUMN workout_log.session_id IS
    '⚠️ month_routine 을 통해 유도 가능한 값이다. 소유권 검증 조인을 줄이려고 '
    '비정규화해 두었으므로, 서로 어긋나게 들어가는 것을 DB 가 막지 못한다. '
    '기록 시 month_routine.session_id 와 일치하는지 애플리케이션이 검증한다.';

CREATE INDEX workout_log_routine_idx ON workout_log (month_routine_id, cycle_no);


-- ── 5. routine_revision — 구조 동일, workout_log 재생성 때문에 같이 만든다 ──
CREATE TABLE routine_revision (
    routine_revision_id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    month_routine_id           UUID         NOT NULL
                               REFERENCES month_routine(month_routine_id) ON DELETE CASCADE,
    previous_month_routine_id  UUID
                               REFERENCES month_routine(month_routine_id) ON DELETE SET NULL,
    source_log_id              UUID
                               REFERENCES workout_log(workout_log_id) ON DELETE SET NULL,
    interpretation             TEXT,
    changes                    JSONB,
    contraindications_added    JSONB,
    raw_response               JSONB,
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT now()
);


-- ── 6. RLS — ENABLE 만. policy 는 만들지 않는다 (위 주석 참고) ──────────────


-- ============================================================================
-- 16. job — 모든 백그라운드 작업의 큐 + 상태
-- ============================================================================
CREATE TABLE job (
    job_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID         NOT NULL
                 REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    kind         VARCHAR(30)  NOT NULL
                 CHECK (kind IN ('SEG_REFERENCE', 'SEG_USER', 'OCR_INBODY',
                                 'VLM_PART', 'VLM_OVERALL',
                                 'ROUTINE_GEN', 'ROUTINE_PATCH')),
    status       VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                 CHECK (status IN ('PENDING', 'PROCESSING', 'DONE', 'FAILED')),
    payload      JSONB,
    result       JSONB,
    error        TEXT,
    attempts     INT          NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX job_poll_idx    ON job (status, kind, created_at);
CREATE INDEX job_session_idx ON job (session_id);

-- ⚠️ #110 — 세션당 kind별로 "열린"(PENDING/PROCESSING) 잡은 최대 1개.
--    라우트의 find_open→enqueue 가드는 read-then-write라 동시 요청(더블클릭,
--    React StrictMode 이중실행)이면 TOCTOU 창에서 둘 다 통과할 수 있었다.
--    이게 마지막 방어선 — 진 쪽 INSERT는 23505로 튕기고, app/worker/queue.py
--    의 enqueue()가 그걸 잡아 기존 잡을 그대로 돌려준다.
CREATE UNIQUE INDEX job_open_one_per_kind_idx
    ON job (session_id, kind) WHERE status IN ('PENDING', 'PROCESSING');

COMMENT ON COLUMN job.error IS
    '사용자에게 그대로 노출해도 되는 문구만. 스택 트레이스·모델 경로·API 키 금지.';


-- ============================================================================
-- RLS — 전 테이블 활성화, 정책은 생성하지 않음
--
-- 정책이 없으면 publishable(anon) 키로는 아무것도 읽히지 않고,
-- secret(service_role) 키만 통과한다. 키가 새어도 한 겹 막힌다.
-- ============================================================================
ALTER TABLE body_part            ENABLE ROW LEVEL SECURITY;
ALTER TABLE exercise_catalog     ENABLE ROW LEVEL SECURITY;
ALTER TABLE users                ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_session     ENABLE ROW LEVEL SECURITY;
ALTER TABLE photo                ENABLE ROW LEVEL SECURITY;
ALTER TABLE segmentation         ENABLE ROW LEVEL SECURITY;
ALTER TABLE body_part_segment    ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbody               ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbody_segment       ENABLE ROW LEVEL SECURITY;
ALTER TABLE part_diagnosis       ENABLE ROW LEVEL SECURITY;
ALTER TABLE overall_diagnosis    ENABLE ROW LEVEL SECURITY;
ALTER TABLE month_routine        ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_day          ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_day_exercise ENABLE ROW LEVEL SECURITY;
ALTER TABLE workout_log          ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_revision     ENABLE ROW LEVEL SECURITY;
ALTER TABLE job                  ENABLE ROW LEVEL SECURITY;


-- ============================================================================
-- body_part seed — Sapiens2 body-part segmentation 29개 클래스
--
-- 출처: https://github.com/facebookresearch/sapiens2/blob/main/docs/SEG.md (29클래스)
--   https://github.com/facebookresearch/sapiens/blob/main/docs/SEG_README.md
--   https://huggingface.co/facebook/sapiens2
--
-- ✅ 클래스 이름과 인덱스 모두 공식 문서(SEG.md)로 확인했습니다. 실측과도 일치합니다.
--    Eyeglass 가 2번에 삽입되어 그 뒤가 한 칸씩 밀린 배열입니다.
--
-- ⚠️ `Eyeglass` 는 **단수형**입니다. 한때 Eyeglasses 로 적어둔 적이 있는데,
--    복수형이면 워커의 label_map 대조에서 "마스터에 없는 클래스"로 걸립니다.
--
--    인덱스는 이 테이블에 저장하지 않습니다. 추론 시점의 매핑을
--    segmentation.label_map 에 행마다 박제하는 설계라, 모델이 바뀌어도
--    과거 데이터가 안전합니다.
--
-- body_part_segment가 이 테이블을 FK로 참조하므로 seed 없이는 아무 데이터도 못 넣습니다.
-- 재적용은 scripts/seed_body_parts.py (멱등) 를 쓰세요.
-- ============================================================================

-- 비교 대상 9개 (is_comparable = true) — 맨살이 드러나는 부위만
-- ⚠️ 좌/우를 비슷한 색 계열로 잡은 이유: 좌우 반전 사고를 눈으로 잡기 위해서.
--    오버레이가 좌우 대칭으로 뒤집혀 보이면 반전 규칙이 깨진 것이다.
INSERT INTO body_part (class_name, name_ko, part_group, inbody_segment,
                       is_comparable, color_hex, display_order) VALUES
    ('Torso',           '몸통',         'CORE',  'TRUNK',     true, '#4C6EF5', 1),
    ('Left_Upper_Arm',  '왼팔 상완',     'UPPER', 'LEFT_ARM',  true, '#F76707', 2),
    ('Left_Lower_Arm',  '왼팔 전완',     'UPPER', 'LEFT_ARM',  true, '#FFA94D', 3),
    ('Right_Upper_Arm', '오른팔 상완',   'UPPER', 'RIGHT_ARM', true, '#2F9E44', 4),
    ('Right_Lower_Arm', '오른팔 전완',   'UPPER', 'RIGHT_ARM', true, '#69DB7C', 5),
    ('Left_Upper_Leg',  '왼쪽 허벅지',   'LOWER', 'LEFT_LEG',  true, '#AE3EC9', 6),
    ('Left_Lower_Leg',  '왼쪽 종아리',   'LOWER', 'LEFT_LEG',  true, '#DA77F2', 7),
    ('Right_Upper_Leg', '오른쪽 허벅지', 'LOWER', 'RIGHT_LEG', true, '#E03131', 8),
    ('Right_Lower_Leg', '오른쪽 종아리', 'LOWER', 'RIGHT_LEG', true, '#FF8787', 9);

-- 비교 대상 아님 20개 — 맵에는 들어가지만 색칠하지 않는다 (color_hex NULL)
-- ⚠️ 손·발·신발·양말은 좌우가 별도 클래스다. 옷도 상/하의가 나뉜다.
--    Apparel은 상/하의로 분류되지 않는 나머지 착용물이다.
INSERT INTO body_part (class_name, name_ko, part_group, is_comparable, display_order) VALUES
    ('Background',     '배경',      'OTHER', false, 90),
    ('Apparel',        '착용물',    'OTHER', false, 91),
    ('Upper_Clothing', '상의',      'OTHER', false, 92),
    ('Lower_Clothing', '하의',      'OTHER', false, 93),
    ('Left_Shoe',      '왼쪽 신발', 'OTHER', false, 94),
    ('Right_Shoe',     '오른쪽 신발', 'OTHER', false, 95),
    ('Left_Sock',      '왼쪽 양말', 'OTHER', false, 96),
    ('Right_Sock',     '오른쪽 양말', 'OTHER', false, 97),
    ('Left_Hand',      '왼손',      'OTHER', false, 98),
    ('Right_Hand',     '오른손',    'OTHER', false, 99),
    ('Left_Foot',      '왼발',      'OTHER', false, 100),
    ('Right_Foot',     '오른발',    'OTHER', false, 101),
    ('Hair',           '머리카락',  'OTHER', false, 102),
    ('Face_Neck',      '얼굴·목',   'OTHER', false, 103),
    ('Eyeglass',       '안경',      'OTHER', false, 104),
    ('Upper_Lip',      '윗입술',    'OTHER', false, 105),
    ('Lower_Lip',      '아랫입술',  'OTHER', false, 106),
    ('Upper_Teeth',    '윗니',      'OTHER', false, 107),
    ('Lower_Teeth',    '아랫니',    'OTHER', false, 108),
    ('Tongue',         '혀',        'OTHER', false, 109);
