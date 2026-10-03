import unittest
import os
import json
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_V13_PATH = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _TMP_V13_PATH

import database
from app import app, parse_excel_pct


class V13RegressionsTestCase(unittest.TestCase):

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        database.DB_PATH = self.db_path
        app.config["TESTING"] = True
        self.client = app.test_client()
        database.init_db()

        # Login as validator (admin)
        self.client.post("/login", json={"username": "admin", "password": "audit2026admin"})

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_excel_percentage_parsing_contract(self):
        self.assertEqual(parse_excel_pct("1%"), 1)
        self.assertEqual(parse_excel_pct("50%"), 50)
        self.assertEqual(parse_excel_pct("100%"), 100)
        self.assertEqual(parse_excel_pct(0.5), 50)
        self.assertEqual(parse_excel_pct(0.01), 1)
        self.assertEqual(parse_excel_pct("0.5"), 50)
        self.assertEqual(parse_excel_pct("1"), 1)
        self.assertEqual(parse_excel_pct("50"), 50)
        self.assertEqual(parse_excel_pct("100"), 100)
        self.assertEqual(parse_excel_pct(None), 0)
        self.assertEqual(parse_excel_pct("invalid"), 0)

    def test_validation_endpoint_aliases_and_response_contract(self):
        report_data = {
            "title": "Informe Validación Alias",
            "process": "Proceso Auditoría",
            "area": "Sistemas",
            "period": "2026",
            "auditor": "Auditoría Interna",
            "summary": "Verificación de contratos de endpoints de validación."
        }
        findings_hierarchy = [
            {
                "code": "H-2026-701",
                "title": "Configuración insegura",
                "situation": "Credenciales por defecto en servidor de aplicaciones",
                "severity": "Alto",
                "responsible_area": "Sistemas",
                "action_owner": "Carlos Gómez",
                "status": "En proceso",
                "proposals": [
                    {
                        "code": "PM-2026-701",
                        "title": "Cambiar credenciales",
                        "proposal_text": "Rotar contraseñas inmediatamente",
                        "target_date": "2026-12-31",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-701",
                                "title": "Ejecutar script de cambio de clave",
                                "action_text": "Ejecutar script de cambio de clave",
                                "action_owner": "Carlos Gómez",
                                "target_date": "2026-11-15",
                                "progress_pct": 100,
                                "status": "Pendiente de validación"
                            }
                        ]
                    }
                ]
            }
        ]
        rep_id = database.save_relational_report_structure(report_data, findings_hierarchy, "val_alias.docx")
        proposals = database.get_all_proposals()
        self.assertEqual(len(proposals), 1)
        prop_id = proposals[0]["id"]
        plans = database.get_all_action_plans()
        plan_id = plans[0]["id"]

        # 1. Validate proposal via /api/proposals/<id>/validate
        res_prop = self.client.post(f"/api/proposals/{prop_id}/validate")
        self.assertEqual(res_prop.status_code, 200)
        data_prop = res_prop.get_json()
        self.assertTrue(data_prop.get("success"))
        self.assertIn("message", data_prop)

        # 2. Validate action plan via /action-plans/<id>/validate
        res_plan = self.client.post(f"/action-plans/{plan_id}/validate")
        self.assertEqual(res_plan.status_code, 200)
        data_plan = res_plan.get_json()
        self.assertTrue(data_plan.get("success"))

    def test_auth_protection_on_all_data_and_export_routes(self):
        # Logout to test 401 unauthenticated access
        self.client.post("/logout")

        protected_urls = [
            "/export-excel",
            "/api/kpi-executive",
            "/kpi-indicators",
            "/dashboard-stats",
            "/api/notifications",
            "/download-template"
        ]

        for url in protected_urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 401, f"URL {url} should return 401 when unauthenticated")
            data = res.get_json()
            self.assertIn("error", data)

    def test_progress_100_transitions_to_pending_validation(self):
        report_data = {
            "title": "Informe Transición 100%",
            "process": "Proceso Riesgos",
            "area": "Operaciones",
            "period": "2026",
            "auditor": "Auditoría Interna",
            "summary": "Comprobación de estado pendiente de validación al 100%."
        }
        findings_hierarchy = [
            {
                "code": "H-2026-702",
                "title": "Falta de control dual",
                "situation": "Transferencias de alto monto aprobadas por un solo usuario",
                "severity": "Alto",
                "responsible_area": "Operaciones",
                "proposals": [
                    {
                        "code": "PM-2026-702",
                        "title": "Implementar firmas cruzadas",
                        "proposal_text": "Requerir doble firma para montos superiores a $1M",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-702",
                                "title": "Configurar perfil en core bancario",
                                "action_text": "Configurar perfil en core bancario",
                                "progress_pct": 50,
                                "status": "En proceso"
                            }
                        ]
                    }
                ]
            }
        ]
        database.save_relational_report_structure(report_data, findings_hierarchy, "trans_test.docx")
        plans = database.get_all_action_plans()
        plan_id = plans[0]["id"]

        # Update progress to 100% via generic update
        res = self.client.post(f"/action-plans/{plan_id}", json={"progress_pct": 100, "status": "Finalizado"})
        self.assertEqual(res.status_code, 200)

        updated_plan = database.get_all_action_plans()[0]
        # Should be Pendiente de validación, NOT Finalizado without formal validation
        self.assertEqual(updated_plan["status"], "Pendiente de validación")
        self.assertIsNone(updated_plan.get("validated_at"))

    def test_reopening_validated_item_invalidates_signature(self):
        report_data = {
            "title": "Informe Reapertura Validada",
            "process": "Proceso Calidad",
            "area": "Operaciones",
            "period": "2026",
            "auditor": "Auditoría Interna",
            "summary": "Verificación de invalidación de firmas al reabrir."
        }
        findings_hierarchy = [
            {
                "code": "H-2026-703",
                "title": "Procedimiento desactualizado",
                "situation": "Manual de compras data de 2018 sin revisiones",
                "severity": "Medio",
                "responsible_area": "Operaciones",
                "proposals": [
                    {
                        "code": "PM-2026-703",
                        "title": "Actualizar manual de compras",
                        "proposal_text": "Revisar y publicar versión 2026 del manual de compras",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-703",
                                "title": "Redactar borrador",
                                "action_text": "Redactar borrador",
                                "progress_pct": 100,
                                "status": "Pendiente de validación"
                            }
                        ]
                    }
                ]
            }
        ]
        database.save_relational_report_structure(report_data, findings_hierarchy, "reopen_test.docx")
        proposals = database.get_all_proposals()
        prop_id = proposals[0]["id"]

        # Validate proposal
        self.client.post(f"/api/proposals/{prop_id}/validate")
        val_prop = database.get_all_proposals()[0]
        self.assertEqual(val_prop["status"], "Finalizado")
        self.assertIsNotNone(val_prop["validated_at"])

        # Reopen action plan by reducing progress to 80%
        plan_id = val_prop["action_plans"][0]["id"]
        self.client.post(f"/action-plans/{plan_id}", json={"progress_pct": 80, "status": "En proceso"})

        reopened_prop = database.get_all_proposals()[0]
        self.assertEqual(reopened_prop["status"], "En proceso")
        self.assertIsNone(reopened_prop["validated_at"])
        self.assertIsNone(reopened_prop["validated_by"])


if __name__ == "__main__":
    unittest.main()
