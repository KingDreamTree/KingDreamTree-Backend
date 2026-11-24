"""Exercise the deployment checker against HTTP responses from a fake server."""

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class DeploymentDocsTests(unittest.TestCase):
    def setUp(self):
        self.health = {"status": "ok", "db": "ok"}
        self.schema = {
            "openapi": "3.1.0",
            "info": {"version": "0.2.0+expected-commit"},
            "paths": {"/api/v1/users": {}},
            "components": {"schemas": {"User": {"type": "object"}}},
        }
        self.docs = 'SwaggerUIBundle({url: "/openapi.json"})'
        self.health_requests = 0
        self.fail_first_health = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    owner.health_requests += 1
                    if owner.fail_first_health and owner.health_requests == 1:
                        self.send_response(503)
                        self.end_headers()
                        return
                    content = json.dumps(owner.health)
                elif self.path == "/openapi.json":
                    content = json.dumps(owner.schema)
                elif self.path == "/docs":
                    content = owner.docs
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(content.encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def check(self, attempts=1):
        script = Path(__file__).resolve().parents[1] / "scripts" / "check_api_docs.py"
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "--base-url",
                f"http://127.0.0.1:{self.server.server_port}",
                "--expected-revision",
                "expected-commit",
                "--attempts",
                str(attempts),
                "--delay",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_healthy_deployment(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_revision_is_not_success(self):
        self.schema["info"]["version"] = "0.2.0+old-commit"
        self.assertNotEqual(self.check().returncode, 0)

    def test_database_failure_with_http_200_is_not_success(self):
        self.health = {"status": "degraded", "db": "unreachable"}
        self.assertNotEqual(self.check().returncode, 0)

    def test_wrong_docs_page_is_not_success(self):
        self.docs = "<html>Proxy placeholder</html>"
        self.assertNotEqual(self.check().returncode, 0)

    def test_retry_during_server_startup(self):
        self.fail_first_health = True
        result = self.check(attempts=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.health_requests, 2)


if __name__ == "__main__":
    unittest.main()
