import unittest
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database import init_db, get_dashboard_stats, save_relational_report_structure, get_all_findings, get_all_proposals, get_all_action_plans


class AuditTrackRelationalTests(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        init_db()

    def test_health_endpoint(self):
        res = self.app.get("/health")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "ok")

    def test_relational_report_structure(self):
        report_data = {
            "title": "Auditoría de Prueba de Créditos",
            "process": "Gestión de Préstamos",
            "area": "Riesgo & Créditos",
            "period": "2026",
            "auditor": "Luciana Gamarra",
            "summary": "Resumen de prueba."
        }
        findings_hierarchy = [
            {
                "code": "H-2026-TEST",
                "title": "Diferencia de cuotas en sistema",
                "situation": "Se observó discrepancia en cuotas.",
                "risk": "Descalce en cobranzas.",
                "severity": "Alto",
                "responsible_area": "Créditos",
                "action_owner": "Gerente de Créditos",
                "status": "En proceso",
                "proposals": [
                    {
                        "code": "PM-2026-TEST",
                        "title": "Ajustar cuotas en sistema",
                        "proposal_text": "Parametrizar validación de cuotas.",
                        "target_date": "2026-12-15",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-TEST",
                                "title": "Acción de prueba",
                                "action_text": "Desarrollar script de validación",
                                "action_owner": "Sistemas",
                                "target_date": "2026-12-01",
                                "progress_pct": 40,
                                "status": "En proceso"
                            }
                        ]
                    }
                ]
            }
        ]

        report_id = save_relational_report_structure(report_data, findings_hierarchy, "informe_test.docx")
        self.assertIsNotNone(report_id)

        res = self.app.get(f"/reports/{report_id}")
        self.assertEqual(res.status_code, 200)
        rep_json = json.loads(res.data)
        self.assertEqual(rep_json["title"], "Auditoría de Prueba de Créditos")

    def test_dashboard_stats(self):
        res = self.app.get("/dashboard-stats")
        self.assertEqual(res.status_code, 200)
        stats = json.loads(res.data)
        self.assertIn("open_findings", stats)
        self.assertIn("impl_rate", stats)

    def test_kpi_indicators(self):
        res = self.app.get("/kpi-indicators")
        self.assertEqual(res.status_code, 200)
        kpis = json.loads(res.data)
        self.assertIn("indicators", kpis)


if __name__ == "__main__":
    unittest.main()
