import unittest
import os
import sys
import tempfile
import sqlite3
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set isolated DATABASE_PATH prior to importing app or database
_TMP_DB_PATH = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _TMP_DB_PATH

import database
from app import app


class Block1FinalizationAndRollbackTests(unittest.TestCase):

    def setUp(self):
        self.tmp_fd, self.tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)
        os.environ["DATABASE_PATH"] = self.tmp_path
        database.DB_PATH = self.tmp_path
        database.init_db()
        self.client = app.test_client()
        self.client.testing = True

    def tearDown(self):
        if os.path.exists(self.tmp_path):
            try:
                os.remove(self.tmp_path)
            except Exception:
                pass

    def test_successful_finding_finalization(self):
        # 1. Setup sample hierarchy
        report_data = {
            "title": "Informe de Prueba Bloque 1",
            "process": "Control Interno",
            "area": "Auditoría",
            "period": "2026",
            "auditor": "Auditor Test",
            "summary": "Verificación de finalización exitosa de hallazgos."
        }
        findings_data = [
            {
                "code": "H-2026-B1",
                "title": "Hallazgo Bloque 1",
                "situation": "Prueba de actualización a Finalizado",
                "severity": "Medio",
                "responsible_area": "Operaciones",
                "action_owner": "Juan Pérez",
                "status": "En proceso",
                "proposals": [
                    {
                        "code": "PM-2026-B1",
                        "title": "Propuesta B1",
                        "proposal_text": "Texto propuesta B1",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-B1",
                                "title": "Acción B1",
                                "action_text": "Texto acción B1",
                                "action_owner": "Juan Pérez",
                                "status": "En proceso",
                                "progress_pct": 20
                            }
                        ]
                    }
                ]
            }
        ]

        report_id = database.save_relational_report_structure(report_data, findings_data, "test_b1.docx")
        self.assertIsNotNone(report_id)

        # 2. Update finding to Finalizado via update_finding
        updated = database.update_finding("H-2026-B1", {"status": "Finalizado"}, user_name="Auditor B1")
        self.assertTrue(updated)

        # 3. Assert finding, proposal, and action plan statuses
        finding = database.get_finding_detail("H-2026-B1")
        self.assertEqual(finding["status"], "Finalizado")

        proposals = database.get_all_proposals()
        matched_p = [p for p in proposals if p["finding_code"] == "H-2026-B1"]
        self.assertEqual(len(matched_p), 1)
        self.assertEqual(matched_p[0]["status"], "Finalizado")

        plans = database.get_all_action_plans()
        matched_pa = [pa for pa in plans if pa["finding_code"] == "H-2026-B1"]
        self.assertEqual(len(matched_pa), 1)
        self.assertEqual(matched_pa[0]["status"], "Finalizado")
        self.assertEqual(matched_pa[0]["progress_pct"], 100)

        # 4. Perform an immediate write operation to ensure DB is active & not locked
        next_code = database.generate_next_code("finding", 2026)
        self.assertEqual(next_code, "H-2026-001")

    def test_controlled_failure_rollback_and_no_db_lock_inside_real_function(self):
        # 1. Setup sample hierarchy
        report_data = {
            "title": "Informe de Prueba Fallo Controlado",
            "process": "Riesgos",
            "area": "Seguridad",
            "period": "2026",
            "auditor": "Auditor Test",
            "summary": "Verificación de error dentro de la función real de actualización."
        }
        findings_data = [
            {
                "code": "H-2026-FAIL",
                "title": "Hallazgo Pruebas Fallo",
                "situation": "Prueba de error controlado dentro de update_finding",
                "severity": "Alto",
                "responsible_area": "Sistemas",
                "action_owner": "Maria Gomez",
                "status": "En proceso"
            }
        ]

        report_id = database.save_relational_report_structure(report_data, findings_data, "test_fail.docx")
        self.assertIsNotNone(report_id)

        # 2. Force an exception INSIDE database.update_finding after SQL execution to trigger try/except/rollback/finally
        with patch("database.add_history_log", side_effect=sqlite3.OperationalError("Error simulado dentro de update_finding")):
            with self.assertRaises(sqlite3.OperationalError):
                # Call the real update_finding function directly
                database.update_finding("H-2026-FAIL", {"status": "Finalizado"})

        # 3. Verify update_finding executed rollback() and no partial changes were saved
        finding = database.get_finding_detail("H-2026-FAIL")
        self.assertEqual(finding["status"], "En proceso")

        # 4. Verify update_finding executed finally: conn.close() releasing the connection
        # Perform an immediate write update using the real update_finding function
        write_success = database.update_finding("H-2026-FAIL", {"title": "Título actualizado post rollback"})
        self.assertTrue(write_success)

        updated_finding = database.get_finding_detail("H-2026-FAIL")
        self.assertEqual(updated_finding["title"], "Título actualizado post rollback")


if __name__ == "__main__":
    unittest.main()
