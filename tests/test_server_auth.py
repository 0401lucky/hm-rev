import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from hm_api.server import build_app


class AdminAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cwd = os.getcwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        self._tmpdir.cleanup()

    def test_admin_routes_require_unlock_when_api_key_enabled(self) -> None:
        with TestClient(build_app(api_key="secret")) as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("控制台已锁定", page.text)

            status = client.get("/api/status")
            self.assertEqual(status.status_code, 401)

    def test_admin_unlock_sets_cookie_session(self) -> None:
        with TestClient(build_app(api_key="secret")) as client:
            failed = client.post(
                "/api/admin/unlock",
                data={"key": "wrong"},
                follow_redirects=False,
            )
            self.assertEqual(failed.status_code, 401)

            unlocked = client.post(
                "/api/admin/unlock",
                data={"key": "secret"},
                follow_redirects=False,
            )
            self.assertEqual(unlocked.status_code, 303)
            self.assertIn("hm_api_admin", unlocked.headers.get("set-cookie", ""))

            status = client.get("/api/status")
            self.assertEqual(status.status_code, 200)
            self.assertIn("logged_in", status.json())

    def test_admin_api_accepts_bearer_key_for_bridge(self) -> None:
        with TestClient(build_app(api_key="secret")) as client:
            status = client.get(
                "/api/status",
                headers={"Authorization": "Bearer secret"},
            )

            self.assertEqual(status.status_code, 200)
            self.assertIn("logged_in", status.json())

    def test_v1_routes_still_require_bearer_key(self) -> None:
        with TestClient(build_app(api_key="secret")) as client:
            unauthorized = client.get("/v1/models")
            self.assertEqual(unauthorized.status_code, 401)

            authorized = client.get(
                "/v1/models",
                headers={"Authorization": "Bearer secret"},
            )
            self.assertEqual(authorized.status_code, 401)
            self.assertEqual(authorized.json()["detail"], "Not logged in")


if __name__ == "__main__":
    unittest.main()
