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

    def test_create_action_plan_via_finding_id(self):
        report_data = {
            "title": "Auditoría Operativa",
            "process": "Operaciones",
            "area": "Logística",
            "period": "2026",
            "auditor": "Auditoría Interna",
            "summary": "Prueba de vinculación directa."
        }
        findings = [
            {
                "title": "Inconsistencia en inventario de almacén",
                "situation": "Descalce de stock físico vs sistema.",
                "severity": "Medio",
                "responsible_area": "Logística",
                "action_owner": "Jefe de Almacén",
                "status": "En proceso",
                "proposals": []
            }
        ]
        rep_id = save_relational_report_structure(report_data, findings, "logistica.docx")
        all_f = get_all_findings()
        f_target = [f for f in all_f if f["report_id"] == rep_id][0]

        res = self.app.post("/action-plans", json={
            "finding_id": f_target["id"],
            "action_text": "Realizar inventario cíclico quincenal",
            "action_owner": "Jefe de Almacén",
            "target_date": "2026-11-15"
        })
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("Plan de Acción", data["message"])

    def test_global_proposals_extraction_and_linkage(self):
        doc_text = """
Informe de Auditoría Interna 2026

Hallazgo 9: Inconsistencia en saldos bancarios
Se detectaron diferencias en las conciliaciones de fin de mes.

Hallazgo N° 10: Falta de arqueos de caja periódicos
No se realizan arqueos de caja sorpresivos en sucursales.

Observación 11: Deficiencias en custodia de valores
Las llaves de las cajas fuertes no tienen control de acceso.

7. PROPUESTAS DE MEJORA:
1. Fortalecer los controles de traspasos y conciliaciones para los Hallazgos 9, 10 y 11.
2. Actualizar el manual de procedimientos generales.
"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write(doc_text)
            tmp_path = f.name

        try:
            parsed = parse_audit_report(tmp_path, "informe_test.txt")
            findings = parsed.get("findings", [])
            proposals = parsed.get("proposals", [])

            # 7. La cantidad de hallazgos no cambia.
            self.assertEqual(len(findings), 3)

            # 2. Hallazgo 9 conserva source_number = 9.
            self.assertEqual(findings[0]["source_number"], 9)
            self.assertEqual(findings[1]["source_number"], 10)
            self.assertEqual(findings[2]["source_number"], 11)

            # 6. Una propuesta multihallazgo permanece como una sola entidad lógica.
            self.assertEqual(len(proposals), 2)

            # 3. Una propuesta que dice Hallazgos 9, 10 y 11 queda asociada a esos tres números.
            p1 = proposals[0]
            self.assertEqual(p1["number"], 1)
            self.assertEqual(p1["finding_numbers"], [9, 10, 11])
            self.assertEqual(p1["link_status"], "linked")

            # 4. Una propuesta sin referencia queda unlinked.
            p2 = proposals[1]
            self.assertEqual(p2["number"], 2)
            self.assertEqual(p2["finding_numbers"], [])
            self.assertEqual(p2["link_status"], "unlinked")

            # 5. La propuesta número 2 (o 5) no se asigna automáticamente al Hallazgo 2/10.
            self.assertNotIn(2, p2["finding_numbers"])
            self.assertNotIn(10, p2["finding_numbers"])

            # Los hallazgos 9, 10 y 11 la referencian mediante proposal_numbers = [1]
            self.assertEqual(findings[0]["proposal_numbers"], [1])
            self.assertEqual(findings[1]["proposal_numbers"], [1])
            self.assertEqual(findings[2]["proposal_numbers"], [1])

            # No generar UUID ni códigos PM-... ni finding_id en propuestas del parser
            for p in proposals:
                self.assertNotIn("id", p)
                self.assertNotIn("code", p)
                self.assertNotIn("finding_id", p)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_proposal_number_5_not_auto_assigned_to_finding_5(self):
        doc_text = """
Informe de Auditoría
Hallazgo 5: Falla de control
Propuestas de Mejora
5. Revisar procesos generales.
"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write(doc_text)
            tmp_path = f.name

        try:
            parsed = parse_audit_report(tmp_path, "test_p5.txt")
            findings = parsed.get("findings", [])
            proposals = parsed.get("proposals", [])

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["source_number"], 5)

            self.assertEqual(len(proposals), 1)
            p5 = proposals[0]
            self.assertEqual(p5["number"], 5)

            # 5. La propuesta número 5 no se asigna automáticamente al Hallazgo 5
            self.assertEqual(p5["finding_numbers"], [])
            self.assertEqual(p5["link_status"], "unlinked")
            self.assertEqual(findings[0]["proposal_numbers"], [])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_tabular_route_preserves_global_proposals(self):
        tabular_text = """
Informe de Auditoría Tabular 2026

| Código | Hallazgo | Riesgo |
| H-001 | Diferencias en conciliación bancaria | Alto |

7. PROPUESTAS DE MEJORA
1. Implementar software de conciliación automática.
"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write(tabular_text)
            tmp_path = f.name

        try:
            parsed = parse_audit_report(tmp_path, "tabular_test.txt")
            proposals = parsed.get("proposals", [])
            # 1. La ruta tabular conserva global_proposals.
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0]["number"], 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()


