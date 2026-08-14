-- 자세 판정값 두 개를 저장한다.
--
-- facing_delta — **판정에 쓰면서 저장은 안 하고 있었다.**
--   이 값으로 사용자를 거부하는데 남기지 않아서, "FACING 으로 반려된 사람들이
--   실제로 몸이 돌아가 있었나"를 확인할 방법이 없었다.
--   ⚠️ 상한 CHECK 를 걸지 않는다. 레퍼런스 어깨폭비로 나눈 값이라
--      레퍼런스가 많이 돌아가 있으면 1 을 넘는다.
--
-- pose_oks — OKS-inspired 유사도. **판정에 쓰지 않는다.**
--   지금 쓰는 각도 기반 pose_similarity 와 어느 쪽이 downstream 오차를
--   더 잘 예측하는지 비교하려고 모으기만 한다.
--
-- ⚠️ 둘 다 **통과한 사진에만 남는다.** 거부된 사진은 저장 자체를 안 하므로
--    반려 건의 분포는 여전히 알 수 없다.

BEGIN;

ALTER TABLE photo ADD COLUMN IF NOT EXISTS facing_delta NUMERIC(6,3);
ALTER TABLE photo ADD COLUMN IF NOT EXISTS pose_oks     NUMERIC(4,3);

ALTER TABLE photo DROP CONSTRAINT IF EXISTS photo_facing_delta_check;
ALTER TABLE photo ADD  CONSTRAINT photo_facing_delta_check CHECK (facing_delta >= 0);

ALTER TABLE photo DROP CONSTRAINT IF EXISTS photo_pose_oks_check;
ALTER TABLE photo ADD  CONSTRAINT photo_pose_oks_check CHECK (pose_oks BETWEEN 0 AND 1);

COMMENT ON COLUMN photo.facing_delta IS
    '레퍼런스와 몸 방향의 차이. 상한 없음 — 레퍼런스가 많이 돌아가 있으면 1을 넘는다.';
COMMENT ON COLUMN photo.pose_oks IS
    'OKS-inspired 유사도. 판정에 쓰지 않고 pose_similarity 와 비교하려고 모으는 중.';

COMMIT;
