"""Local-only Supabase backups and Flyway operations.

Credentials are read from .env.flyway into memory and passed only to local,
short-lived Docker containers. They are never written to deployment settings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prepare_database_env import connection_environment

PG_IMAGE = "postgres:17"
FLYWAY_IMAGE = "redgate/flyway:13.13.0"
CA_URL = "https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt"
SQL_DIR = ROOT / "db" / "flyway" / "sql"
BASELINE = SQL_DIR / "V1__web_only_baseline.sql"
BACKUPS = Path.home() / "refit-backups"
WEB_TAG = "web-only-before-app-2026-11-24"

INVENTORY_SQL = r"""
SET TIME ZONE 'UTC';
SET extra_float_digits = 3;
SELECT format(
  'SELECT json_build_object(''table'', %L, ''rows'', count(*), ''digest'', '
  'md5(coalesce(string_agg(md5(to_jsonb(t)::text), '''' ORDER BY md5(to_jsonb(t)::text)), ''''))) '
  'FROM %I.%I t;', schemaname || '.' || tablename, schemaname, tablename
)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
\gexec
"""


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def normalized_schema(text: str) -> str:
    # pg_dump's random psql guard tokens aren't SQL and differ on every export.
    # Full Supabase dumps omit the built-in public schema declaration, whereas
    # --schema=public dumps include it. Neither statement describes app objects.
    bootstrap = {"CREATE SCHEMA public;", "COMMENT ON SCHEMA public IS 'standard public schema';"}
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip()
        and not line.startswith(("--", "\\restrict", "\\unrestrict"))
        and line.strip() not in bootstrap
    ]
    return "\n".join(lines).rstrip() + "\n"


def baseline_sql(schema: str) -> str:
    """Convert pg_dump DDL to transactional JDBC SQL, without any table data."""
    lines = []
    for line in schema.splitlines():
        if line.startswith(("\\restrict", "\\unrestrict")):
            continue
        if line.startswith("SET "):
            line = line.replace("SET ", "SET LOCAL ", 1)
        if line == "SELECT pg_catalog.set_config('search_path', '', false);":
            line = "SELECT pg_catalog.set_config('search_path', '', true);"
        # Flyway creates the configured schemas before executing migrations.
        if line == "CREATE SCHEMA public;":
            continue
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def check_local_docker() -> None:
    override = os.environ.get("DOCKER_HOST", "")
    if override and not override.startswith(("npipe://", "unix://")):
        raise RuntimeError("Only a local Docker engine may receive database credentials")
    result = subprocess.run(["docker", "context", "inspect"], capture_output=True, check=True)
    host = json.loads(result.stdout)[0]["Endpoints"]["docker"]["Host"]
    if not host.startswith(("npipe://", "unix://")):
        raise RuntimeError("The selected Docker context is not local")


class Database:
    def __init__(self) -> None:
        check_local_docker()
        app = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
        project = (urlsplit(app.get("SUPABASE_URL") or "").hostname or "").split(".")[0]
        self.env = connection_environment(
            dotenv_values(ROOT / ".env.flyway", encoding="utf-8-sig", interpolate=False), project
        )
        self.identity = hashlib.sha256(
            f"{self.env['PGHOST']}|{self.env['PGUSER']}|{self.env['PGDATABASE']}".encode()
        ).hexdigest()
        self.ca = ROOT / "tmp" / "supabase-root.crt"
        self.ca.parent.mkdir(exist_ok=True)
        if not self.ca.exists():
            with urllib.request.urlopen(CA_URL, timeout=30) as response:
                self.ca.write_bytes(response.read())

    def run(self, args: list[str], *, data: bytes | None = None, env=None) -> bytes:
        result = subprocess.run(args, input=data, capture_output=True, env=env)
        if result.returncode:
            # Errors from COPY/restore can include row values: keep them local.
            message = result.stderr.decode("utf-8", errors="replace")
            for value in (self.env["PGPASSWORD"], self.env["FLYWAY_URL"]):
                message = message.replace(value, "<redacted>")
            log = ROOT / "tmp" / "database-error.log"
            log.write_text(message, encoding="utf-8")
            raise RuntimeError(f"Database tool failed; details are in the local file {log}")
        return result.stdout

    def container(self, image: str, args: list[str], *, kind="", mounts=(), data=None) -> bytes:
        command = ["docker", "run", "--rm"]
        for key in self.env:
            if kind and key.startswith(kind):
                command += ["--env", key]
        for source, target, readonly in mounts:
            spec = f"type=bind,source={Path(source).resolve()},target={target}"
            command += ["--mount", spec + (",readonly" if readonly else "")]
        if kind:
            command += [
                "--mount",
                f"type=bind,source={self.ca},target=/etc/ssl/certs/ca-certificates.crt,readonly",
            ]
        if data is not None:
            command += ["--interactive"]
        return self.run(command + [image] + args, data=data, env=os.environ | self.env)

    def query(self, sql: str) -> bytes:
        return self.container(
            PG_IMAGE,
            ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            kind="PG",
            data=sql.encode(),
        )

    def inventory(self, container: str | None = None) -> dict:
        if container:
            raw = self.run(
                [
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
                ],
                data=INVENTORY_SQL.encode(),
            )
        else:
            raw = self.query(INVENTORY_SQL)
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        return {row["table"]: {"rows": row["rows"], "digest": row["digest"]} for row in rows}

    def backup(self, label: str) -> Path:
        version = self.query("SHOW server_version_num;").decode().strip()
        if int(version) // 10000 != 17:
            raise RuntimeError(
                "Review the backup tools before using a different PostgreSQL major version"
            )
        now = datetime.now(timezone(timedelta(hours=9)))
        directory = BACKUPS / f"{label}-{now:%Y%m%dT%H%M%S}"
        directory.mkdir(parents=True, mode=0o700)
        before = self.inventory()
        print(f"Creating local backup of {len(before)} public tables: {directory}", flush=True)
        self.container(
            PG_IMAGE,
            ["pg_dump", "--format=custom", "--no-owner", "--file=/backup/database.dump"],
            kind="PG",
            mounts=[(directory, "/backup", False)],
        )
        roles = self.container(
            PG_IMAGE, ["pg_dumpall", "--roles-only", "--no-role-passwords"], kind="PG"
        )
        (directory / "roles.sql").write_bytes(roles)
        self.container(
            PG_IMAGE,
            [
                "pg_restore",
                "--schema-only",
                "--schema=public",
                "--no-owner",
                "--no-privileges",
                "--file=/backup/public-schema.sql",
                "/backup/database.dump",
            ],
            mounts=[(directory, "/backup", False)],
        )
        (directory / "inventory.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
        manifest = {
            "created_at": now.isoformat(),
            "database_identity": self.identity,
            "web_code_tag": WEB_TAG,
            "postgres_version_num": version,
            "scope": "Full logical database dump and roles without role passwords; Storage file bytes are separate.",
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.verify_backup(directory)
        return directory

    def verify_backup(self, directory: Path) -> None:
        directory = directory.resolve()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["database_identity"] != self.identity:
            raise RuntimeError("This backup belongs to a different database")
        name = "refit-restore-" + uuid.uuid4().hex[:12]
        container = (
            self.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--label",
                    "refit.disposable=restore-verification",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "--mount",
                    f"type=bind,source={directory},target=/backup,readonly",
                    PG_IMAGE,
                ]
            )
            .decode()
            .strip()
        )
        try:
            for attempt in range(30):
                ready = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "postgres",
                    ],
                    capture_output=True,
                )
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("The isolated restore database did not become ready")
            # Only this newly-created, network-isolated test container is modified.
            self.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "psql",
                    "-U",
                    "postgres",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                data=b"CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;\n",
            )
            self.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_restore",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "--schema=public",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "/backup/database.dump",
                ]
            )
            restored = self.inventory(container)
            (directory / "restored-inventory.json").write_text(
                json.dumps(restored, indent=2), encoding="utf-8"
            )
            expected = json.loads((directory / "inventory.json").read_text(encoding="utf-8"))
            if restored != expected:
                raise RuntimeError(
                    "Restored row counts/content hashes differ from the source snapshot"
                )
            if self.inventory() != expected:
                raise RuntimeError("The live database changed during backup; take another backup")
            restored_schema = self.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_dump",
                    "-U",
                    "postgres",
                    "--schema-only",
                    "--schema=public",
                    "--no-owner",
                    "--no-privileges",
                ]
            ).decode("utf-8")
            original_schema = (directory / "public-schema.sql").read_text(encoding="utf-8")
            # PostgreSQL minor versions can deparse identical casts differently.
            # Parse the reference DDL on the same engine before comparing schemas.
            self.run(
                [
                    "docker",
                    "exec",
                    container,
                    "createdb",
                    "-U",
                    "postgres",
                    "--template=template0",
                    "schema_reference",
                ]
            )
            reference_sql = original_schema
            if re.search(r"^CREATE SCHEMA public;", reference_sql, re.MULTILINE):
                reference_sql = "DROP SCHEMA public;\n" + reference_sql
            self.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "schema_reference",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                data=reference_sql.encode("utf-8"),
            )
            reference_schema = self.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    "schema_reference",
                    "--schema-only",
                    "--schema=public",
                    "--no-owner",
                    "--no-privileges",
                ]
            ).decode("utf-8")
            if normalized_schema(restored_schema) != normalized_schema(reference_schema):
                (directory / "restored-public-schema.sql").write_text(
                    restored_schema, encoding="utf-8"
                )
                raise RuntimeError(
                    "Restored schema differs; inspect the private backup schema files"
                )
            filenames = [
                "database.dump",
                "roles.sql",
                "public-schema.sql",
                "inventory.json",
                "manifest.json",
            ]
            verification = {
                "public_restore_verified": True,
                "verified_tables": len(restored),
                "verified_rows": sum(row["rows"] for row in restored.values()),
                "sha256": {filename: sha256(directory / filename) for filename in filenames},
                "scope": "public schema, row counts and content hashes; managed schemas remain in the full dump but were not restore-tested",
            }
            (directory / "verification.json").write_text(
                json.dumps(verification, indent=2), encoding="utf-8"
            )
            print(
                f"Restore verified: {verification['verified_tables']} tables, {verification['verified_rows']} rows",
                flush=True,
            )
        finally:
            # The exact ID above is a disposable container we created, not a user's DB.
            self.run(["docker", "rm", "--force", "--volumes", container])

    def verify_manifest(self, directory: Path) -> None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        verification = json.loads((directory / "verification.json").read_text(encoding="utf-8"))
        if (
            manifest["database_identity"] != self.identity
            or verification.get("public_restore_verified") is not True
        ):
            raise RuntimeError("A verified backup of this database is required")
        for name, expected in verification["sha256"].items():
            if Path(name).name != name or sha256(directory / name) != expected:
                raise RuntimeError("Backup checksum verification failed")

    def flyway(self, command: str) -> dict:
        if command not in {"info", "validate", "migrate", "baseline"}:
            raise ValueError("This tool does not permit destructive Flyway commands")
        if not BASELINE.is_file():
            raise RuntimeError("Capture and review the baseline SQL before using Flyway")
        raw = self.container(
            FLYWAY_IMAGE,
            ["-outputType=json", command],
            kind="FLYWAY",
            mounts=[
                (ROOT / "db/flyway/flyway.conf", "/flyway/conf/flyway.conf", True),
                (SQL_DIR, "/flyway/sql", True),
            ],
        )
        return json.loads(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["backup", "verify-backup", "info", "validate", "baseline", "migrate"]
    )
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--label", default="before-migration")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.label):
        parser.error("Use a simple backup label without directory separators")
    db = Database()
    if args.command == "backup":
        print(db.backup(args.label))
    elif args.command == "verify-backup":
        if not args.backup:
            parser.error("--backup is required")
        db.verify_backup(args.backup)
    elif args.command == "baseline":
        if not args.backup:
            parser.error("--backup is required")
        db.verify_manifest(args.backup)
        original_schema = (args.backup / "public-schema.sql").read_text(encoding="utf-8")
        if BASELINE.read_text(encoding="utf-8") != baseline_sql(original_schema):
            raise RuntimeError("V1 must match the schema in the verified web-only backup")
        if db.inventory() != json.loads(
            (args.backup / "inventory.json").read_text(encoding="utf-8")
        ):
            raise RuntimeError("Data changed since the backup; take another backup before baseline")
        current_schema = db.container(
            PG_IMAGE,
            ["pg_dump", "--schema-only", "--schema=public", "--no-owner", "--no-privileges"],
            kind="PG",
        ).decode("utf-8")
        if normalized_schema(current_schema) != normalized_schema(original_schema):
            raise RuntimeError("Schema changed since the backup; review before baseline")
        result = db.flyway("baseline")
        print("Flyway baseline registered:", result.get("baselineVersion"))
    elif args.command == "migrate":
        if (
            db.query(
                "SELECT to_regclass('refit_migrations.flyway_schema_history') IS NOT NULL;"
            ).strip()
            != b"t"
        ):
            raise RuntimeError(
                "Register the existing database baseline with a verified backup first"
            )
        info = db.flyway("info")
        if any(row["state"] == "Pending" for row in info["migrations"]):
            db.backup("before-migration")
        result = db.flyway("migrate")
        print("Migrations executed:", result.get("migrationsExecuted"))
    elif args.command == "validate":
        result = db.flyway("validate")
        print("Migration validation:", result.get("validationSuccessful"))
    else:
        info = db.flyway("info")
        print("Schema version:", info.get("schemaVersion"))
        for row in info["migrations"]:
            print(row.get("version"), row.get("description"), row.get("state"))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
