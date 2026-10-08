import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import database as db


class ReopeningAndAgendaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tmp.name) / 'test.db')
        db.init_db()
        with db.get_db() as conn:
            conn.execute("INSERT INTO reports (id,code,title,process,area,period) VALUES ('r','R','Report','Audit','Legal','2026')")

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def add(self, key, severity='Alto', days=10, status='En proceso', pct=0):
        target = (db.get_argentina_today() - timedelta(days=days)).isoformat() if days is not None else None
        with db.get_db() as conn:
            conn.execute("INSERT INTO findings (id,report_id,code,title,situation,severity,responsible_area,status) VALUES (?,'r',?,'Finding','Situation',?,'Legal',?)", (key,key,severity,status))
            conn.execute("INSERT INTO proposals (id,finding_id,code,title,proposal_text,status,responsible_area) VALUES (?,?,?,'Proposal','Proposal',?,'Legal')", (key,key,key,status))
            conn.execute("INSERT INTO action_plans (id,proposal_id,code,title,action_text,status,progress_pct,target_date,validated_at,validated_by,closed_date) VALUES (?,?,?,'Plan','Plan',?,?,?,'2026-01-01','Auditor','2026-01-01')", (key,key,key,status,pct,target))

    def assert_reopened(self, key):
        with db.get_db() as conn:
            row = conn.execute('SELECT * FROM action_plans WHERE id=?', (key,)).fetchone()
        self.assertEqual(row['status'], 'En proceso')
        self.assertEqual(row['progress_pct'], 0)
        for field in ('validated_at', 'validated_by', 'closed_date'):
            self.assertIsNone(row[field])

    def test_plan_reopen_ignores_stale_100_from_modal(self):
        self.add('a', status='Finalizado', pct=100)
        db.update_action_plan('a', status='En proceso', progress_pct=100)
        self.assert_reopened('a')

    def test_parent_reopen_resets_children_and_validation(self):
        for key, update in [('f', db.update_finding), ('p', db.update_proposal)]:
            self.add(key, status='Finalizado', pct=100)
            db.validate_proposal(key)
            update(key, {'status': 'En proceso'})
            self.assert_reopened(key)
            with db.get_db() as conn:
                row = conn.execute('SELECT validated_at,validated_by FROM proposals WHERE id=?', (key,)).fetchone()
            self.assertEqual(tuple(row), (None,None))

    def test_partial_progress_is_preserved(self):
        self.add('a', pct=45)
        db.update_action_plan('a', status='En proceso', notes='Follow up')
        with db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT progress_pct FROM action_plans WHERE id='a'").fetchone()[0],45)

    def test_agenda_only_overdue_sorted_by_risk_then_age(self):
        self.add('low', severity='Bajo', days=90)
        self.add('high_new', severity='ALTA', days=5)
        self.add('high_old', days=20)
        self.add('medium', severity='Medio', days=40)
        self.add('future_validation', days=-5, status='Pendiente de validación', pct=100)
        self.add('no_date', days=None, status='Pendiente de validación', pct=100)
        self.add('today', days=0)
        self.add('closed', status='Finalizado', pct=100)
        self.add('suspended', status='En suspensión')
        result = db.get_executive_kpis({'area':'Legal'})
        self.assertEqual([r['plan_id'] for r in result['agenda']], ['high_old','high_new','medium','low'])
        self.assertEqual(result['kpis']['overdue_commitments']['count'],4)
        self.assertEqual(db.get_executive_kpis({'area':'Other'})['agenda'],[])

    def test_state_chart_counts_finished_proposals_without_formal_validation(self):
        self.add('closed1', status='Finalizado', pct=100)
        self.add('closed2', status='Completada', pct=100)
        self.add('pending', status='Pendiente de validación', pct=100)
        with db.get_db() as conn:
            conn.execute("INSERT INTO action_plans (id,code,proposal_id,title,action_text,status) VALUES ('extra','extra','pending','Extra','Extra','En proceso')")
        result = db.get_executive_kpis({'report_id':'r'})
        self.assertEqual(result['charts']['proposal_states']['Finalizado'], 2)
        self.assertEqual(result['charts']['proposal_states']['Pendiente de validación'], 1)
        self.assertEqual(sum(result['charts']['proposal_states'].values()), 3)
        self.assertEqual(result['kpis']['validated_implementation']['count'], 0)
        self.assertEqual(db.get_dashboard_stats()['status_breakdown']['Finalizado'], 2)
        self.assertEqual(db.get_all_proposals()[0]['report_id'], 'r')
        self.assertEqual(sum(db.get_executive_kpis({'area':'Other'})['charts']['proposal_states'].values()), 0)


if __name__ == '__main__':
    unittest.main()
