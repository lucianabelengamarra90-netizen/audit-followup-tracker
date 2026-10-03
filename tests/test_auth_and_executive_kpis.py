import unittest
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from domain.auth import ROLE_READER, ROLE_EDITOR, ROLE_VALIDATOR

class AuthAndExecutiveKPITests(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def login_as_admin(self):
        return self.app.post("/login", json={"username": "admin", "password": "audit2026admin"})

    def test_health_endpoint(self):
        res = self.app.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["version"], "v1.3.0")
        self.assertEqual(data["base_tag"], "v1.0.0-base-2026-10-02")
        self.assertFalse(data["ai_enabled"])

    def test_unauthenticated_api_returns_401(self):
        res = self.app.get("/findings")
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Acceso no autorizado. Inicie sesión.")

    def test_user_session_and_login(self):
        # Default unauthenticated user session
        self.app.post("/logout")
        res = self.app.get("/api/user")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIsNone(data["user"])

        # Login as reader
        login_res = self.app.post("/login", json={"username": "lector", "password": "audit2026reader"})
        self.assertEqual(login_res.status_code, 200)
        login_data = login_res.get_json()
        self.assertEqual(login_data["user"]["role"], ROLE_READER)

        # Login as validator
        login_res = self.app.post("/login", json={"username": "admin", "password": "audit2026admin"})
        self.assertEqual(login_res.status_code, 200)
        login_data = login_res.get_json()
        self.assertEqual(login_data["user"]["role"], ROLE_VALIDATOR)

    def test_executive_kpis_endpoint(self):
        self.login_as_admin()
        res = self.app.get("/api/kpi-executive")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("kpis", data)
        kpis = data["kpis"]
        self.assertIn("high_risk_open", kpis)
        self.assertIn("overdue_commitments", kpis)
        self.assertIn("validated_implementation", kpis)
        self.assertIn("on_time_closure", kpis)
        self.assertIn("pending_validation", kpis)

    def test_production_db_fails_if_no_database_url(self):
        import database
        original_env = os.environ.get("STRICT_POSTGRES")
        original_db_url = os.environ.get("DATABASE_URL")
        try:
            os.environ["STRICT_POSTGRES"] = "true"
            if "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]
            with self.assertRaises(RuntimeError) as ctx:
                database.get_db()
            self.assertIn("DATABASE_URL is not set", str(ctx.exception))
        finally:
            if original_env is not None:
                os.environ["STRICT_POSTGRES"] = original_env
            else:
                os.environ.pop("STRICT_POSTGRES", None)
            if original_db_url is not None:
                os.environ["DATABASE_URL"] = original_db_url

if __name__ == "__main__":
    unittest.main()
