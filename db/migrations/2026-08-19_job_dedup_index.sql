-- job 테이블에 세션+kind당 "열린" 잡 1개 제약 (#110)
--
-- 왜 필요한가
--   POST /analysis 등의 "진행 중이면 새로 안 만든다" 가드는 find_open()
--   (SELECT) 다음에 enqueue()(INSERT)를 부르는 read-then-write다. 더블클릭이나
--   React StrictMode 이중실행이면 그 사이(TOCTOU)에 두 요청 다 find_open에서
--   "없음"을 보고 둘 다 INSERT할 수 있다 — 가장 비싼 Vision 호출이 2번 나간다.
--
-- ⚠️ 반드시 app/worker/queue.py의 enqueue() 수정과 **같이** 배포한다.
--    그 함수가 이 제약의 23505(unique_violation)를 잡아서 기존 잡을 돌려주게
--    바뀌어 있어야, 이 인덱스가 생긴 뒤 동시 요청의 진 쪽이 500을 받지 않는다.
--    순서상 이 마이그레이션을 먼저 적용해도 안전하다 — enqueue()가 아직
--    옛 코드면 그냥 API 에러가 사용자에게 노출되는 정도이고(더 나빠지지 않음),
--    앱 코드가 먼저 나가는 쪽이 이상적이다(제약이 없으면 새 except 블록이
--    조용히 안 타서 지금까지와 동작이 같다).
--
-- ⚠️ 적용 전 실측 확인(2026-08-19) — 현재 열린(PENDING/PROCESSING) 잡 중
--    (session_id, kind) 중복 0건. 기존 데이터가 이 제약을 어기지 않는다.

CREATE UNIQUE INDEX job_open_one_per_kind_idx
    ON job (session_id, kind) WHERE status IN ('PENDING', 'PROCESSING');
