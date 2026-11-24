"""Validate local database settings and construct an in-memory environment.

No credential values are printed or written to another file.
DATABASE_PASSWORD can override the placeholder in the Supabase connection URI.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit


def connection_environment(values: dict, expected_project: str) -> dict[str, str]:
    raw_url = values.get("DATABASE_URL") or ""
    scheme, separator, rest = raw_url.partition("://")
    if not separator or scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must start with postgresql://")
    credentials, at, address = rest.rpartition("@")
    if not at:
        raise ValueError("DATABASE_URL must include a database username")
    username, _, uri_password = credentials.partition(":")
    try:
        # Parse the host separately: Supabase placeholders and raw passwords can
        # contain brackets, which urlsplit otherwise mistakes for an IPv6 host.
        url = urlsplit(f"{scheme}://{address}")
        host = url.hostname or ""
        port = url.port or 5432
    except ValueError:
        raise ValueError("DATABASE_URL is not a valid PostgreSQL URI") from None
    user = unquote(username)
    password = values.get("DATABASE_PASSWORD") or unquote(uri_password)
    database = unquote(url.path.removeprefix("/"))
    direct = host == f"db.{expected_project}.supabase.co" and user == "postgres"
    pooled = host.endswith(".pooler.supabase.com") and user == f"postgres.{expected_project}"
    if not expected_project or not (direct or pooled):
        raise ValueError("The connection must target this project's Supabase database")
    if port != 5432:
        raise ValueError("Use a direct or Session pooler connection on port 5432")
    if database != "postgres":
        raise ValueError("Expected the existing Supabase postgres database")
    if not password or password in {"[YOUR-PASSWORD]", "[YOUR_PASSWORD]"}:
        raise ValueError("Set DATABASE_PASSWORD to the database password")
    if any(c in password for c in "\r\n\x00"):
        raise ValueError("A Docker env file cannot contain a multiline password")
    return {
        "PGHOST": host,
        "PGPORT": str(port),
        "PGDATABASE": database,
        "PGUSER": user,
        "PGPASSWORD": password,
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": "/etc/ssl/certs/ca-certificates.crt",
        "PGCONNECT_TIMEOUT": "15",
        "FLYWAY_URL": (
            f"jdbc:postgresql://{host}:{port}/{database}"
            "?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt"
        ),
        "FLYWAY_USER": user,
        "FLYWAY_PASSWORD": password,
    }
