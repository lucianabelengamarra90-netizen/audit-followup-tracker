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

    def test_health_endpoint(self):
        res = self.app.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["version"], "v1.1.0")
        self.assertEqual(data["base_tag"], "v1.0.0-base-2026-10-02")
        self.assertEqual(data["commit"], "cea1a03")
        self.assertFalse(data["ai_enabled"])

    def test_user_session_and_login(self):
        # Default user
        res = self.app.get("/api/user")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("role", data["user"])

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

if __name__ == "__main__":
    unittest.main()
