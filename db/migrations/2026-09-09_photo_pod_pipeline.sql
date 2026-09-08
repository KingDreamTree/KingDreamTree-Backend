-- 사진 프라이버시 — 팟 직접 업로드(저장 없음) 경로 (PHOTO_PIPELINE=pod)
--
-- 1. photo.storage_path NULL 허용
--    사진 파일을 더 이상 Storage 에 두지 않는다. 행은 남는다 — 랜드마크·거울 여부·
--    크기·촬영 방식이 여기 있고 segmentation / part_diagnosis 가 photo_id 로 매달린다.
--    ⚠️ 종전 경로(PHOTO_PIPELINE=storage)는 계속 값을 채우므로 NOT NULL 만 푼다.
--
-- 2. photo.crop_box
--    팟은 세그 전에 거울 되돌리기 + 3:4 인물 중심 크롭을 하고, 맵은 그 크롭본
--    기준이다. 사진을 저장하지 않으니 화면은 **기기의 원본** 위에 맵을 얹어야
--    하는데, 크롭 박스가 없으면 어긋난다. 팟이 여기 남기고 프론트가 같은 박스로
--    잘라 그린다.
--      {"x": int, "y": int, "w": int, "h": int,            -- 되돌린(비반전) 원본 픽셀 좌표
--       "source_width": int, "source_height": int,        -- 되돌린 원본 크기
--       "flipped": bool}                                   -- 거울 되돌리기를 적용했는가
--    ⚠️ 종전 경로는 크롭본 자체를 저장하므로 NULL 이다 (프론트는 photo_url 을 쓴다).
--
-- 적용: Supabase SQL 편집기에서 실행. 종전 경로 배포에는 영향이 없다 (NULL 허용 +
--      nullable 컬럼 추가뿐). 되돌리려면 crop_box 를 지우고 NOT NULL 을 다시 건다 —
--      단 pod 경로로 만든 행(storage_path NULL)이 있으면 NOT NULL 복구는 실패한다.

ALTER TABLE photo ALTER COLUMN storage_path DROP NOT NULL;

ALTER TABLE photo ADD COLUMN IF NOT EXISTS crop_box JSONB;

COMMENT ON COLUMN photo.storage_path IS
    'Storage 경로. PHOTO_PIPELINE=pod 로 만든 행은 NULL — 사진을 저장하지 않는다.';
COMMENT ON COLUMN photo.crop_box IS
    '팟이 세그 전에 적용한 크롭 (비반전 원본 픽셀 좌표 + 원본 크기 + flipped). '
    '프론트가 기기 원본을 같은 박스로 잘라 맵을 얹는다. storage 경로에서는 NULL.';
