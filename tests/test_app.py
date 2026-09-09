import unittest
import json
import os
import sys
import io
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database import init_db, save_relational_report_structure, get_all_findings, get_all_proposals
from report_parser import parse_audit_report, extract_raw_text_from_file


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

    def test_invalid_file_extension(self):
        data = {'file': (io.BytesIO(b"binary content"), "test.exe")}
        res = self.app.post("/parse-preview", data=data, content_type='multipart/form-data')
        self.assertEqual(res.status_code, 400)
        json_data = json.loads(res.data)
        self.assertIn("error", json_data)

    def test_empty_file_upload(self):
        data = {'file': (io.BytesIO(b""), "empty.txt")}
        res = self.app.post("/parse-preview", data=data, content_type='multipart/form-data')
        self.assertEqual(res.status_code, 422)

    def test_csv_extraction_and_delimiter_sniffer(self):
        csv_content = "Hallazgo;Propuesta;Area;Riesgo\nFalta de firma en autorizaciones;Implementar firma digital;Operaciones;Alto\n"
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", encoding="utf-8") as f:
            f.write(csv_content)
            tmp_path = f.name

        try:
            raw_text = extract_raw_text_from_file(tmp_path)
            self.assertIn("Falta de firma", raw_text)
            self.assertIn("Implementar firma digital", raw_text)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_multi_proposals_preservation(self):
        report_data = {
            "title": "Auditoría de Seguridad e Infraestructura",
            "process": "Tecnología",
            "area": "Sistemas",
            "period": "2026",
            "auditor": "Auditoría Interna",
            "summary": "Múltiples propuestas asociadas."
        }
        findings_hierarchy = [
            {
                "title": "Falta de control de acceso a servidores",
                "situation": "Servidores expuestos sin MFA.",
                "risk": "Riesgo de intrusión no autorizada.",
                "severity": "Alto",
                "responsible_area": "Sistemas",
                "action_owner": "CISO",
                "status": "Pendiente",
                "proposals": [
                    {
                        "code": "PM-2026-001",
                        "title": "Implementar MFA para todos los administradores",
                        "proposal_text": "Exigir token MFA en accesos SSH.",
                        "status": "Pendiente"
                    },
                    {
                        "code": "PM-2026-002",
                        "title": "Restringir IPs de origen en Firewall",
                        "proposal_text": "Configurar VPN corporativa exclusiva.",
                        "status": "Pendiente"
                    }
                ]
            }
        ]

        report_id = save_relational_report_structure(report_data, findings_hierarchy, "informe_seguridad.docx")
        self.assertIsNotNone(report_id)

        findings = get_all_findings()
        matched = [f for f in findings if f["report_id"] == report_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(matched[0]["proposals"]), 2)

    def test_traceability_fields(self):
        txt_content = "Hallazgo 1: Falta de conciliación bancaria mensual.\nRecomendación: Realizar cierre y conciliación de cuentas antes del día 5 de cada mes.\n"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write(txt_content)
            tmp_path = f.name

        try:
            parsed = parse_audit_report(tmp_path, "conciliaciones.txt")
            findings = parsed.get("findings", [])
            self.assertGreater(len(findings), 0)
            f0 = findings[0]
            self.assertIn("id", f0)
            self.assertIn("sourceItemId", f0)
            self.assertIn("sourceKey", f0)
            self.assertEqual(f0["selectedAsFinding"], True)
            self.assertEqual(f0["converted"], False)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
