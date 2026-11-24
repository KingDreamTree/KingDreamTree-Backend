"""Check generated API docs locally, or verify an EC2 deployment over HTTPS.

Local checks never call business endpoints or /health (which accesses Supabase).
Remote checks use only the standard library, so CI needs no API dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen


def verify_docs(schema: dict, docs: str, expected_revision: str | None) -> None:
    if not schema.get("openapi", "").startswith("3."):
        raise ValueError("Expected an OpenAPI 3 document")
    if not schema.get("paths") or not schema.get("components", {}).get("schemas"):
        raise ValueError("OpenAPI is missing routes or request/response schemas")
    if "/api/v1/users" not in schema["paths"]:
        raise ValueError("The API prefix or user route is missing")
    if "SwaggerUIBundle" not in docs or "/openapi.json" not in docs:
        raise ValueError("Swagger UI is missing or points to a different schema")
    if expected_revision and not schema["info"]["version"].endswith(f"+{expected_revision}"):
        raise ValueError("OpenAPI does not describe the expected deployment revision")


def local_docs() -> tuple[dict, str]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        schema_response = client.get("/openapi.json")
        schema_response.raise_for_status()
        docs_response = client.get("/docs")
        docs_response.raise_for_status()
        return schema_response.json(), docs_response.text


def remote_docs(base_url: str) -> tuple[dict, str]:
    def read(path: str) -> str:
        with urlopen(f"{base_url.rstrip('/')}{path}", timeout=10) as response:
            return response.read().decode("utf-8")

    health = json.loads(read("/health"))
    # /health returns HTTP 200 even when Supabase is unavailable.
    if health.get("status") != "ok" or health.get("db") != "ok":
        raise ValueError("API health check reports an unavailable database")
    return json.loads(read("/openapi.json")), read("/docs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Omit to check the local FastAPI application")
    parser.add_argument("--expected-revision")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--delay", type=float, default=10)
    args = parser.parse_args()
    if args.attempts < 1 or args.delay < 0:
        parser.error("attempts must be positive and delay must be nonnegative")

    for attempt in range(1, args.attempts + 1):
        try:
            schema, docs = remote_docs(args.base_url) if args.base_url else local_docs()
            verify_docs(schema, docs, args.expected_revision)
            break
        except Exception as exc:
            print(f"API docs check {attempt}/{args.attempts} failed: {exc}", file=sys.stderr)
            if attempt == args.attempts:
                raise SystemExit(1) from exc
            time.sleep(args.delay)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        f"Swagger OK; OpenAPI {schema['openapi']}; {len(schema['paths'])} paths; version {schema['info']['version']}"
    )


if __name__ == "__main__":
    main()
