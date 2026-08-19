-- body_part_segment.invalid_reason 에 MOSTLY_CLOTHING 추가
--
-- 왜 필요한가
--   옷 흡수 상한 계산이 crop 기준 → 원본 전체 기준으로 바뀌면서(8bc4e7b),
--   병합 없이도 이미 유효했던 부위까지 옷을 대량 흡수하는 사례가 생겼다.
--   is_valid 판정에 새 사유(MOSTLY_CLOTHING)를 쓰기 위한 제약 갱신.
--
-- ⚠️ 2026-08-19 프로덕션 장애 원인 — 이 마이그레이션 없이 앱 코드만 배포되면서
--    (app/services/segmenter.py 의 clothing_ratio 게이트), 이 값을 쓰려는 모든
--    세그멘테이션 잡이 23514(제약 위반)로 실패했다. **앱 코드와 이 마이그레이션은
--    같이 배포한다.** 순서가 어긋나면 같은 장애가 재발한다.

ALTER TABLE body_part_segment
    DROP CONSTRAINT IF EXISTS body_part_segment_invalid_reason_check;

ALTER TABLE body_part_segment
    ADD CONSTRAINT body_part_segment_invalid_reason_check
        CHECK (invalid_reason IN ('TOO_SMALL', 'TOO_SMALL_RATIO',
                                   'TRUNCATED', 'NOT_COMPARABLE',
                                   'MOSTLY_CLOTHING'));
