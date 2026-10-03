import unittest
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
import database

class TestV14ExecutiveDashboard(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def login_admin(self):
        return self.app.post("/login", json={"username": "admin", "password": "audit2026admin"})

    def test_health_version_v14(self):
        res = self.app.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["version"], "v1.4.0")
        self.assertEqual(data["base_tag"], "v1.0.0-base-2026-10-02")
        self.assertFalse(data["ai_enabled"])

    def test_unauthenticated_executive_api_returns_401(self):
        res = self.app.get("/api/kpi-executive")
        self.assertEqual(res.status_code, 401)

        res_drill = self.app.get("/api/kpi-executive/drilldown?metric=high_risk_open")
        self.assertEqual(res_drill.status_code, 401)

    def test_executive_dashboard_api_structure(self):
        self.login_admin()
        res = self.app.get("/api/kpi-executive")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertTrue(data.get("success"))
        self.assertIn("cut_date", data)
        self.assertIn("scope", data)
        self.assertIn("kpis", data)
        self.assertIn("charts", data)
        self.assertIn("agenda", data)

        # Check Scope structure
        scope = data["scope"]
        self.assertIn("reports", scope)
        self.assertIn("findings", scope)
        self.assertIn("proposals", scope)
        self.assertIn("plans", scope)

        # Check KPIs structure
        kpis = data["kpis"]
        self.assertIn("high_risk_open", kpis)
        self.assertIn("overdue_commitments", kpis)
        self.assertIn("pending_validation", kpis)
        self.assertIn("validated_implementation", kpis)
        self.assertIn("on_time_closing", kpis)

        # Check Charts structure
        charts = data["charts"]
        self.assertIn("by_area", charts)
        self.assertIn("aging", charts)
        self.assertIn("1_30", charts["aging"])
        self.assertIn("31_60", charts["aging"])
        self.assertIn(">60", charts["aging"])

        # Check Agenda structure
        agenda = data["agenda"]
        self.assertIsInstance(agenda, list)
        if len(agenda) > 0:
            item = agenda[0]
            self.assertIn("description", item)
            self.assertIn("risk_level", item)
            self.assertIn("overdue_days", item)
            self.assertIn("status", item)
            self.assertIn("next_action", item)

    def test_executive_dashboard_filtering(self):
        self.login_admin()
        # Fetch reports list to get a valid report_id
        res_reports = self.app.get("/reports")
        reports = res_reports.get_json().get("reports", [])

        if reports:
            rep_id = reports[0]["id"]
            res_filtered = self.app.get(f"/api/kpi-executive?report_id={rep_id}")
            self.assertEqual(res_filtered.status_code, 200)
            data = res_filtered.get_json()
            self.assertTrue(data["success"])
            self.assertLessEqual(data["scope"]["reports"], len(reports))

    def test_executive_drilldown_metrics(self):
        self.login_admin()
        metrics = [
            "high_risk_open",
            "overdue_commitments",
            "pending_validation",
            "validated_implementation",
            "on_time_closing"
        ]
        for m in metrics:
            res = self.app.get(f"/api/kpi-executive/drilldown?metric={m}")
            self.assertEqual(res.status_code, 200, f"Drilldown failed for metric {m}")
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("metric"), m)
            self.assertIsInstance(data.get("records"), list)

    def test_deterministic_next_action_rules(self):
        # Verify deterministic rules in database helper without calling AI
        self.login_admin()
        res = self.app.get("/api/kpi-executive")
        data = res.get_json()
        agenda = data.get("agenda", [])

        for item in agenda:
            status = item["status"]
            next_act = item["next_action"]
            overdue = item["overdue_days"]

            if status == "Pendiente de validación":
                self.assertIn("Validación formal", next_act)
            elif status == "En proceso" and overdue > 0:
                self.assertIn("Seguimiento plan vencido", next_act)

if __name__ == "__main__":
    unittest.main()
