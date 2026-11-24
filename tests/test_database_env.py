import unittest

from scripts.prepare_database_env import connection_environment


class DatabaseConnectionTests(unittest.TestCase):
    def settings(self, **updates):
        values = {
            "DATABASE_URL": "postgresql://postgres.refit:[YOUR-PASSWORD]@aws-0-example.pooler.supabase.com:5432/postgres",
            "DATABASE_PASSWORD": "quotes'and$dollars#percent%",
        }
        return values | updates

    def test_password_is_preserved_without_shell_interpolation(self):
        env = connection_environment(self.settings(), "refit")
        self.assertEqual(env["PGPASSWORD"], "quotes'and$dollars#percent%")
        self.assertEqual(env["FLYWAY_PASSWORD"], env["PGPASSWORD"])
        self.assertNotIn(env["PGPASSWORD"], env["FLYWAY_URL"])
        self.assertEqual(env["PGSSLMODE"], "verify-full")

    def test_wrong_project_is_rejected(self):
        with self.assertRaises(ValueError):
            connection_environment(self.settings(), "another-project")

    def test_separate_password_overrides_bracketed_uri_password(self):
        url = self.settings()["DATABASE_URL"].replace("[YOUR-PASSWORD]", "[a:raw@password]")
        env = connection_environment(self.settings(DATABASE_URL=url), "refit")
        self.assertEqual(env["PGPASSWORD"], self.settings()["DATABASE_PASSWORD"])

    def test_transaction_pooler_is_rejected(self):
        url = self.settings()["DATABASE_URL"].replace(":5432/", ":6543/")
        with self.assertRaises(ValueError):
            connection_environment(self.settings(DATABASE_URL=url), "refit")

    def test_missing_password_is_rejected(self):
        with self.assertRaises(ValueError):
            connection_environment(self.settings(DATABASE_PASSWORD=""), "refit")

    def test_multiline_password_is_rejected(self):
        with self.assertRaises(ValueError):
            connection_environment(self.settings(DATABASE_PASSWORD="first\nsecond"), "refit")

    def test_direct_connection_and_uri_encoded_password(self):
        env = connection_environment(
            {
                "DATABASE_URL": "postgresql://postgres:p%40ss%3Aword@db.refit.supabase.co:5432/postgres"
            },
            "refit",
        )
        self.assertEqual(env["PGPASSWORD"], "p@ss:word")


if __name__ == "__main__":
    unittest.main()
