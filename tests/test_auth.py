"""카카오 HTTP 검증 및 실제 FastAPI 인증 계약. 외부 DB/카카오 호출 없음."""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.errors import ApiError
from app.main import app
from app.services import auth, db, rate_limit


class KakaoVerificationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(settings, "kakao_app_id", 123456).start()
        self.client = AsyncMock()
        self.factory = patch("app.services.auth.httpx.AsyncClient").start()
        self.factory.return_value.__aenter__.return_value = self.client

    def verify(self, status=200, body=None):
        self.client.get.return_value = httpx.Response(
            status,
            json=(
                body
                if body is not None
                else {"id": 9876543210, "app_id": 123456, "expires_in": 300}
            ),
        )
        return asyncio.run(auth.verify_kakao_token("synthetic-kakao-token"))

    def test_checks_provider_and_exact_app(self):
        result = self.verify()
        self.assertEqual(result.id, 9876543210)
        self.client.get.assert_awaited_once_with(
            auth.KAKAO_TOKEN_INFO_URL,
            headers={"Authorization": "Bearer synthetic-kakao-token"},
        )
        self.assertFalse(self.factory.call_args.kwargs["follow_redirects"])

    def test_wrong_app_expired_and_invalid_tokens(self):
        for status, body in (
            (200, {"id": 1, "app_id": 999, "expires_in": 300}),
            (200, {"id": 1, "app_id": 123456, "expires_in": 0}),
            (401, {"code": -401}),
            (400, {"code": -2}),
        ):
            with self.subTest(status=status, body=body), self.assertRaises(ApiError) as caught:
                self.verify(status, body)
            self.assertEqual(caught.exception.status_code, 401)

    def test_provider_faults_are_retryable_not_invalid_credentials(self):
        for status, body in (
            (400, {"code": -1}),
            (429, {}),
            (500, {}),
            (302, {}),
            (200, {}),
            (200, {"id": True, "app_id": 123456, "expires_in": 300}),
        ):
            with self.subTest(status=status, body=body), self.assertRaises(ApiError) as caught:
                self.verify(status, body)
            self.assertEqual(caught.exception.status_code, 503)

    def test_network_error_does_not_expose_token(self):
        self.client.get.side_effect = httpx.ReadTimeout("secret-provider-token")
        with self.assertRaises(ApiError) as caught:
            self.verify()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("secret-provider-token", str(caught.exception.to_dict()))

    def test_missing_config_fails_closed_without_network(self):
        with patch.object(settings, "kakao_app_id", None), self.assertRaises(ApiError) as caught:
            self.verify()
        self.assertEqual(caught.exception.code, "AUTH_NOT_CONFIGURED")
        self.client.get.assert_not_awaited()


