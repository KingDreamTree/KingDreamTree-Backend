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

BEGIN;

-- ── 1. 삭제 (FK 역순) ────────────────────────────────────────────────────────
DROP TABLE IF EXISTS routine_revision;
DROP TABLE IF EXISTS workout_log;
DROP TABLE IF EXISTS day_routine_exercise;
DROP TABLE IF EXISTS day_routine;


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
ALTER TABLE routine_day          ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_day_exercise ENABLE ROW LEVEL SECURITY;
ALTER TABLE workout_log          ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_revision     ENABLE ROW LEVEL SECURITY;

COMMIT;
