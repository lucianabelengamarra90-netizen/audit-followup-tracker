import unittest
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
import database
import import_engine

class TestV15FinalStabilization(unittest.TestCase):
    def setUp(self):
        self.tmp_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        database.DB_PATH = self.db_path
        database.init_db(force=True)
        self.app = app.test_client()
        self.app.testing = True

    def tearDown(self):
        os.close(self.tmp_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def login_admin(self):
        return self.app.post("/login", json={"username": "admin", "password": "audit2026admin"})

    def test_cross_report_finding_modification_rejected(self):
        """Verifica que un hallazgo con código H-2026-001 de Informe A NO modifique Informe A al reimportar Informe B."""
        repA_id = database.save_relational_report_structure(
            {"title": "Informe A", "process": "P", "area": "Area A"},
            [{"title": "Hallazgo A", "situation": "Sit A", "code": "H-2026-001"}],
            "informeA.xlsx", mode="new"
        )
        repB_id = database.save_relational_report_structure(
            {"title": "Informe B", "process": "P", "area": "Area B"},
            [{"title": "Hallazgo B", "situation": "Sit B", "code": "H-2026-002"}],
            "informeB.xlsx", mode="new"
        )

        res = import_engine.import_report_structure(
            report_data={"title": "Informe B Modificado"},
            findings=[{"title": "Intento de sobreescribir A", "situation": "Sit Mod", "code": "H-2026-001"}],
            source_filename="informeB.xlsx",
            mode="merge",
            target_report_id=repB_id
        )

        self.assertGreater(len(res["conflicts"]), 0)
        self.assertIn("pertenece al informe", res["conflicts"][0])

        conn = database.get_db()
        cur = conn.cursor()
        cur.execute("SELECT situation FROM findings WHERE id = (SELECT id FROM findings WHERE code = 'H-2026-001')")
        sit = cur.fetchone()[0]
        conn.close()
        self.assertEqual(sit, "Sit A")

    def test_save_validated_report_rejects_errors(self):
        """Verifica que /save-validated-report rechace el guardado si hay errores de validación de relaciones."""
        self.login_admin()
        res = self.app.post("/save-validated-report", json={
            "report": {"title": "Test Error Guardado"},
            "findings": [{"title": "H1", "situation": "Sit 1"}],
            "errors": ["Hoja 'Planes de Acción', Fila 4: El código de propuesta 'PM-999' no existe."]
        })
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    def test_save_validated_report_requires_decision(self):
        """Verifica que /save-validated-report solicite decisión (409) cuando mode='auto' y existen candidatos."""
        self.login_admin()
        database.save_relational_report_structure(
            {"title": "Informe Existente", "process": "P", "area": "Operaciones"},
            [{"title": "H1", "situation": "Situacion repetida"}],
            "informe.xlsx", mode="new"
        )

        res = self.app.post("/save-validated-report", json={
            "report": {"title": "Informe Existente", "process": "P", "area": "Operaciones"},
            "findings": [{"code": "H-2026-001", "title": "H1", "situation": "Situacion repetida con mod"}]
        })
        self.assertEqual(res.status_code, 409)
        data = res.get_json()
        self.assertTrue(data.get("requires_decision"))


if __name__ == "__main__":
    unittest.main()
