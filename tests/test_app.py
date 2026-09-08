import unittest
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database import init_db, get_dashboard_kpis, save_report_and_findings


class AuditFollowupTrackerTests(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        init_db()

    def test_health_endpoint(self):
        res = self.app.get("/health")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["app"], "Audit Follow-up Tracker")

    def test_save_report_and_findings(self):
        report_data = {
            "title": "Auditoría de Prueba de Créditos",
            "process": "Gestión de Préstamos",
            "area": "Riesgo & Créditos",
            "period": "2025-2026",
            "auditor": "Equipo Auditor",
            "summary": "Resumen de prueba de la auditoría."
        }
        findings_data = [
            {
                "code": "OBS-01",
                "type": "Debilidad de Control",
                "process_step": "Otorgamiento",
                "title": "Diferencia de cuotas en sistema vs convenio",
                "situation": "Se observó discrepancia en cuotas registradas.",
                "risk": "Descalce en cobranzas.",
                "proposal": "Ajustar cuotas al convenio legal.",
                "severity": "Alta",
                "responsible_area": "Créditos",
                "action_owner": "Gerente de Créditos",
                "target_date": "2026-12-15",
                "status": "Pendiente"
            }
        ]

        report_id, count = save_report_and_findings(report_data, findings_data, "informe_test.docx")
        self.assertIsNotNone(report_id)
        self.assertEqual(count, 1)

        res = self.app.get(f"/reports/{report_id}")
        self.assertEqual(res.status_code, 200)
        rep_json = json.loads(res.data)
        self.assertEqual(rep_json["title"], "Auditoría de Prueba de Créditos")
        self.assertEqual(len(rep_json["findings"]), 1)

    def test_findings_list_and_filters(self):
        res = self.app.get("/findings")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("findings", data)

    def test_dashboard_stats(self):
        res = self.app.get("/dashboard-stats")
        self.assertEqual(res.status_code, 200)
        stats = json.loads(res.data)
        self.assertIn("total_reports", stats)
        self.assertIn("total_findings", stats)
        self.assertIn("resolution_rate", stats)

    def test_export_excel(self):
        res = self.app.post("/export-excel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    unittest.main()
