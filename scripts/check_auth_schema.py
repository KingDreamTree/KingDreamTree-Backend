"""일회성 PostgreSQL에서만 실행하는 카카오 인증 DB 동작 검증."""

import subprocess
from concurrent.futures import ThreadPoolExecutor


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
