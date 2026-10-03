import unittest
import os
import tempfile
import json
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from backup_postgres import export_database, restore_and_verify_isolated


class PostgresBackupAndRestoreTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)
        os.environ["DATABASE_PATH"] = self.db_path
        database.DB_PATH = self.db_path
        database.init_db()

        # Insert test relational data
        report_data = {
            "title": "Informe de Prueba Respaldo Postgres",
            "process": "Proceso Auditoría",
            "area": "Sistemas",
            "period": "2026",
            "auditor": "Auditoría Interna",
            "summary": "Respaldo y verificación de restauración."
        }
        findings_hierarchy = [
            {
                "code": "H-2026-801",
                "title": "Acceso no restringido",
                "situation": "Servidor de base de datos expuesto sin autenticación",
                "severity": "Alto",
                "responsible_area": "Sistemas",
                "action_owner": "Auditoría",
                "status": "En proceso",
                "proposals": [
                    {
                        "code": "PM-2026-801",
                        "title": "Configurar firewall y autenticación",
                        "proposal_text": "Implementar políticas de autenticación y cerrar puertos",
                        "target_date": "2026-12-31",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-801",
                                "title": "Cerrar puerto DB",
                                "action_text": "Cerrar puerto 5432 público",
                                "action_owner": "Administrador DB",
                                "target_date": "2026-11-15",
                                "progress_pct": 100,
                                "status": "Pendiente de validación"
                            }
                        ]
                    }
                ]
            }
        ]
        database.save_relational_report_structure(report_data, findings_hierarchy, "backup_test.docx")

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_export_and_restore_cycle(self):
        backup_prefix = os.path.join(tempfile.gettempdir(), "test_backup_v12")
        json_file = export_database(output_prefix=backup_prefix)
        self.assertTrue(os.path.exists(json_file))

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("tables", data)
        tables = data["tables"]
        self.assertIn("reports", tables)
        self.assertIn("findings", tables)
        self.assertIn("proposals", tables)
        self.assertIn("action_plans", tables)

        self.assertGreaterEqual(len(tables["reports"]), 1)
        self.assertGreaterEqual(len(tables["findings"]), 1)
        self.assertGreaterEqual(len(tables["proposals"]), 1)
        self.assertGreaterEqual(len(tables["action_plans"]), 1)

        # Verify restoration into an isolated db file
        isolated_db = os.path.join(tempfile.gettempdir(), "isolated_restore_test.db")
        result = restore_and_verify_isolated(json_file, target_db_path=isolated_db)
        self.assertTrue(result)

        conn = sqlite3.connect(isolated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM action_plans WHERE progress_pct = 100")
        cnt = cursor.fetchone()[0]
        self.assertEqual(cnt, 1)
        conn.close()

        if os.path.exists(json_file):
            os.remove(json_file)
        if os.path.exists(isolated_db):
            os.remove(isolated_db)


if __name__ == "__main__":
    unittest.main()
