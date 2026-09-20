import unittest
import os
import sys
import tempfile
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.statuses import normalize_status, is_final_status, compute_effective_status, get_status_badge_class
from domain.dates import parse_date_to_iso, format_display_date, is_date_past


class DomainAndMigrationsTests(unittest.TestCase):

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db_path = self.tmp_db.name
        self.tmp_db.close()
        os.environ["DATABASE_PATH"] = self.tmp_db_path
        
        # Import database AFTER setting environment variable
        import database
        database.DB_PATH = self.tmp_db_path
        database.init_db()
        self.database = database

    def tearDown(self):
        if os.path.exists(self.tmp_db_path):
            try:
                os.remove(self.tmp_db_path)
            except Exception:
                pass

    def test_status_normalization_and_variants(self):
        self.assertEqual(normalize_status("Completado"), "Finalizado")
        self.assertEqual(normalize_status("completada"), "Finalizado")
        self.assertEqual(normalize_status("Cerrado"), "Finalizado")
        self.assertEqual(normalize_status("Implementada"), "Finalizado")
        self.assertEqual(normalize_status("En suspensión"), "En suspensión")
        self.assertEqual(normalize_status("stand-by"), "En suspensión")
        self.assertEqual(normalize_status("En proceso"), "En proceso")
        self.assertEqual(normalize_status("Pendiente"), "En proceso")
        self.assertEqual(normalize_status(None), "En proceso")

    def test_effective_status_computation(self):
        past_date = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        future_date = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")

        # 1. En proceso con fecha pasada -> Vencido
        self.assertEqual(compute_effective_status("En proceso", past_date), "Vencido")

        # 2. En proceso con fecha futura -> En proceso
        self.assertEqual(compute_effective_status("En proceso", future_date), "En proceso")

        # 3. En proceso sin fecha -> En proceso (nunca Vencido)
        self.assertEqual(compute_effective_status("En proceso", None), "En proceso")
        self.assertEqual(compute_effective_status("En proceso", ""), "En proceso")

        # 4. En suspensión -> En suspensión (incluso con fecha pasada)
        self.assertEqual(compute_effective_status("En suspensión", past_date), "En suspensión")

        # 5. Finalizado -> Finalizado (incluso con fecha pasada)
        self.assertEqual(compute_effective_status("Finalizado", past_date), "Finalizado")

    def test_date_parsing_and_formatting(self):
        self.assertEqual(parse_date_to_iso("2026-09-30"), "2026-09-30")
        self.assertEqual(parse_date_to_iso("30/09/2026"), "2026-09-30")
        self.assertIsNone(parse_date_to_iso("invalid-date"))
        self.assertIsNone(parse_date_to_iso(""))
        self.assertIsNone(parse_date_to_iso(None))

        self.assertEqual(format_display_date("2026-09-30"), "30/09/2026")
        self.assertEqual(format_display_date("30/09/2026"), "30/09/2026")
        self.assertEqual(format_display_date(None), "Sin fecha")
        self.assertEqual(format_display_date(""), "Sin fecha")

    def test_persistent_code_generation(self):
        code1 = self.database.generate_next_code("report", 2026)
        code2 = self.database.generate_next_code("report", 2026)
        self.assertEqual(code1, "AUD-2026-001")
        self.assertEqual(code2, "AUD-2026-002")

        h_code1 = self.database.generate_next_code("finding", 2026)
        self.assertEqual(h_code1, "H-2026-001")


if __name__ == "__main__":
    unittest.main()
