# 루틴 스키마 변경안 — ✅ A 합의 완료 (2026-08-14)

> **작성**: B파트 · 2026-08-14 · **배경**: docs/routine-logic-decision.md §Q8
> **상태**: A 합의 완료 → 마이그레이션 파일로 반영됨
>   `db/migrations/2026-08-14_exercise_catalog.sql` (먼저 실행)
>   `db/migrations/2026-08-14_routine_cycle_model.sql`
> **요지**: 루틴 단위가 "Day 1~28"에서 **"주기당 N일 × 4주기 반복"**으로 확정됨.
> `day_routine`(1~28행, week_number)·요일 개념을 폐기하고 N행 구조로 바꾼다.

---

## 1. 왜 바꾸나

| 기존 (Day 1~28) | 확정된 모델 (주기당 N일) |
|---|---|
| 루틴 1개 = 28행 (휴식일 포함) | 루틴 1개 = **N행** (N = 사용자가 고른 1~7, 휴식일 행 없음) |
| week_number 생성 컬럼으로 주차 토글 | 4주기 반복은 **조회 규칙** — 데이터 복제 없음 |
| 오늘의 Day = COUNT+1 (1~28 선형) | 오늘의 Day = **(완료수 mod N)+1**, 주기 = (완료수 div N)+1 |
| 요일/순서가 데이터에 고정 | 언제 수행할지는 사용자 자유. 회복 간격은 Day 순서 배치로 보장 |

부수 이득: 28행 → N행이라 저장·생성 로직이 줄고, LLM 응답 검증도 N일치만 보면 됨.

## 2. 변경 DDL 초안

```sql
-- month_routine: 유지 (이름은 "4주기 프로그램"의 의미로 존치)
--   exercise_days_per_week 가 곧 N. 변경 없음.

-- day_routine → routine_day 로 교체
CREATE TABLE routine_day (
    routine_day_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    month_routine_id        UUID          NOT NULL
                            REFERENCES month_routine(month_routine_id) ON DELETE CASCADE,
    day_order               SMALLINT      NOT NULL CHECK (day_order BETWEEN 1 AND 7),
                            -- ⚠️ 주기 내 순서. 요일 아님. 휴식일 행 없음.
    title                   VARCHAR(100),          -- "전신 A", "상체" ...
    estimated_duration_min  SMALLINT      CHECK (estimated_duration_min > 0),

    CONSTRAINT routine_day_uniq UNIQUE (month_routine_id, day_order)
);

-- day_routine_exercise → routine_day_exercise 로 교체
CREATE TABLE routine_day_exercise (
    routine_day_exercise_id  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_day_id           UUID          NOT NULL
                             REFERENCES routine_day(routine_day_id) ON DELETE CASCADE,
    order_index              SMALLINT      NOT NULL CHECK (order_index > 0),

    exercise_ref             VARCHAR(40),           -- ExerciseDB exerciseId (환각 차단의 근거)
    name                     VARCHAR(100)  NOT NULL, -- 한글화된 이름 (캐시에서)
    image_url                VARCHAR(500),           -- ExerciseDB imageUrl

    exercise_kind            VARCHAR(10)   NOT NULL DEFAULT 'STRENGTH'
                             CHECK (exercise_kind IN ('STRENGTH', 'CARDIO')),
    sets                     SMALLINT      CHECK (sets > 0),        -- CARDIO 면 NULL
    reps                     SMALLINT      CHECK (reps > 0),
    duration_min             SMALLINT      CHECK (duration_min > 0), -- CARDIO 용
    rest_sec                 SMALLINT      CHECK (rest_sec >= 0),
    rir                      SMALLINT      CHECK (rir BETWEEN 0 AND 5),
                             -- "N회 남기고 멈추는 무게" 안내값. weight_kg 대체
    note                     TEXT,

    CONSTRAINT routine_day_exercise_uniq UNIQUE (routine_day_id, order_index)
);
-- weight_kg 컬럼 제거: 사진·인바디로 kg 추정 불가 → RIR 처방으로 확정 (D9)

-- workout_log: day_number(1~28) → routine_day 참조 + 주기 번호
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

    -- 같은 주기의 같은 Day 를 두 번 완료 처리하지 않음
    CONSTRAINT workout_log_uniq UNIQUE (month_routine_id, cycle_no, routine_day_id)
);

-- 신규: ExerciseDB 로컬 캐시 (배치 1회 수집, 시연 중 외부 API 무의존)
CREATE TABLE exercise_catalog (
    exercise_ref      VARCHAR(40)   PRIMARY KEY,   -- ExerciseDB exerciseId
    name_en           VARCHAR(200)  NOT NULL,
    name_ko           VARCHAR(200),                -- 배치 시 1회 번역
    body_parts        JSONB         NOT NULL,      -- ["UPPER ARMS", ...]
    equipments        JSONB         NOT NULL,
    exercise_type     VARCHAR(20)   NOT NULL,      -- STRENGTH | CARDIO | ...
    target_muscles    JSONB         NOT NULL,
    secondary_muscles JSONB,
    keywords          JSONB,
    image_url         VARCHAR(500),
    is_beginner_safe  BOOLEAN       NOT NULL DEFAULT true,  -- D8 제외 목록 반영
    fetched_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
```

