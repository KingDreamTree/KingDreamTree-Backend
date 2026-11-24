"""Apply every Flyway migration to disposable local PostgreSQL; no real DB credentials."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prompt_version import sql_activate, sql_new


def run(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


def main() -> None:
    name = "refit-migration-test-" + uuid.uuid4().hex[:12]
    network = run("docker", "network", "create", "--internal", name).decode().strip()
    container = None
    try:
        container = (
            run(
                "docker",
                "run",
                "--detach",
                "--network",
                network,
                "--network-alias",
                "database",
                "--env",
                "POSTGRES_PASSWORD=disposable-test-only",
                "postgres:17",
            )
            .decode()
            .strip()
        )
        for _ in range(60):
            result = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not start")

        def sql(statement: str) -> bytes:
            return run(
                "docker",
                "exec",
                "-i",
                container,
                "psql",
                "-U",
                "postgres",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                data=statement.encode(),
            )

        sql("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;")

        def flyway(command: str) -> dict:
            output = run(
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--env",
                "FLYWAY_URL=jdbc:postgresql://database:5432/postgres",
                "--env",
                "FLYWAY_USER=postgres",
                "--env",
                "FLYWAY_PASSWORD=disposable-test-only",
                "--mount",
                f"type=bind,source={ROOT / 'db/flyway/flyway.conf'},target=/flyway/conf/flyway.conf,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'db/flyway/sql'},target=/flyway/sql,readonly",
                "redgate/flyway:13.13.0",
                "-outputType=json",
                command,
            )
            return json.loads(output)

        result = flyway("migrate")
        assert result["migrationsExecuted"] >= 1, result
        assert flyway("validate")["validationSuccessful"] is True
        assert flyway("migrate")["migrationsExecuted"] == 0
        assert int(sql("SELECT count(*) FROM pg_tables WHERE schemaname='public';")) >= 18
        # Exercise generated prompt SQL with Flyway's default schema and an outer
        # transaction, including a failed activation that must preserve the old one.
        for content in ("synthetic v1", "synthetic v2"):
            sql(
                "BEGIN; SET LOCAL search_path=refit_migrations;\n"
                + sql_new("refit.test", content, "test")
                + "COMMIT;"
            )
        sql("BEGIN;\n" + sql_activate("refit.test", 1, "test") + "COMMIT;")
        try:
            sql("BEGIN;\n" + sql_activate("refit.test", 99, "test") + "COMMIT;")
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("Missing prompt version should fail")
        assert (
            sql(
                "SELECT version FROM public.prompt_version WHERE name='refit.test' AND is_active;"
            ).strip()
            == b"1"
        )
        print(
            "All migrations applied to an empty PostgreSQL 17 database; validation and repeat run passed."
        )
    finally:
        if container:
            run("docker", "rm", "--force", "--volumes", container)
        run("docker", "network", "rm", network)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        # This script only uses synthetic local data and the test password above.
        print(exc.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(1) from None
