import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.database import Database, baseline_sql, check_local_docker, normalized_schema
from scripts.prompt_version import _write, sql_activate, sql_new


class DatabaseSafetyTests(unittest.TestCase):
    def test_remote_docker_cannot_receive_credentials(self):
        with patch.dict("os.environ", {"DOCKER_HOST": "ssh://ubuntu@example.test"}):
            with self.assertRaisesRegex(RuntimeError, "local Docker"):
                check_local_docker()

    def test_clean_and_repair_are_not_exposed(self):
        db = Database.__new__(Database)
        for command in ("clean", "repair", "undo"):
            with self.assertRaises(ValueError):
                db.flyway(command)

    def test_schema_comparison_keeps_structural_changes(self):
        schema = "CREATE TABLE public.example (id integer);\n"
        self.assertEqual(
            normalized_schema("\\restrict token\n" + schema), normalized_schema(schema)
        )
        self.assertNotEqual(
            normalized_schema(schema), normalized_schema(schema.replace("integer", "bigint"))
        )

    def test_baseline_uses_transaction_local_settings(self):
        sql = baseline_sql("\\restrict token\nSET row_security = off;\nCREATE SCHEMA public;\n")
        self.assertEqual(sql, "SET LOCAL row_security = off;\n")

    def test_prompt_migrations_use_public_and_flyway_transaction(self):
        for sql in (sql_new("part.rules", "test", "test"), sql_activate("part.rules", 1, "test")):
            self.assertIn("public.prompt_version", sql)
            self.assertNotIn("\nCOMMIT;", sql)
            self.assertNotIn("\nBEGIN;", sql)
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.prompt_version.MIGRATIONS", Path(directory)):
                path = _write("part_rules", "SELECT 1;\n")
                self.assertRegex(path.name, r"^V\d+__prompt_part_rules\.sql$")


if __name__ == "__main__":
    unittest.main()
