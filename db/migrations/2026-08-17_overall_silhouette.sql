-- 종합 진단이 실루엣을 직접 보게 된다 (F09 이미지 입력 추가, 2026-08-17)
--
-- 종전 F09 는 부위별 결론만 받는 텍스트 전용이었다. 부위 9개를 각각 잘 봐도
-- "상체는 두꺼운데 하체가 얇다" 같은 **부위 사이의 관계**는 어느 부위 카드에도
-- 안 나온다. 종합에 원본 두 장을 같이 넣어 전체 형태를 직접 비교하게 한다.
--
-- ⚠️ 전부 nullable 이다. 이 마이그레이션 이전 세션은 값이 없고, 응답에서 null 로
--    내려간다. 프론트는 기존 필드(summary·priority_parts)만으로도 동작한다.

ALTER TABLE overall_diagnosis
    --: 레퍼런스와 사용자의 가장 중요한 차이 1~3개. 부위 카드로는 안 보이는
    --  관계(상하체 균형, 어깨-허리 폭 비율 등)를 담는다.
    ADD COLUMN IF NOT EXISTS key_differences JSONB,
    --: 전체 실루엣 비교 한 단락. summary 가 "무엇을 할지"라면 이건 "지금 어떤 형태인지".
    ADD COLUMN IF NOT EXISTS silhouette      TEXT,
    --: 종합 판단의 확신도 0.00~1.00. 사진이 흐리거나 판단 불가 부위가 많으면 낮다.
    --  ⚠️ similarity_score 와 무관하다. 점수는 코드가 규칙으로 계산한다(score_source=RULE).
    --     이건 "이 종합을 얼마나 믿을 수 있나"이지 "얼마나 닮았나"가 아니다.
    ADD COLUMN IF NOT EXISTS confidence      NUMERIC(3, 2);

DO $$
BEGIN
    ALTER TABLE overall_diagnosis
        ADD CONSTRAINT overall_diagnosis_confidence_range
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN overall_diagnosis.key_differences IS
    '부위 카드로는 드러나지 않는 전체 차이 1~3개 (JSONB 문자열 배열)';
COMMENT ON COLUMN overall_diagnosis.silhouette IS
    '전체 실루엣·비율·상하체 균형 비교 한 단락';
COMMENT ON COLUMN overall_diagnosis.confidence IS
    '종합 판단의 확신도 0~1. similarity_score 와 별개다';
