import unittest
import os
import tempfile
from datetime import datetime, timedelta

# Isolated temporary database before database/app imports
self_temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
self_temp_db.close()
os.environ["DATABASE_PATH"] = self_temp_db.name

import database


class TestBlock2SequencesStatusesCascades(unittest.TestCase):

    def setUp(self):
        # Create fresh isolated database for each test
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        database.DB_PATH = self.tmp_db.name
        database.init_db()

    def tearDown(self):
        if os.path.exists(self.tmp_db.name):
            try:
                os.remove(self.tmp_db.name)
            except Exception:
                pass

    def test_code_sequences_generation_and_no_reuse_on_delete(self):
        """1. Secuencia de códigos: no reutiliza números al borrar registros y no genera duplicados."""
        conn = database.get_db()
        cursor = conn.cursor()

        # Generar report
        r_id = database.save_relational_report_structure(
            {"title": "Report 1", "process": "P1", "area": "A1"},
            [{"title": "H1", "situation": "Sit 1"}],
            "report1.docx"
        )
        f_list = database.get_all_findings()
        self.assertEqual(len(f_list), 1)
        code1 = f_list[0]["code"] # e.g. H-2026-001

        # Crear segundo hallazgo
        code2 = database.generate_next_code("finding") # H-2026-002
        cursor.execute("INSERT INTO findings (id, report_id, code, title, situation) VALUES (?, ?, ?, ?, ?)",
                       ("f2_id", r_id, code2, "H2", "Sit 2"))
        conn.commit()

        # Borrar el primer hallazgo (H-2026-001)
        database.delete_finding(f_list[0]["id"])

        # Generar siguiente código de hallazgo
        code3 = database.generate_next_code("finding")
        
        # Debe generar H-2026-003, NUNCA H-2026-001
        self.assertNotEqual(code3, code1)
        self.assertTrue(code3.endswith("-003"))
        conn.close()

    def test_code_sequences_sync_with_existing_max(self):
        """1. Secuencia de códigos: sincroniza secuencia con el máximo existente en base."""
        conn = database.get_db()
        cursor = conn.cursor()

        r_id = database.save_relational_report_structure(
            {"title": "Report X", "process": "P", "area": "A"},
            [],
            "r.docx"
        )
        # Insertar manualmente hallazgo con código alto (ej: H-2026-050)
        high_code = f"H-{datetime.now().year}-050"
        cursor.execute("INSERT INTO findings (id, report_id, code, title, situation) VALUES (?, ?, ?, ?, ?)",
                       ("f_high", r_id, high_code, "H High", "Sit"))
        conn.commit()

        # Ejecutar sincronización de secuencias
        database.sync_code_sequences(cursor)
        conn.commit()

        # Generar siguiente código
        next_code = database.generate_next_code("finding", cursor=cursor)
        self.assertEqual(next_code, f"H-{datetime.now().year}-051")
        conn.close()

    def test_persisted_vs_effective_status_and_filtering(self):
        """2. Estados: persiste solo 'En proceso', 'En suspensión' y 'Finalizado'. Genera 'Vencido' dinámicamente y filtra correctamente."""
        r_id = database.save_relational_report_structure(
            {"title": "Report St", "process": "P", "area": "A"},
            [{"title": "H1", "situation": "Sit"}],
            "r.docx"
        )
        findings = database.get_all_findings()
        f_id = findings[0]["id"]
        p_id, _ = database.create_proposal_for_finding(f_id, "Prop 1")

        # Crear plan vencido (fecha en el pasado)
        past_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        plan_vencido_id, _ = database.create_action_plan(p_id, "Acción Vencida", "Owner", past_date, status="En proceso")

        # Crear plan al día (fecha en el futuro)
        future_date = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        plan_aldia_id, _ = database.create_action_plan(p_id, "Acción Al Día", "Owner", future_date, status="En proceso")

        plans = database.get_all_action_plans()
        vencido_plan = [p for p in plans if p["id"] == plan_vencido_id][0]
        aldia_plan = [p for p in plans if p["id"] == plan_aldia_id][0]

        # Estado guardado debe ser 'En proceso'
        self.assertEqual(vencido_plan["status"], "En proceso")
        # Estado efectivo debe ser 'Vencido'
        self.assertEqual(vencido_plan["effective_status"], "Vencido")

        self.assertEqual(aldia_plan["status"], "En proceso")
        self.assertEqual(aldia_plan["effective_status"], "En proceso")

        # Probar filtrado por 'Vencido'
        vencidos = database.get_all_action_plans({"status": "Vencido"})
        self.assertEqual(len(vencidos), 1)
        self.assertEqual(vencidos[0]["id"], plan_vencido_id)

    def test_progress_100_unconfirmed_stays_in_process_with_pending_validation(self):
        """3. Avance 100% sin confirmación: mantiene 'En proceso', 'closed_date = None' y marca 'Pendiente de validación'."""
        r_id = database.save_relational_report_structure(
            {"title": "R", "process": "P", "area": "A"},
            [{"title": "H1", "situation": "Sit"}],
            "r.docx"
        )
        f_id = database.get_all_findings()[0]["id"]
        p_id, _ = database.create_proposal_for_finding(f_id, "Prop 1")
        pa_id, _ = database.create_action_plan(p_id, "Acción 1", "Owner", "2026-12-31")

        # Actualizar a 100% SIN confirmación
        updated = database.update_action_plan(pa_id, progress_pct=100, confirm_finalize=False)
        self.assertTrue(updated)

        plans = database.get_all_action_plans()
        plan = [p for p in plans if p["id"] == pa_id][0]

        self.assertEqual(plan["status"], "En proceso")
        self.assertEqual(plan["progress_pct"], 100)
        self.assertIsNone(plan["closed_date"])
        self.assertIn("Pendiente de validación", plan["notes"])

    def test_progress_100_confirmed_finalizes_and_sets_closed_date(self):
        """3. Avance 100% confirmado: pasa a 'Finalizado' y registra 'closed_date'."""
        r_id = database.save_relational_report_structure(
            {"title": "R", "process": "P", "area": "A"},
            [{"title": "H1", "situation": "Sit"}],
            "r.docx"
        )
        f_id = database.get_all_findings()[0]["id"]
        p_id, _ = database.create_proposal_for_finding(f_id, "Prop 1")
        pa_id, _ = database.create_action_plan(p_id, "Acción 1", "Owner", "2026-12-31")

        # Actualizar a 100% CON confirmación
        updated = database.update_action_plan(pa_id, progress_pct=100, confirm_finalize=True)
        self.assertTrue(updated)

        plans = database.get_all_action_plans()
        plan = [p for p in plans if p["id"] == pa_id][0]

        self.assertEqual(plan["status"], "Finalizado")
        self.assertEqual(plan["progress_pct"], 100)
        self.assertEqual(plan["closed_date"], datetime.now().strftime("%Y-%m-%d"))

    def test_reopen_clears_closed_date(self):
        """3. Reapertura: limpia la fecha de cierre (closed_date = None)."""
        r_id = database.save_relational_report_structure(
            {"title": "R", "process": "P", "area": "A"},
            [{"title": "H1", "situation": "Sit"}],
            "r.docx"
        )
        f_id = database.get_all_findings()[0]["id"]
        p_id, _ = database.create_proposal_for_finding(f_id, "Prop 1")
        pa_id, _ = database.create_action_plan(p_id, "Acción 1", "Owner", "2026-12-31")

        # Finalizar
        database.update_action_plan(pa_id, status="Finalizado", confirm_finalize=True)
        
        # Reabrir
        database.update_action_plan(pa_id, status="En proceso", progress_pct=80)

        plans = database.get_all_action_plans()
        plan = [p for p in plans if p["id"] == pa_id][0]

        self.assertEqual(plan["status"], "En proceso")
        self.assertIsNone(plan["closed_date"])

    def test_ascending_cascades_and_reopen_rules(self):
        """4. Cascadas: finalización y reapertura en jerarquía Acción -> Propuesta -> Hallazgo."""
        r_id = database.save_relational_report_structure(
            {"title": "R", "process": "P", "area": "A"},
            [{"title": "H1", "situation": "Sit"}],
            "r.docx"
        )
        f_id = database.get_all_findings()[0]["id"]
        p_id, _ = database.create_proposal_for_finding(f_id, "Prop 1")

        pa1_id, _ = database.create_action_plan(p_id, "Acción 1", "Owner", "2026-12-31")
        pa2_id, _ = database.create_action_plan(p_id, "Acción 2", "Owner", "2026-12-31")

        # Finalizar sólo Acción 1 -> Propuesta y Hallazgo deben seguir 'En proceso'
        database.update_action_plan(pa1_id, status="Finalizado", confirm_finalize=True)
        prop = database.get_all_proposals()[0]
        finding = database.get_all_findings()[0]
        self.assertEqual(prop["status"], "En proceso")
        self.assertEqual(finding["status"], "En proceso")

        # Finalizar Acción 2 -> Propuesta y Hallazgo deben pasar a 'Finalizado'
        database.update_action_plan(pa2_id, status="Finalizado", confirm_finalize=True)
        prop = database.get_all_proposals()[0]
        finding = database.get_all_findings()[0]
        self.assertEqual(prop["status"], "Finalizado")
        self.assertEqual(finding["status"], "Finalizado")

        # Reabrir Acción 1 -> Propuesta y Hallazgo reabren a 'En proceso'
        database.update_action_plan(pa1_id, status="En proceso", progress_pct=50)
        prop = database.get_all_proposals()[0]
        finding = database.get_all_findings()[0]
        self.assertEqual(prop["status"], "En proceso")
        self.assertEqual(finding["status"], "En proceso")

    def test_manual_suspension_respected(self):
        """4. Suspensión manual: no es sobrescrita por evaluación de cascadas."""
        r_id = database.save_relational_report_structure(
            {"title": "R", "process": "P", "area": "A"},
            [{"title": "H1", "situation": "Sit"}],
            "r.docx"
        )
        f_id = database.get_all_findings()[0]["id"]
        p_id, _ = database.create_proposal_for_finding(f_id, "Prop 1")
        pa1_id, _ = database.create_action_plan(p_id, "Acción 1", "Owner", "2026-12-31")

        # Suspender la Propuesta manualmente
        database.update_proposal(p_id, {"status": "En suspensión"})
        prop = database.get_all_proposals()[0]
        self.assertEqual(prop["status"], "En suspensión")

        # Finalizar el plan de acción hijo
        database.update_action_plan(pa1_id, status="Finalizado", confirm_finalize=True)

        # La Propuesta debe PERMANECER en 'En suspensión'
        prop = database.get_all_proposals()[0]
        self.assertEqual(prop["status"], "En suspensión")

    def test_no_children_entities_do_not_auto_finalize(self):
        """4. Entidades sin hijos: no se finalizan automáticamente."""
        conn = database.get_db()
        cursor = conn.cursor()

        r_id = database.save_relational_report_structure(
            {"title": "R", "process": "P", "area": "A"},
            [],
            "r.docx"
        )
        # Crear hallazgo sin propuestas
        f_code = database.generate_next_code("finding", cursor=cursor)
        cursor.execute("INSERT INTO findings (id, report_id, code, title, situation, status) VALUES (?, ?, ?, ?, ?, ?)",
                       ("f_childless", r_id, f_code, "Childless Finding", "Sit", "En proceso"))
        conn.commit()

        database._evaluate_finding_cascade(cursor, "f_childless")
        conn.commit()

        finding = database.get_finding_detail("f_childless")
        self.assertEqual(finding["status"], "En proceso")
        conn.close()


if __name__ == "__main__":
    unittest.main()
