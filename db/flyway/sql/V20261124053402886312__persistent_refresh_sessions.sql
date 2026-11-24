-- Additive upgrade: existing access-only sessions and legacy RPCs remain valid.
-- Refresh credentials deliberately have no time expiry (product login-until-logout policy).
CREATE TABLE public.auth_refresh_session (
    session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_refresh_session_user_idx ON public.auth_refresh_session(user_id);

CREATE TABLE public.auth_refresh_token (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    session_id uuid NOT NULL REFERENCES public.auth_refresh_session(session_id) ON DELETE CASCADE,
    used_at timestamptz,
    request_id uuid,
    replacement_hash text CHECK (replacement_hash ~ '^[0-9a-f]{64}$'),
    access_hash text CHECK (access_hash ~ '^[0-9a-f]{64}$'),
    access_expires_at timestamptz,
    CHECK ((used_at IS NULL AND request_id IS NULL AND replacement_hash IS NULL
            AND access_hash IS NULL AND access_expires_at IS NULL)
        OR (used_at IS NOT NULL AND request_id IS NOT NULL AND replacement_hash IS NOT NULL
            AND access_hash IS NOT NULL AND access_expires_at IS NOT NULL))
);
CREATE INDEX auth_refresh_token_session_idx ON public.auth_refresh_token(session_id);
CREATE UNIQUE INDEX auth_refresh_one_current_idx ON public.auth_refresh_token(session_id)
    WHERE used_at IS NULL;
ALTER TABLE public.auth_session ADD COLUMN refresh_session_id uuid
    REFERENCES public.auth_refresh_session(session_id) ON DELETE CASCADE;
CREATE INDEX auth_session_refresh_idx ON public.auth_session(refresh_session_id);

ALTER TABLE public.auth_refresh_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_refresh_token ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.auth_refresh_session, public.auth_refresh_token FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.auth_refresh_session, public.auth_refresh_token TO service_role;

CREATE FUNCTION public.refit_kakao_login_persistent(
    p_app_id bigint, p_subject text, p_token_hash text, p_refresh_hash text, p_ttl_seconds integer
)
RETURNS TABLE (user_id uuid, is_pro_user boolean, created_at timestamptz, expires_at timestamptz)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    v_login record;
    v_session_id uuid;
BEGIN
    SELECT * INTO STRICT v_login FROM public.refit_kakao_login(p_app_id, p_subject, p_token_hash, p_ttl_seconds);
    INSERT INTO public.auth_refresh_session(user_id) VALUES (v_login.user_id)
    RETURNING session_id INTO v_session_id;
    INSERT INTO public.auth_refresh_token(token_hash, session_id) VALUES (p_refresh_hash, v_session_id);
    UPDATE public.auth_session SET refresh_session_id=v_session_id WHERE token_hash=p_token_hash;
    RETURN QUERY SELECT v_login.user_id, v_login.is_pro_user, v_login.created_at, v_login.expires_at;
END;
$$;

CREATE FUNCTION public.refit_refresh_login(
    p_refresh_hash text, p_request_id uuid, p_token_hash text,
    p_next_refresh_hash text, p_ttl_seconds integer
)
RETURNS TABLE (user_id uuid, is_pro_user boolean, created_at timestamptz, expires_at timestamptz)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    v_session public.auth_refresh_session%ROWTYPE;
    v_token public.auth_refresh_token%ROWTYPE;
    v_expires_at timestamptz;
BEGIN
    IF p_request_id IS NULL OR p_ttl_seconds IS NULL OR p_ttl_seconds < 60 OR p_ttl_seconds > 86400 THEN
        RAISE EXCEPTION 'Invalid refresh request';
    END IF;
    -- Serialize refresh and logout by family, including requests on different generations.
    SELECT s.* INTO v_session FROM public.auth_refresh_session AS s
    JOIN public.auth_refresh_token AS t ON t.session_id=s.session_id
    WHERE t.token_hash=p_refresh_hash FOR UPDATE OF s;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT * INTO STRICT v_token FROM public.auth_refresh_token WHERE token_hash=p_refresh_hash;

    IF v_token.used_at IS NOT NULL THEN
        -- Retry the same request after response loss. No plaintext tokens in the database.
        IF v_token.request_id=p_request_id AND v_token.replacement_hash=p_next_refresh_hash
           AND v_token.access_hash=p_token_hash AND EXISTS (
               SELECT 1 FROM public.auth_refresh_token AS t
               WHERE t.token_hash=p_next_refresh_hash AND t.session_id=v_session.session_id
                 AND t.used_at IS NULL
           ) THEN
            RETURN QUERY SELECT u.user_id, u.is_pro_user, u.created_at, v_token.access_expires_at
            FROM public.users AS u WHERE u.user_id=v_session.user_id;
            RETURN;
        END IF;
        -- Reuse of a spent credential revokes this device's entire family, not other devices.
        -- Return normally so the revocation commits; raising an error here would roll it back.
        DELETE FROM public.auth_refresh_session WHERE session_id=v_session.session_id;
        RETURN;
    END IF;

    v_expires_at := clock_timestamp() + pg_catalog.make_interval(secs => p_ttl_seconds);
    UPDATE public.auth_refresh_token SET used_at=clock_timestamp(), request_id=p_request_id,
        replacement_hash=p_next_refresh_hash, access_hash=p_token_hash, access_expires_at=v_expires_at
    WHERE token_hash=p_refresh_hash;
    INSERT INTO public.auth_refresh_token(token_hash, session_id)
    VALUES (p_next_refresh_hash, v_session.session_id);
    -- Expired access rows are no longer needed, but spent refresh hashes detect replay.
    DELETE FROM public.auth_session AS s WHERE s.refresh_session_id=v_session.session_id AND s.expires_at<=now();
    INSERT INTO public.auth_session(token_hash, user_id, expires_at, refresh_session_id)
    VALUES (p_token_hash, v_session.user_id, v_expires_at, v_session.session_id);
    RETURN QUERY SELECT u.user_id, u.is_pro_user, u.created_at, v_expires_at
    FROM public.users AS u WHERE u.user_id=v_session.user_id;
END;
$$;

CREATE FUNCTION public.refit_logout(p_token_hash text, p_refresh_hash text)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    v_session_id uuid;
BEGIN
    IF p_refresh_hash IS NOT NULL THEN
        SELECT t.session_id INTO v_session_id FROM public.auth_refresh_token AS t
        WHERE t.token_hash=p_refresh_hash;
    ELSE
        SELECT s.refresh_session_id INTO v_session_id FROM public.auth_session AS s
        WHERE s.token_hash=p_token_hash;
    END IF;
    IF v_session_id IS NOT NULL THEN
        -- Same row lock as refresh, so a racing refresh cannot resurrect a logged-out session.
        PERFORM 1 FROM public.auth_refresh_session WHERE session_id=v_session_id FOR UPDATE;
        DELETE FROM public.auth_refresh_session WHERE session_id=v_session_id;
    ELSIF p_refresh_hash IS NULL THEN
        DELETE FROM public.auth_session WHERE token_hash=p_token_hash AND refresh_session_id IS NULL;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION public.refit_kakao_login_persistent(bigint,text,text,text,integer) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.refit_refresh_login(text,uuid,text,text,integer) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.refit_logout(text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refit_kakao_login_persistent(bigint,text,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.refit_refresh_login(text,uuid,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.refit_logout(text,text) TO service_role;
