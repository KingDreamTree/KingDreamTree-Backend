-- 기존 users/분석 기록은 그대로 보존한다. 신규 카카오 계정만 내부 UUID에 연결한다.
CREATE TABLE public.user_identity (
    provider text NOT NULL CHECK (provider = 'kakao'),
    provider_app_id bigint NOT NULL CHECK (provider_app_id > 0),
    provider_subject text NOT NULL CHECK (provider_subject ~ '^[1-9][0-9]{0,18}$'),
    user_id uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, provider_app_id, provider_subject),
    UNIQUE (user_id, provider)
);

CREATE TABLE public.auth_session (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    user_id uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at)
);
CREATE INDEX auth_session_user_id_idx ON public.auth_session(user_id);
CREATE INDEX auth_session_expires_at_idx ON public.auth_session(expires_at);

ALTER TABLE public.user_identity ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_session ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.user_identity, public.auth_session FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_identity, public.auth_session TO service_role;
GRANT SELECT, INSERT ON TABLE public.users TO service_role;

-- 백엔드는 카카오에서 검증된 ID만 전달한다. 사용자와 세션 생성은 한 트랜잭션이다.
CREATE FUNCTION public.refit_kakao_login(
    p_app_id bigint, p_subject text, p_token_hash text, p_ttl_seconds integer
)
RETURNS TABLE (user_id uuid, is_pro_user boolean, created_at timestamptz, expires_at timestamptz)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    v_user_id uuid;
    v_expires_at timestamptz;
BEGIN
    IF p_ttl_seconds IS NULL OR p_ttl_seconds < 60 OR p_ttl_seconds > 86400 THEN
        RAISE EXCEPTION 'Invalid session lifetime';
    END IF;
    -- 동시 첫 로그인에서도 중복 사용자나 고아 행을 만들지 않는다.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('kakao:' || p_app_id::text || ':' || p_subject, 0)
    );
    SELECT i.user_id INTO v_user_id FROM public.user_identity AS i
    WHERE i.provider = 'kakao' AND i.provider_app_id = p_app_id AND i.provider_subject = p_subject;
    IF v_user_id IS NULL THEN
        INSERT INTO public.users DEFAULT VALUES RETURNING public.users.user_id INTO v_user_id;
        INSERT INTO public.user_identity(provider, provider_app_id, provider_subject, user_id)
        VALUES ('kakao', p_app_id, p_subject, v_user_id);
    END IF;
    DELETE FROM public.auth_session AS s WHERE s.user_id = v_user_id AND s.expires_at <= now();
    v_expires_at := now() + pg_catalog.make_interval(secs => p_ttl_seconds);
    INSERT INTO public.auth_session(token_hash, user_id, expires_at)
    VALUES (p_token_hash, v_user_id, v_expires_at);
    RETURN QUERY SELECT u.user_id, u.is_pro_user, u.created_at, v_expires_at
    FROM public.users AS u WHERE u.user_id = v_user_id;
END;
$$;

CREATE FUNCTION public.refit_auth_session(p_token_hash text)
RETURNS TABLE (user_id uuid)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = '' AS $$
    SELECT s.user_id FROM public.auth_session AS s
    WHERE s.token_hash = p_token_hash AND s.expires_at > now();
$$;

REVOKE ALL ON FUNCTION public.refit_kakao_login(bigint, text, text, integer) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.refit_auth_session(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refit_kakao_login(bigint, text, text, integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.refit_auth_session(text) TO service_role;
COMMENT ON TABLE public.users IS '내부 사용자 UUID. 앱 인증은 카카오 계정 연결 및 리핏 Bearer 세션을 사용한다.';
