-- Supabase에서 직접 실행하거나 SQL 에디터에 붙여넣기
-- 실행 전 Supabase 프로젝트·버킷(images/, overlays/)을 먼저 생성할 것

CREATE TABLE IF NOT EXISTS analysis (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_image_url  TEXT NOT NULL,                -- images/ 버킷의 원본 사용자 이미지 URL
    ref_image_url   TEXT NOT NULL,                -- images/ 버킷의 레퍼런스 이미지 URL
    overlay_urls    TEXT[] DEFAULT '{}',          -- overlays/ 버킷의 오버레이 이미지 URL 목록
    analysis_json   JSONB,                        -- 체형 비교 분석 결과 (Claude Call1 출력)
    routine_json    JSONB,                        -- 개인화 운동 루틴 (Claude Call2 출력)
    created_at      TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- 최신순 조회 인덱스
CREATE INDEX IF NOT EXISTS analysis_created_at_idx ON analysis (created_at DESC);
