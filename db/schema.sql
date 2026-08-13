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
    pose_person_area_ratio  REAL          CHECK (pose_person_area_ratio BETWEEN 0 AND 1),
    multi_person            BOOLEAN       NOT NULL DEFAULT false,
    was_mirrored            BOOLEAN       NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT photo_session_kind_uniq UNIQUE (session_id, kind)
);

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
    is_valid         BOOLEAN       NOT NULL,
    invalid_reason   VARCHAR(30)
                     CHECK (invalid_reason IN ('TOO_SMALL', 'TOO_SMALL_RATIO',
                                               'TRUNCATED', 'NOT_COMPARABLE')),
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
    score_source          VARCHAR(20)  NOT NULL DEFAULT 'VLM'
                          CHECK (score_source IN ('VLM', 'RULE')),
    score_rationale       TEXT,
    summary               TEXT,
    priority_parts        JSONB,
    strengths             JSONB,
    cautions              JSONB,
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
-- 12. day_routine — Day 1~28 (휴식일 포함 항상 28행)
-- ============================================================================
CREATE TABLE day_routine (
    day_routine_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    month_routine_id        UUID          NOT NULL
                            REFERENCES month_routine(month_routine_id) ON DELETE CASCADE,
    day_number              SMALLINT      NOT NULL CHECK (day_number BETWEEN 1 AND 28),
    week_number             SMALLINT      GENERATED ALWAYS AS
                            (((day_number - 1) / 7 + 1)::smallint) STORED,
    is_rest                 BOOLEAN       NOT NULL DEFAULT false,
    title                   VARCHAR(100),
    estimated_duration_min  SMALLINT      CHECK (estimated_duration_min > 0),

    CONSTRAINT day_routine_uniq UNIQUE (month_routine_id, day_number)
);

COMMENT ON COLUMN day_routine.week_number IS
    'day_number에서 파생되는 생성 컬럼. INSERT에 넣지 말 것.';


-- ============================================================================
-- 13. day_routine_exercise
-- ============================================================================
CREATE TABLE day_routine_exercise (
    day_routine_exercise_id  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    day_routine_id           UUID          NOT NULL
                             REFERENCES day_routine(day_routine_id) ON DELETE CASCADE,
    order_index              SMALLINT      NOT NULL CHECK (order_index > 0),
    name                     VARCHAR(100)  NOT NULL,
    equipment                VARCHAR(50),
    target_muscle            VARCHAR(50),
    sets                     SMALLINT      NOT NULL CHECK (sets > 0),
    reps                     SMALLINT      CHECK (reps > 0),
    weight_kg                NUMERIC(5,1)  CHECK (weight_kg >= 0),
    rest_sec                 SMALLINT      CHECK (rest_sec >= 0),
    note                     TEXT,

    CONSTRAINT day_routine_exercise_uniq UNIQUE (day_routine_id, order_index)
);

COMMENT ON COLUMN day_routine_exercise.target_muscle IS
    '근육명(삼각근 등). body_part FK 아님 — 세그멘테이션 부위와 단위가 다름.';
COMMENT ON COLUMN day_routine_exercise.reps IS
    '시간 기반 종목(플랭크 등)은 NULL이고 note에 기록.';


-- ============================================================================
-- 14. workout_log — 루틴 버전과 무관하게 살아남는 수행 기록
-- ============================================================================
CREATE TABLE workout_log (
    workout_log_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID         NOT NULL
                      REFERENCES analysis_session(session_id) ON DELETE CASCADE,
    day_number        SMALLINT     NOT NULL CHECK (day_number BETWEEN 1 AND 28),
    month_routine_id  UUID         NOT NULL
                      REFERENCES month_routine(month_routine_id) ON DELETE CASCADE,
    completed_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    feedback_text     TEXT,

    CONSTRAINT workout_log_uniq UNIQUE (session_id, day_number)
);

COMMENT ON TABLE workout_log IS
    'day_routine_id가 아니라 (session_id, day_number)에 매단 이유 — 루틴 버전이 바뀌어도 기록이 흩어지지 않게.';


-- ============================================================================
-- 15. routine_revision — 피드백 기반 패치 이력 (제일 마지막)
-- ============================================================================
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

CREATE INDEX routine_revision_routine_idx ON routine_revision (month_routine_id);

COMMENT ON TABLE routine_revision IS
    'feedback_text는 여기 중복 저장하지 않음. 원본은 workout_log.feedback_text, 조회 시 조인.';


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

COMMENT ON COLUMN job.error IS
    '사용자에게 그대로 노출해도 되는 문구만. 스택 트레이스·모델 경로·API 키 금지.';


-- ============================================================================
-- RLS — 전 테이블 활성화, 정책은 생성하지 않음
--
-- 정책이 없으면 publishable(anon) 키로는 아무것도 읽히지 않고,
-- secret(service_role) 키만 통과한다. 키가 새어도 한 겹 막힌다.
-- ============================================================================
ALTER TABLE body_part            ENABLE ROW LEVEL SECURITY;
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
ALTER TABLE day_routine          ENABLE ROW LEVEL SECURITY;
ALTER TABLE day_routine_exercise ENABLE ROW LEVEL SECURITY;
ALTER TABLE workout_log          ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_revision     ENABLE ROW LEVEL SECURITY;
ALTER TABLE job                  ENABLE ROW LEVEL SECURITY;


-- ============================================================================
-- body_part seed — Sapiens2 body-part segmentation 29개 클래스
--
-- 출처: Sapiens(1세대) goliath 28클래스 + Eyeglasses = 29
--   https://github.com/facebookresearch/sapiens/blob/main/docs/SEG_README.md
--   https://huggingface.co/facebook/sapiens2
--
-- ⚠️ 클래스 "이름"은 위 출처로 확인했지만, **픽셀 값(인덱스)은 확인하지 않았습니다.**
--    Eyeglasses가 어디에 삽입되며 나머지가 밀리는지 알 수 없기 때문입니다.
--    → 인덱스는 이 테이블에 저장하지 않습니다. 추론 시점의 매핑을
--      segmentation.label_map 에 행마다 박제하는 설계라 문제가 되지 않습니다.
--
-- ⚠️ Eyeglasses 만은 정확한 철자를 원문에서 확인하지 못했습니다.
--    첫 추론 후 label_map을 마스터와 대조해 불일치가 나오면 이 행만 고치면 됩니다.
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
    ('Eyeglasses',     '안경',      'OTHER', false, 104),
    ('Upper_Lip',      '윗입술',    'OTHER', false, 105),
    ('Lower_Lip',      '아랫입술',  'OTHER', false, 106),
    ('Upper_Teeth',    '윗니',      'OTHER', false, 107),
    ('Lower_Teeth',    '아랫니',    'OTHER', false, 108),
    ('Tongue',         '혀',        'OTHER', false, 109);
