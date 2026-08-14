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

BEGIN;

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
ALTER TABLE exercise_catalog ENABLE ROW LEVEL SECURITY;

COMMIT;
