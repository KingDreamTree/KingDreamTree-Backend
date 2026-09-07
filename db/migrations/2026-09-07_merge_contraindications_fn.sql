-- analysis_session.contraindications 를 DB 안에서 원자적으로 병합하는 함수 (#172)
--
-- 왜 필요한가
--   금기(부상 주의)는 두 곳에서 저장된다 — 코치 대화 도중(그 턴의 flag)과 [적용] 시점
--   (히스토리 전체 재수집). 지금은 앱이 "현재 목록 읽기 → 새 부위 더하기 → 통째로 UPDATE"
--   를 하므로, 두 저장이 겹치면 나중 것이 먼저 것을 못 본 채 덮어써 부위 하나가 사라진다.
--   부상 정보라 확률이 낮아도 막는다 — 사라진 부위에 부담 주는 운동이 다음 루틴에 들어간다.
--
-- 어떻게 막나
--   병합을 UPDATE 한 문장 안에서 한다. Postgres 는 같은 행을 갱신하는 두 트랜잭션을 행 잠금으로
--   직렬화하고, 뒤 트랜잭션은 앞 것이 커밋한 새 값 위에 SET 식을 다시 평가한다. 그래서
--   앞 저장의 부위가 뒤 저장에 보인다 — "읽고 나서 쓰기" 사이가 없다.
--
-- ⚠️ 앱 코드(routes/coach_chat._merge_contraindications)는 이 함수가 없으면(PGRST202)
--    종전 방식으로 폴백한다. 그래서 순서와 무관하게 안전하지만, 폴백은 여전히 겹침에 취약하다 —
--    이 마이그레이션을 적용해야 수리가 실제로 켜진다. 적용 후 scripts/verify_contraindication_merge.py.
--
-- 중복 판정은 종전 앱 로직과 같다 — 같은 {body_part, severity} 객체가 이미 있으면 안 넣는다.

CREATE OR REPLACE FUNCTION merge_contraindications(p_session_id UUID, p_added JSONB)
RETURNS JSONB
LANGUAGE sql
AS $$
    UPDATE analysis_session AS s
    SET contraindications = COALESCE(s.contraindications, '[]'::jsonb) || (
        SELECT COALESCE(jsonb_agg(a.elem ORDER BY a.ord), '[]'::jsonb)
        FROM jsonb_array_elements(COALESCE(p_added, '[]'::jsonb)) WITH ORDINALITY AS a(elem, ord)
        WHERE NOT (COALESCE(s.contraindications, '[]'::jsonb) @> jsonb_build_array(a.elem))
    )
    WHERE s.session_id = p_session_id
    RETURNING s.contraindications;
$$;
