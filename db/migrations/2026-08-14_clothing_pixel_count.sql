-- body_part_segment.clothing_pixel_count 추가
--
-- 왜 필요한가
--   옷 픽셀을 인접 부위로 흡수시킨 뒤(part_merge), 그 부위의 pixel_count 중
--   **얼마가 옷에서 왔는지**를 진단 쪽이 알아야 한다. 이 비율이 크면 그 부위는
--   맨살이 아니라 **옷 위 실루엣**을 잰 것이라, 근육 윤곽 근거로 쓰면 안 된다.
--   지금은 계산해서 잡 결과에만 실어 보내고 있어, 잡이 끝나면 사라진다.
--
-- ⚠️ nullable 로 둔다. NOT NULL DEFAULT 0 으로 만들면
--      NULL = 병합 미적용(구버전 행 / SEG_MERGE_CLOTHING=false)
--      0    = 병합 적용했고 흡수분이 없음
--    두 경우가 합쳐져 "옷을 안 입은 것"과 "병합을 안 돌린 것"을 구분할 수 없다.
--
-- ⚠️ CHECK 로 pixel_count 를 넘지 못하게 한다. 흡수분은 pixel_count 의 부분집합이다.

ALTER TABLE body_part_segment
    ADD COLUMN IF NOT EXISTS clothing_pixel_count INT;

ALTER TABLE body_part_segment
    DROP CONSTRAINT IF EXISTS body_part_segment_clothing_px_chk;

ALTER TABLE body_part_segment
    ADD CONSTRAINT body_part_segment_clothing_px_chk
        CHECK (clothing_pixel_count IS NULL
               OR (clothing_pixel_count >= 0
                   AND clothing_pixel_count <= pixel_count));

COMMENT ON COLUMN body_part_segment.clothing_pixel_count IS
    'pixel_count 중 옷에서 흡수된 픽셀 수 (app/services/part_merge.py). '
    'NULL = 병합 미적용(구버전 행), 0 = 병합 적용했고 흡수 없음. '
    '이 구분을 위해 NOT NULL DEFAULT 0 으로 만들지 말 것.';