## 3. 조회 규칙 (API 형태 변경)

```
진행 상태 (N=3 예시, 완료 4회):
  완료수 4 → 주기 = 4÷3+1 = 2주기,  오늘의 Day = 4 mod 3 + 1 = Day 2
  전체 진행률 = 4 / (4주기 × 3일) = 33%

GET .../routines/active   → { days: [Day1..N], cycle_no, total_cycles: 4, progress }
GET .../routines/today    → 이번 주기의 다음 미완료 Day (완료수 기반, 요일 무관)
GET .../routines/{id}/days/{day_order}   ← day_number(1~28) 대신 day_order(1~N)
```

- `day_source: "COUNT"` 필드는 유지하되 의미가 "주기 내 순서 계산"으로 바뀜
- 주차 토글 UI → **주기(1~4) 토글**로 대체. 모든 주기의 내용이 같으므로
  프론트는 Day 1..N 목록 하나만 그리면 됨

## 4. 지켜지는 기존 원칙

- `month_routine` 행 삭제 금지 · `is_active=false`로만 내림 (workout_log CASCADE 보호) — 유지
- 새 버전 `DONE` 후에만 `is_active` 전환 — 유지
- 운동 일수 조정 = 새 버전 (`DAYS_CHANGED`) — 유지, 오히려 단순해짐 (N행만 재생성)
- 루틴 패치(F12)는 변경분만 — 유지. 패치 대상이 28행이 아니라 N행이라 diff 도 작아짐

## 5. A 합의 결과 (2026-08-14)

| 질문 | 결론 | 반영 |
|---|---|---|
| rename+alter vs drop+create | **drop+create** — 루틴 계열 전 테이블 0행 확인됨 | 마이그레이션 |
| 카탈로그를 테이블 vs 파일 | **테이블** — 워커·API 가 다른 기계라 파일이면 동기화 문제 | `exercise_catalog.sql` |
| RLS 정책 | **정책 없음이 곧 정책** — `ENABLE` 만, `CREATE POLICY` 0개. service_role 전용이 의도된 설계 | 두 파일 모두 |

**A 요청으로 추가된 것**

1. **DROP 을 FK 역순으로** — 초안에서 `routine_revision → workout_log` 의존성이 빠져 있었다.
   순서: `routine_revision` → `workout_log` → `routine_day_exercise` → `routine_day`
2. **BEGIN/COMMIT 으로 묶기** — 중간 실패 시 반쪽 스키마가 남으면 되돌릴 수 없다
3. **`db/migrations/` 에 파일로** — `schema.sql` 은 전체 생성 스크립트라 기존 DB 에 못 쓴다
4. **seed 스크립트** — `scripts/seed_exercise_catalog.py`
5. **워커 preflight** — 카탈로그가 비면 기동 거부 (`app/worker/handlers/routine.py`).
   `body_part` 가 비었는데 워커가 떠서 "비교 부위 0"이 조용히 나오던 사고의 재발 방지.
   ⚠️ 루틴 잡을 실제로 처리하는 워커에서만 검사한다 — 조건 없이 걸면 VLM 전용
   워커까지 카탈로그 때문에 못 뜬다

### 🔴 A 지적 반영 — `is_beginner_safe` 기본값 뒤집음

초안의 `NOT NULL DEFAULT true` 는 **"아직 검토 안 함"이 "초보자에게 안전함"으로
들어가는 구조**였다. 새로 수집한 운동이 자동으로 후보에 올라가고 LLM 이 그걸 근거로
초보자에게 처방하게 된다. `clothing_pixel_count` 를 nullable 로 둔 것과 같은 판단이다.

→ **`NOT NULL DEFAULT false`.** seed 스크립트가 스크리닝 결과를 명시적으로 넣고,
안 넣으면 후보가 0건이라 바로 티가 난다. 조용히 위험한 운동이 나가는 것보다 낫다.

### A 지적 — DB 가 못 막는 것 2개 (애플리케이션 검증 대상)

| 항목 | 왜 DB 가 못 막나 | 대응 |
|---|---|---|
| `workout_log.session_id` ↔ `month_routine.session_id` | 비정규화라 서로 어긋나게 넣어도 통과 | 기록 시 일치 검증. 컬럼 COMMENT 에 명시 |
| `routine_day` 행 수 ↔ `month_routine.exercise_days_per_week` | N=3 인데 4행이 들어가도 통과 | 저장 시 검증. `day_order` COMMENT 에 명시 |

복합 FK·트리거까지 갈 위험은 아니라는 데 동의. 저장 계층에서 검증하고 주석으로 남겼다.
```
