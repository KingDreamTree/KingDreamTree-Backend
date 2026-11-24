"""일회성 PostgreSQL에서만 실행하는 카카오 인증 DB 동작 검증."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4


def verify_auth_schema(sql) -> None:
    # Supabase service_role은 BYPASSRLS다. 로컬 테스트 역할도 같은 속성으로 맞춘다.
    sql("ALTER ROLE service_role BYPASSRLS;")
    anonymous_id = sql("INSERT INTO public.users DEFAULT VALUES RETURNING user_id;").strip()
    before = int(sql("SELECT count(*) FROM public.users;"))

    def login(index, subject="9900000001", app_id=123456):
        digest = f"{index:064x}"
        result = sql(
            "SET ROLE service_role; SELECT user_id FROM public.refit_kakao_login("
            f"{app_id}, '{subject}', '{digest}', 3600);"
        ).strip()
        return result, digest

    with ThreadPoolExecutor(max_workers=4) as pool:
        sessions = list(pool.map(login, range(1, 5)))
    assert len({uid for uid, _ in sessions}) == 1, "Concurrent login created duplicate users"
    user_id = sessions[0][0].decode()
    assert int(sql("SELECT count(*) FROM public.users;")) == before + 1
    assert (
        sql(f"SELECT user_id FROM public.users WHERE user_id='{anonymous_id.decode()}';").strip()
        == anonymous_id
    )
    second_user, _ = login(5, subject="9900000002")
    other_app_user, _ = login(6, app_id=654321)
    assert second_user != sessions[0][0] != other_app_user

    digest = sessions[0][1]
    assert (
        sql("SET ROLE service_role; " f"SELECT user_id FROM public.refit_auth_session('{digest}');")
        .strip()
        .decode()
        == user_id
    )
    sql(
        "UPDATE public.auth_session SET created_at=now()-interval '2 hours', "
        f"expires_at=now()-interval '1 hour' WHERE token_hash='{digest}';"
    )
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest}');").strip()
    # 만료 행을 정리해도 다른 기기의 유효한 세션은 유지한다.
    login(7)
    assert int(sql(f"SELECT count(*) FROM public.auth_session WHERE user_id='{user_id}';")) == 4

    digest = sessions[1][1]
    sql(f"SET ROLE service_role; DELETE FROM public.auth_session WHERE token_hash='{digest}';")
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest}');").strip()
    assert sql(f"SELECT * FROM public.refit_auth_session('{sessions[2][1]}');").strip()

    # 세션 생성 실패 시 사용자 생성도 롤백한다.
    before_failure = int(sql("SELECT count(*) FROM public.users;"))
    try:
        sql("SELECT * FROM public.refit_kakao_login(123456, '9900000099', 'invalid-hash', 3600);")
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Invalid token hash accepted")
    assert int(sql("SELECT count(*) FROM public.users;")) == before_failure

    for role in ("anon", "authenticated"):
        for table in ("auth_session", "user_identity"):
            assert (
                sql(f"SELECT has_table_privilege('{role}', 'public.{table}', 'SELECT');").strip()
                == b"f"
            )
        for signature in (
            "refit_kakao_login(bigint,text,text,integer)",
            "refit_auth_session(text)",
        ):
            assert (
                sql(
                    f"SELECT has_function_privilege('{role}', 'public.{signature}', 'EXECUTE');"
                ).strip()
                == b"f"
            )
        try:
            sql(f"SET ROLE {role}; SELECT * FROM public.refit_auth_session('{sessions[2][1]}');")
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError(f"{role} bypassed authentication")

    sql(f"DELETE FROM public.users WHERE user_id='{user_id}';")
    assert int(sql(f"SELECT count(*) FROM public.user_identity WHERE user_id='{user_id}';")) == 0
    assert int(sql(f"SELECT count(*) FROM public.auth_session WHERE user_id='{user_id}';")) == 0
    print(
        "Kakao auth DB: concurrent login, expiry, revocation, ownership, rollback and privileges passed."
    )
    verify_refresh_schema(sql)


def verify_refresh_schema(sql) -> None:
    def digest(number):
        return f"{number:064x}"

    def login(number):
        return sql(
            "SET ROLE service_role; SELECT user_id FROM public.refit_kakao_login_persistent("
            f"123456, '9900000100', '{digest(number)}', '{digest(number + 1)}', 3600);"
        ).strip().decode()

    def refresh(old, new, request_id):
        return sql(
            "SET ROLE service_role; SELECT user_id, expires_at FROM public.refit_refresh_login("
            f"'{digest(old)}', '{request_id}', '{digest(new)}', '{digest(new + 1)}', 3600);"
        ).strip()

    def logout(access=None, refresh_token=None):
        access_arg = f"'{digest(access)}'" if access is not None else "NULL"
        refresh_arg = f"'{digest(refresh_token)}'" if refresh_token is not None else "NULL"
        sql(f"SET ROLE service_role; SELECT public.refit_logout({access_arg}, {refresh_arg});")

    user_id = login(100)
    assert login(200) == user_id
    measurement = sql(
        f"INSERT INTO public.analysis_session(user_id) VALUES ('{user_id}') RETURNING session_id;"
    ).strip().decode()
    sql(
        "UPDATE public.auth_session SET created_at=now()-interval '2 years', "
        f"expires_at=now()-interval '1 year' WHERE token_hash='{digest(100)}';"
        "UPDATE public.auth_refresh_session SET created_at=now()-interval '2 years';"
    )
    request_id = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: refresh(101, 110, request_id), range(4)))
    assert len(set(results)) == 1 and results[0].startswith(user_id.encode())
    assert int(sql("SELECT count(*) FROM public.auth_refresh_token WHERE used_at IS NULL;")) == 2
    assert sql(f"SELECT * FROM public.refit_auth_session('{digest(110)}');").strip()
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest(100)}');").strip()

    # Response-loss recovery still returns the next refresh credential after access cleanup.
    sql(f"DELETE FROM public.auth_session WHERE token_hash='{digest(110)}';")
    assert refresh(101, 110, request_id) == results[0]
    assert refresh(111, 120, uuid4())
    # Used token + different request is replay; revocation must commit despite empty result.
    assert not refresh(101, 130, uuid4())
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest(120)}');").strip()
    assert not refresh(121, 140, uuid4())
    assert refresh(201, 210, uuid4()), "Another device was revoked"
    assert sql(f"SELECT session_id FROM public.analysis_session WHERE session_id='{measurement}';").strip()

    # Logout using an ancestor access token removes all generations in that family.
    logout(access=200)
    assert not refresh(211, 220, uuid4())
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest(210)}');").strip()
    logout(access=200)  # Idempotent retry.

    # Concurrent logout/refresh cannot leave any usable descendants behind.
    for number in (300, 400, 500):
        login(number)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(refresh, number + 1, number + 10, uuid4())
            exiting = pool.submit(logout, None, number + 1)
            pending.result()
            exiting.result()
        assert not refresh(number + 11, number + 20, uuid4())
        assert not sql(f"SELECT * FROM public.refit_auth_session('{digest(number + 10)}');").strip()
    assert sql(f"SELECT * FROM public.users WHERE user_id='{user_id}';").strip()
    assert sql(f"SELECT * FROM public.analysis_session WHERE session_id='{measurement}';").strip()

    # Concurrent distinct refresh requests detect reuse instead of minting two branches.
    login(600)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(refresh, 601, number, uuid4()) for number in (610, 620)]
        assert sum(bool(f.result()) for f in pending) == 1
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest(610)}');").strip()
    assert not sql(f"SELECT * FROM public.refit_auth_session('{digest(620)}');").strip()

    # Invalid insertion must roll back the complete login, including user creation.
    before = int(sql("SELECT count(*) FROM public.users;"))
    try:
        sql(
            "SELECT * FROM public.refit_kakao_login_persistent(123456, '9900000999', "
            f"'{digest(700)}', 'invalid', 3600);"
        )
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Invalid refresh hash accepted")
    assert int(sql("SELECT count(*) FROM public.users;")) == before
    for table in ("auth_refresh_session", "auth_refresh_token"):
        assert sql(f"SELECT relrowsecurity FROM pg_class WHERE oid='public.{table}'::regclass;").strip() == b"t"
        for role in ("anon", "authenticated"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert sql(f"SELECT has_table_privilege('{role}', 'public.{table}', '{privilege}');").strip() == b"f"
    for role in ("anon", "authenticated"):
        for signature in (
            "refit_kakao_login_persistent(bigint,text,text,text,integer)",
            "refit_refresh_login(text,uuid,text,text,integer)",
            "refit_logout(text,text)",
        ):
            assert sql(f"SELECT has_function_privilege('{role}', 'public.{signature}', 'EXECUTE');").strip() == b"f"
    login(800)
    sql(f"DELETE FROM public.users WHERE user_id='{user_id}';")
    assert not refresh(801, 810, uuid4())
    assert int(sql(f"SELECT count(*) FROM public.auth_refresh_session WHERE user_id='{user_id}';")) == 0
    print("Persistent login DB: expiry recovery, retry, replay, concurrent logout, isolation and cascade passed.")