class MemoryAuthStore:
    """HTTP 계약용 저장소. SQL의 만료·동시성·권한은 실제 PostgreSQL로 별도 검증."""

    def __init__(self):
        self.users = {}
        self.identities = {}
        self.sessions = {}

    def rpc(self, name, args):
        def execute():
            if name == "refit_kakao_login":
                identity = (args["p_app_id"], args["p_subject"])
                user_id = self.identities.setdefault(identity, str(uuid4()))
                now = datetime.now(timezone.utc)
                user = self.users.setdefault(
                    user_id,
                    {
                        "user_id": user_id,
                        "is_pro_user": False,
                        "created_at": now.isoformat(),
                    },
                )
                expires_at = now + timedelta(seconds=args["p_ttl_seconds"])
                self.sessions[args["p_token_hash"]] = (user_id, expires_at)
                return SimpleNamespace(data=[{**user, "expires_at": expires_at.isoformat()}])
            if name == "refit_auth_session":
                session = self.sessions.get(args["p_token_hash"])
                valid = session and session[1] > datetime.now(timezone.utc)
                return SimpleNamespace(data=[{"user_id": session[0]}] if valid else [])
            raise AssertionError(f"Unexpected RPC: {name}")

        return SimpleNamespace(execute=execute)

    def table(self, name):
        assert name == "auth_session"

        def eq(key, value):
            assert key == "token_hash"
            return SimpleNamespace(execute=lambda: self.sessions.pop(value, None))

        return SimpleNamespace(delete=lambda: SimpleNamespace(eq=eq))


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.store = MemoryAuthStore()
        patch.object(db, "get_client", return_value=self.store).start()
        patch.object(db, "get_user", side_effect=lambda uid: self.store.users.get(str(uid))).start()
        self.verify = patch.object(auth, "verify_kakao_token", new_callable=AsyncMock).start()
        self.verify.return_value = auth.KakaoTokenInfo(id=123, app_id=123456, expires_in=300)
        rate_limit._hits.clear()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def login(self):
        response = self.client.post("/api/v1/auth/kakao", json={"access_token": "kakao-test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def bearer(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_login_reuses_user_and_stores_only_hashes(self):
        first, second = self.login(), self.login()
        self.assertEqual(first["user"]["user_id"], second["user"]["user_id"])
        self.assertNotEqual(first["access_token"], second["access_token"])
        self.assertNotIn(first["access_token"], self.store.sessions)
        self.assertIn(auth.token_hash(first["access_token"]), self.store.sessions)
        response = self.client.get("/api/v1/users/me", headers=self.bearer(first["access_token"]))
        self.assertEqual(response.json(), first["user"])

    def test_uuid_cannot_bypass_auth_and_cannot_override_token_owner(self):
        first = self.login()
        for headers in (
            {},
            {"X-User-Id": first["user"]["user_id"]},
            self.bearer("kakao-test"),
            self.bearer("refit_" + "A" * 43),
        ):
            response = self.client.get("/api/v1/users/me", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
        response = self.client.get(
            "/api/v1/users/me",
            headers={
                **self.bearer(first["access_token"]),
                "X-User-Id": str(uuid4()),
            },
        )
        self.assertEqual(response.json()["user_id"], first["user"]["user_id"])

    def test_no_unauthenticated_user_creation(self):
        response = self.client.post("/api/v1/users")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.store.users, {})
        login = self.login()
        response = self.client.post("/api/v1/users", headers=self.bearer(login["access_token"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.store.users), 1)

    def test_logout_revokes_current_token_only_and_preserves_account(self):
        first, second = self.login(), self.login()
        headers = self.bearer(first["access_token"])
        response = self.client.post("/api/v1/auth/logout", headers=headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.client.get("/api/v1/users/me", headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=headers).status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/v1/users/me", headers=self.bearer(second["access_token"])
            ).status_code,
            200,
        )
        self.assertEqual(len(self.store.users), 1)

    def test_expired_token_rejected(self):
        login = self.login()
        digest = auth.token_hash(login["access_token"])
        self.store.sessions[digest] = (
            login["user"]["user_id"],
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        response = self.client.get("/api/v1/users/me", headers=self.bearer(login["access_token"]))
        self.assertEqual(response.status_code, 401)

    def test_cross_user_photo_access_still_rejected(self):
        login = self.login()
        session_id = str(uuid4())
        with patch.object(
            db, "get_session", return_value={"session_id": session_id, "user_id": str(uuid4())}
        ):
            response = self.client.get(
                f"/api/v1/sessions/{session_id}/photos/user",
                headers=self.bearer(login["access_token"]),
            )
        self.assertEqual(response.status_code, 404)

    def test_database_outage_fails_closed(self):
        login = self.login()
        with patch.object(db, "get_client", side_effect=RuntimeError("secret-db-value")):
            response = self.client.get(
                "/api/v1/users/me", headers=self.bearer(login["access_token"])
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-db-value", response.text)

    def test_validation_does_not_echo_credentials_or_accept_user_ids(self):
        for payload in (
            {"access_token": "secret\r\nvalue"},
            {"access_token": "x" * 4097},
            {"access_token": "secret-token", "user_id": str(uuid4())},
            {"access_token": {"nested": "secret-token"}},
            {},
        ):
            response = self.client.post("/api/v1/auth/kakao", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertNotIn("secret", response.text)
            self.assertNotIn('"input"', response.text)
        self.verify.assert_not_awaited()

    def test_rate_limit_runs_before_provider_request(self):
        with patch.object(settings, "auth_login_rate_limit", 1):
            self.login()
            response = self.client.post("/api/v1/auth/kakao", json={"access_token": "kakao-test"})
        self.assertEqual(response.status_code, 429)
        self.verify.assert_awaited_once()

    def test_swagger_bearer_and_error_contracts(self):
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        self.assertEqual(schema["components"]["securitySchemes"]["RefitBearer"]["scheme"], "bearer")
        self.assertFalse(paths["/api/v1/auth/kakao"]["post"].get("security"))
        for route, method in (
            ("/api/v1/users/me", "get"),
            ("/api/v1/sessions/{session_id}/photos/user", "post"),
            ("/api/v1/auth/logout", "post"),
        ):
            operation = paths[route][method]
            self.assertEqual(operation["security"], [{"RefitBearer": []}])
            self.assertIn("401", operation["responses"])
            self.assertFalse(any(p["name"] == "X-User-Id" for p in operation.get("parameters", [])))
        self.assertTrue(paths["/api/v1/users"]["post"]["deprecated"])

        # 로컬 JSON 참조가 모두 해석되는지 확인한다.
        def check(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    target = schema
                    for part in node["$ref"][2:].split("/"):
                        target = target[part]
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)

        check(schema)


if __name__ == "__main__":
    unittest.main()
