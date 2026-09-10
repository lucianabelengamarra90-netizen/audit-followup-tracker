import sqlite3
import os
import uuid
from datetime import datetime, date, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_tracker.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Verify if old schema exists
    cursor.execute("PRAGMA table_info(reports)")
    cols = [r["name"] for r in cursor.fetchall()]
    if cols and "code" not in cols:
        cursor.execute("DROP TABLE IF EXISTS findings")
        cursor.execute("DROP TABLE IF EXISTS reports")
        cursor.execute("DROP TABLE IF EXISTS proposals")
        cursor.execute("DROP TABLE IF EXISTS action_plans")
        cursor.execute("DROP TABLE IF EXISTS audit_history")
        conn.commit()

    # 1. Informes (Registro Padre)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            process TEXT NOT NULL,
            area TEXT,
            period TEXT,
            auditor TEXT,
            summary TEXT,
            source_filename TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Hallazgos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            situation TEXT NOT NULL,
            risk TEXT,
            severity TEXT DEFAULT 'Medio',
            responsible_area TEXT,
            action_owner TEXT,
            status TEXT DEFAULT 'Pendiente',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            observations TEXT DEFAULT '',
            FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE
        )
    """)

    # Migrate: add observations column if missing in existing databases
    cursor.execute("PRAGMA table_info(findings)")
    finding_cols = [r['name'] for r in cursor.fetchall()]
    if finding_cols and 'observations' not in finding_cols:
        cursor.execute("ALTER TABLE findings ADD COLUMN observations TEXT DEFAULT ''")
        conn.commit()

    # 3. Propuestas de Mejora
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY,
            finding_id TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            proposal_text TEXT NOT NULL,
            severity TEXT DEFAULT 'Medio',
            responsible_area TEXT,
            action_owner TEXT,
            target_date TEXT,
            status TEXT DEFAULT 'Pendiente',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (finding_id) REFERENCES findings (id) ON DELETE CASCADE
        )
    """)

    # 4. Planes de Acción
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS action_plans (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            action_text TEXT NOT NULL,
            action_owner TEXT,
            target_date TEXT,
            status TEXT DEFAULT 'Pendiente',
            progress_pct INTEGER DEFAULT 0,
            notes TEXT,
            evidence_file TEXT,
            closed_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (proposal_id) REFERENCES proposals (id) ON DELETE CASCADE
        )
    """)

    # 5. Historial de Cambios & Trazabilidad
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_history (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            change_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_name TEXT DEFAULT 'Auditoría Interna',
            description TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def add_history_log(entity_type, entity_id, user_name, description, cursor=None):
    close_at_end = False
    if cursor is None:
        conn = get_db()
        cursor = conn.cursor()
        close_at_end = True

    cursor.execute("""
        INSERT INTO audit_history (id, entity_type, entity_id, user_name, description)
        VALUES (?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), entity_type, entity_id, user_name or "Auditoría Interna", description))

    if close_at_end:
        cursor.connection.commit()
        cursor.connection.close()


def get_history_logs(entity_type=None, entity_id=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT * FROM audit_history WHERE 1=1"
    params = []
    if entity_type:
        query += " AND entity_type = ?"
        params.append(entity_type)
    if entity_id:
        query += " AND entity_id = ?"
        params.append(entity_id)
    query += " ORDER BY change_date DESC"
    cursor.execute(query, params)
    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return logs


def save_relational_report_structure(report_data, findings_hierarchy, source_filename=""):
    """
    Guarda la relación Informe -> Hallazgos -> Propuestas -> Planes.

    Reglas:
    - No inventa riesgo si no viene informado.
    - No inventa fecha compromiso.
    - No inventa planes de acción.
    - Una propuesta puede existir sin plan.
    - Un hallazgo puede tener múltiples propuestas.
    """
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    report_id = str(uuid.uuid4())
    rep_idx_cursor = conn.cursor()
    rep_idx_cursor.execute("SELECT COUNT(*) FROM reports")
    rep_num = rep_idx_cursor.fetchone()[0] + 1
    rep_code = f"AUD-2026-{rep_num:03d}"

    title = (report_data.get("title") or f"Informe de Auditoría {rep_code}").strip()
    process = (report_data.get("process") or "Proceso General").strip()
    area = (report_data.get("area") or "Operaciones").strip()
    period = (report_data.get("period") or "2026").strip()
    auditor = (report_data.get("auditor") or "Auditoría Interna").strip()
    summary = (report_data.get("summary") or "").strip()

    cursor.execute("""
        INSERT INTO reports (id, code, title, process, area, period, auditor, summary, source_filename)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (report_id, rep_code, title, process, area, period, auditor, summary, source_filename))

    add_history_log("report", report_id, auditor, f"Carga inicial de informe {title} ({source_filename})", cursor=cursor)

    f_c_cursor = conn.cursor()
    f_c_cursor.execute("SELECT COUNT(*) FROM findings")
    finding_counter = f_c_cursor.fetchone()[0] + 1

    p_c_cursor = conn.cursor()
    p_c_cursor.execute("SELECT COUNT(*) FROM proposals")
    proposal_counter = p_c_cursor.fetchone()[0] + 1

    pa_c_cursor = conn.cursor()
    pa_c_cursor.execute("SELECT COUNT(*) FROM action_plans")
    action_counter = pa_c_cursor.fetchone()[0] + 1


    for f_item in findings_hierarchy:
        finding_id = str(uuid.uuid4())
        raw_f_code = f_item.get("code") or f"H-2026-{finding_counter:03d}"
        finding_counter += 1

        chk_f = conn.cursor()
        chk_f.execute("SELECT COUNT(*) FROM findings WHERE code = ?", (raw_f_code,))
        if chk_f.fetchone()[0] > 0:
            f_code = f"{raw_f_code}-{finding_counter:03d}"
        else:
            f_code = raw_f_code

        f_title = (f_item.get("title") or "Observación de Auditoría").strip()
        situation = (f_item.get("situation") or f_title).strip()
        risk = (f_item.get("risk") or "").strip()
        severity = (f_item.get("severity") or "Medio").strip()
        if severity.lower() in ("alto", "alta"):
            severity = "Alto"
        elif severity.lower() in ("bajo", "baja"):
            severity = "Bajo"
        else:
            severity = "Medio"

        responsible_area = (f_item.get("responsible_area") or area).strip()
        action_owner = (f_item.get("action_owner") or "Pendiente de definir").strip()
        status = (f_item.get("status") or "Pendiente").strip()

        cursor.execute("""
            INSERT INTO findings (id, report_id, code, title, situation, risk, severity, responsible_area, action_owner, status, observations)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (finding_id, report_id, f_code, f_title, situation, risk, severity, responsible_area, action_owner, status, ""))

        add_history_log("finding", finding_id, auditor, f"Creación de hallazgo {f_code}: {f_title}", cursor=cursor)

        # Propuestas asociadas a este Hallazgo
        proposals_list = f_item.get("proposals") or []
        if not proposals_list and f_item.get("proposal"):
            proposals_list = [{"title": f_item.get("title"), "proposal_text": f_item.get("proposal")}]

        for p_item in proposals_list:
            proposal_id = str(uuid.uuid4())
            raw_p_code = p_item.get("code") or f"PM-2026-{proposal_counter:03d}"
            proposal_counter += 1

            chk_p = conn.cursor()
            chk_p.execute("SELECT COUNT(*) FROM proposals WHERE code = ?", (raw_p_code,))
            if chk_p.fetchone()[0] > 0:
                p_code = f"{raw_p_code}-{proposal_counter:03d}"
            else:
                p_code = raw_p_code

            p_title = (p_item.get("title") or f"Propuesta para {f_title}").strip()
            p_text = (p_item.get("proposal_text") or p_item.get("proposal") or p_title).strip()
            p_target_date = (p_item.get("target_date") or "").strip()
            p_status = (p_item.get("status") or "Pendiente").strip()

            cursor.execute("""
                INSERT INTO proposals (id, finding_id, code, title, proposal_text, severity, responsible_area, action_owner, target_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (proposal_id, finding_id, p_code, p_title, p_text, severity, responsible_area, action_owner, p_target_date, p_status))

            add_history_log("proposal", proposal_id, auditor, f"Creación de propuesta {p_code} vinculada a {f_code}", cursor=cursor)

            plans_list = p_item.get("action_plans") or []

            for pa_item in plans_list:
                plan_id = str(uuid.uuid4())
                raw_pa_code = pa_item.get("code") or f"PA-2026-{action_counter:03d}"
                action_counter += 1

                chk_pa = conn.cursor()
                chk_pa.execute("SELECT COUNT(*) FROM action_plans WHERE code = ?", (raw_pa_code,))
                if chk_pa.fetchone()[0] > 0:
                    pa_code = f"{raw_pa_code}-{action_counter:03d}"
                else:
                    pa_code = raw_pa_code

                pa_title = (pa_item.get("title") or f"Acción para {p_code}").strip()
                pa_text = (pa_item.get("action_text") or pa_title).strip()
                pa_owner = (pa_item.get("action_owner") or action_owner).strip()
                pa_date = (pa_item.get("target_date") or p_target_date).strip()
                pa_pct = int(pa_item.get("progress_pct") or 0)
                pa_status = (pa_item.get("status") or "Pendiente").strip()
                pa_notes = (pa_item.get("notes") or "").strip()
                pa_evidence = (pa_item.get("evidence_file") or "").strip()

                cursor.execute("""
                    INSERT INTO action_plans (id, proposal_id, code, title, action_text, action_owner, target_date, status, progress_pct, notes, evidence_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (plan_id, proposal_id, pa_code, pa_title, pa_text, pa_owner, pa_date, pa_status, pa_pct, pa_notes, pa_evidence))

                add_history_log("action_plan", plan_id, pa_owner, f"Creación de plan de acción {pa_code} vinculado a propuesta {p_code}", cursor=cursor)

    conn.commit()
    conn.close()
    return report_id


def get_all_reports():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports ORDER BY created_at DESC")
    reports = [dict(r) for r in cursor.fetchall()]

    for rep in reports:
        rep_id = rep["id"]
        cursor.execute("SELECT COUNT(*) FROM findings WHERE report_id = ?", (rep_id,))
        rep["findings_count"] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM proposals p
            JOIN findings f ON p.finding_id = f.id
            WHERE f.report_id = ?
        """, (rep_id,))
        rep["proposals_count"] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            WHERE f.report_id = ?
        """, (rep_id,))
        rep["action_plans_count"] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            WHERE f.report_id = ? AND LOWER(pa.status) != 'completado' AND LOWER(pa.status) != 'cerrado'
        """, (rep_id,))
        rep["pending_plans_count"] = cursor.fetchone()[0]

    conn.close()
    return reports


def get_report_detail(report_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports WHERE id = ? OR code = ?", (report_id, report_id))
    r = cursor.fetchone()
    if not r:
        conn.close()
        return None

    rep = dict(r)
    rep_id = rep["id"]

    # Findings
    cursor.execute("SELECT * FROM findings WHERE report_id = ? ORDER BY code ASC", (rep_id,))
    findings = [dict(f) for f in cursor.fetchall()]

    for f in findings:
        f_id = f["id"]
        cursor.execute("SELECT * FROM proposals WHERE finding_id = ? ORDER BY code ASC", (f_id,))
        props = [dict(p) for p in cursor.fetchall()]
        for p in props:
            p_id = p["id"]
            cursor.execute("SELECT * FROM action_plans WHERE proposal_id = ? ORDER BY code ASC", (p_id,))
            p["action_plans"] = [dict(pa) for pa in cursor.fetchall()]
        f["proposals"] = props

    rep["findings"] = findings

    # History logs for report
    cursor.execute("SELECT * FROM audit_history WHERE entity_type = 'report' AND entity_id = ? ORDER BY change_date DESC", (rep_id,))
    rep["history"] = [dict(h) for h in cursor.fetchall()]

    conn.close()
    return rep


def get_all_findings(filters=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT f.*, r.code as report_code, r.title as report_title, r.source_filename
        FROM findings f
        JOIN reports r ON f.report_id = r.id
        WHERE 1=1
    """
    params = []
    if filters:
        if filters.get("status"):
            query += " AND LOWER(f.status) = LOWER(?)"
            params.append(filters["status"])
        if filters.get("severity"):
            query += " AND LOWER(f.severity) = LOWER(?)"
            params.append(filters["severity"])
        if filters.get("area"):
            query += " AND LOWER(f.responsible_area) LIKE ?"
            params.append(f"%{filters['area'].lower()}%")
        if filters.get("search"):
            q = f"%{filters['search'].lower()}%"
            query += " AND (LOWER(f.title) LIKE ? OR LOWER(f.situation) LIKE ? OR LOWER(f.code) LIKE ? OR LOWER(f.responsible_area) LIKE ?)"
            params.extend([q, q, q, q])

    query += " ORDER BY f.code ASC"
    cursor.execute(query, params)
    findings = [dict(row) for row in cursor.fetchall()]

    # Nest proposals and action plans for total traceability
    for f in findings:
        f_id = f["id"]
        cursor.execute("SELECT * FROM proposals WHERE finding_id = ? ORDER BY code ASC", (f_id,))
        proposals = [dict(p) for p in cursor.fetchall()]

        for p in proposals:
            p_id = p["id"]
            cursor.execute("SELECT * FROM action_plans WHERE proposal_id = ? ORDER BY code ASC", (p_id,))
            p["action_plans"] = [dict(pa) for pa in cursor.fetchall()]

        f["proposals"] = proposals
        f["proposals_count"] = len(proposals)
        f["action_plans_count"] = sum(len(p["action_plans"]) for p in proposals)

    conn.close()
    return findings


def get_finding_detail(finding_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.*, r.code as report_code, r.title as report_title, r.source_filename, r.auditor, r.period
        FROM findings f
        JOIN reports r ON f.report_id = r.id
        WHERE f.id = ? OR f.code = ?
    """, (finding_id, finding_id))
    f_row = cursor.fetchone()
    if not f_row:
        conn.close()
        return None

    f = dict(f_row)
    f_id = f["id"]

    cursor.execute("SELECT * FROM proposals WHERE finding_id = ? ORDER BY code ASC", (f_id,))
    proposals = [dict(p) for p in cursor.fetchall()]

    all_evidence = []
    for p in proposals:
        p_id = p["id"]
        cursor.execute("SELECT * FROM action_plans WHERE proposal_id = ? ORDER BY code ASC", (p_id,))
        plans = [dict(pa) for pa in cursor.fetchall()]
        for pa in plans:
            if pa.get("evidence_file"):
                all_evidence.append({
                    "plan_code": pa["code"],
                    "filename": pa["evidence_file"],
                    "date": pa["last_updated"]
                })
        p["action_plans"] = plans

    f["proposals"] = proposals
    f["evidence_files"] = all_evidence

    # History timeline
    cursor.execute("""
        SELECT * FROM audit_history
        WHERE (entity_type = 'finding' AND entity_id = ?)
           OR (entity_type = 'proposal' AND entity_id IN (SELECT id FROM proposals WHERE finding_id = ?))
           OR (entity_type = 'action_plan' AND entity_id IN (SELECT pa.id FROM action_plans pa JOIN proposals p ON pa.proposal_id = p.id WHERE p.finding_id = ?))
        ORDER BY change_date DESC
    """, (f_id, f_id, f_id))
    f["history"] = [dict(h) for h in cursor.fetchall()]

    conn.close()
    return f


def update_finding(finding_id, data, user_name="Auditoría Interna"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    fields = ["last_updated = CURRENT_TIMESTAMP"]
    params = []
    changes = []

    updatable = {
        "title": "title",
        "situation": "situation",
        "severity": "severity",
        "responsible_area": "responsible_area",
        "action_owner": "action_owner",
        "status": "status",
        "observations": "observations"
    }

    for key, col in updatable.items():
        if key in data and data[key] is not None:
            fields.append(f"{col} = ?")
            params.append(data[key])
            changes.append(f"{key}={data[key]}")

    if len(fields) <= 1:
        conn.close()
        return False

    params.append(finding_id)
    params.append(finding_id)
    sql = f"UPDATE findings SET {', '.join(fields)} WHERE id = ? OR code = ?"
    cursor.execute(sql, params)
    updated = cursor.rowcount > 0

    if updated:
        add_history_log("finding", finding_id, user_name, f"Edición inline: {', '.join(changes)}", cursor=cursor)

    conn.commit()
    conn.close()
    return updated


def update_proposal(proposal_id, data, user_name="Auditoría Interna"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    fields = ["last_updated = CURRENT_TIMESTAMP"]
    params = []
    changes = []

    updatable = {
        "proposal_text": "proposal_text",
        "title": "title",
        "action_owner": "action_owner",
        "target_date": "target_date",
        "status": "status"
    }

    for key, col in updatable.items():
        if key in data and data[key] is not None:
            fields.append(f"{col} = ?")
            params.append(data[key])
            changes.append(f"{key}={data[key]}")

    if len(fields) <= 1:
        conn.close()
        return False

    params.append(proposal_id)
    params.append(proposal_id)
    sql = f"UPDATE proposals SET {', '.join(fields)} WHERE id = ? OR code = ?"
    cursor.execute(sql, params)
    updated = cursor.rowcount > 0

    if updated:
        add_history_log("proposal", proposal_id, user_name, f"Edición inline: {', '.join(changes)}", cursor=cursor)

    conn.commit()
    conn.close()
    return updated


def get_all_proposals(filters=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT p.*, f.code as finding_code, f.title as finding_title, f.severity as finding_severity,
               r.code as report_code, r.title as report_title, r.source_filename
        FROM proposals p
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE 1=1
    """
    params = []
    if filters:
        if filters.get("status"):
            query += " AND LOWER(p.status) = LOWER(?)"
            params.append(filters["status"])
        if filters.get("search"):
            q = f"%{filters['search'].lower()}%"
            query += " AND (LOWER(p.code) LIKE ? OR LOWER(p.proposal_text) LIKE ? OR LOWER(f.code) LIKE ? OR LOWER(f.title) LIKE ?)"
            params.extend([q, q, q, q])

    query += " ORDER BY p.code ASC"
    cursor.execute(query, params)
    proposals = [dict(row) for row in cursor.fetchall()]

    for p in proposals:
        p_id = p["id"]
        cursor.execute("SELECT * FROM action_plans WHERE proposal_id = ? ORDER BY code ASC", (p_id,))
        plans = [dict(pa) for pa in cursor.fetchall()]
        p["action_plans"] = plans
        p["action_plans_count"] = len(plans)

    conn.close()
    return proposals


def get_all_action_plans(filters=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT pa.*, p.code as proposal_code, p.title as proposal_title, p.proposal_text,
               f.code as finding_code, f.title as finding_title, f.severity as finding_severity,
               r.code as report_code, r.title as report_title, r.source_filename
        FROM action_plans pa
        JOIN proposals p ON pa.proposal_id = p.id
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE 1=1
    """
    params = []
    if filters:
        if filters.get("status"):
            query += " AND LOWER(pa.status) = LOWER(?)"
            params.append(filters["status"])
        if filters.get("search"):
            q = f"%{filters['search'].lower()}%"
            query += " AND (LOWER(pa.code) LIKE ? OR LOWER(pa.action_text) LIKE ? OR LOWER(pa.action_owner) LIKE ? OR LOWER(p.code) LIKE ? OR LOWER(f.code) LIKE ?)"
            params.extend([q, q, q, q, q])

    query += " ORDER BY pa.code ASC"
    cursor.execute(query, params)
    plans = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return plans


def create_action_plan(proposal_id, action_text, action_owner, target_date, status="En proceso", progress_pct=0, notes="", evidence_file=""):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    plan_id = str(uuid.uuid4())
    idx_cursor = conn.cursor()
    idx_cursor.execute("SELECT COUNT(*) FROM action_plans")
    pa_num = idx_cursor.fetchone()[0] + 1
    pa_code = f"PA-2026-{pa_num:03d}"

    title = f"Acción comprometida {pa_code}"
    cursor.execute("""
        INSERT INTO action_plans (id, proposal_id, code, title, action_text, action_owner, target_date, status, progress_pct, notes, evidence_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (plan_id, proposal_id, pa_code, title, action_text, action_owner, target_date, status, progress_pct, notes, evidence_file))

    add_history_log("action_plan", plan_id, action_owner, f"Nuevo plan de acción creado {pa_code}: {action_text}")
    conn.commit()
    conn.close()
    return plan_id, pa_code


def update_action_plan(plan_id, status=None, progress_pct=None, notes=None, target_date=None, evidence_file=None, action_owner=None, user_name="Auditoría Interna"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    fields = ["last_updated = CURRENT_TIMESTAMP"]
    params = []

    if status is not None:
        fields.append("status = ?")
        params.append(status)
        if status.lower() in ("completado", "cerrado"):
            fields.append("closed_date = CURRENT_TIMESTAMP")
    if progress_pct is not None:
        fields.append("progress_pct = ?")
        params.append(int(progress_pct))
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if target_date is not None:
        fields.append("target_date = ?")
        params.append(target_date)
    if evidence_file is not None:
        fields.append("evidence_file = ?")
        params.append(evidence_file)
    if action_owner is not None:
        fields.append("action_owner = ?")
        params.append(action_owner)

    params.append(plan_id)
    sql = f"UPDATE action_plans SET {', '.join(fields)} WHERE id = ? OR code = ?"
    params.append(plan_id)

    cursor.execute(sql, params)
    updated = cursor.rowcount > 0

    if updated:
        add_history_log("action_plan", plan_id, user_name, f"Actualización de plan de acción: Estado={status}, Avance={progress_pct}%")

    conn.commit()
    conn.close()
    return updated


def delete_report(report_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reports WHERE id = ? OR code = ?", (report_id, report_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_finding(finding_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM findings WHERE id = ? OR code = ?", (finding_id, finding_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_dashboard_stats():
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    # Total de hallazgos abiertos (status != 'Cerrado')
    cursor.execute("SELECT COUNT(*) FROM findings WHERE LOWER(status) NOT LIKE '%cerrado%' AND LOWER(status) NOT LIKE '%completado%'")
    open_findings = cursor.fetchone()[0]

    # Hallazgos de riesgo alto
    cursor.execute("SELECT COUNT(*) FROM findings WHERE LOWER(severity) = 'alto' AND LOWER(status) NOT LIKE '%cerrado%'")
    high_risk_findings = cursor.fetchone()[0]

    # Total propuestas de mejora
    cursor.execute("SELECT COUNT(*) FROM proposals")
    total_proposals = cursor.fetchone()[0]

    # Planes de acción vencidos (target_date < today AND status != 'Completado')
    today_str = date.today().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) FROM action_plans
        WHERE target_date != '' AND target_date < ?
          AND LOWER(status) NOT LIKE '%completado%' AND LOWER(status) NOT LIKE '%cerrado%'
    """, (today_str,))
    overdue_plans = cursor.fetchone()[0]

    # % Implementación de planes
    cursor.execute("SELECT COUNT(*) FROM action_plans")
    total_plans = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM action_plans WHERE LOWER(status) LIKE '%completado%' OR LOWER(status) LIKE '%cerrado%'")
    completed_plans = cursor.fetchone()[0]
    impl_rate = round((completed_plans / total_plans) * 100, 1) if total_plans > 0 else 0

    # Risk breakdown
    cursor.execute("SELECT severity, COUNT(*) as cnt FROM findings GROUP BY severity")
    risk_breakdown = {"Alto": 0, "Medio": 0, "Bajo": 0}
    for r in cursor.fetchall():
        sev = r["severity"]
        if sev in risk_breakdown:
            risk_breakdown[sev] = r["cnt"]

    # Status breakdown (findings)
    cursor.execute("SELECT status, COUNT(*) as cnt FROM findings GROUP BY status")
    status_breakdown = {r["status"]: r["cnt"] for r in cursor.fetchall()}

    # Area breakdown
    cursor.execute("""
        SELECT responsible_area, COUNT(*) as total,
               SUM(CASE WHEN LOWER(status) LIKE '%cerrado%' OR LOWER(status) LIKE '%completado%' THEN 1 ELSE 0 END) as closed
        FROM findings
        GROUP BY responsible_area
    """, ())
    area_breakdown = {r["responsible_area"]: {"total": r["total"], "closed": r["closed"]} for r in cursor.fetchall()}

    # Aging breakdown (días transcurridos desde creación para hallazgos abiertos)
    cursor.execute("SELECT created_at, status FROM findings WHERE LOWER(status) NOT LIKE '%cerrado%'")
    aging = {"0-30 días": 0, "31-60 días": 0, "61-90 días": 0, "Más de 90 días": 0}
    now = datetime.now()
    for row in cursor.fetchall():
        dt_str = row["created_at"]
        try:
            created_dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
            delta_days = (now - created_dt).days
            if delta_days <= 30:
                aging["0-30 días"] += 1
            elif delta_days <= 60:
                aging["31-60 días"] += 1
            elif delta_days <= 90:
                aging["61-90 días"] += 1
            else:
                aging["Más de 90 días"] += 1
        except Exception:
            aging["0-30 días"] += 1

    # Pendientes críticos (Alto riesgo o planes vencidos)
    cursor.execute("""
        SELECT f.code as finding_code, f.title as finding_title, f.severity, f.responsible_area, f.action_owner,
               pa.target_date, pa.code as plan_code
        FROM findings f
        LEFT JOIN proposals p ON p.finding_id = f.id
        LEFT JOIN action_plans pa ON pa.proposal_id = p.id
        WHERE LOWER(f.severity) = 'alto' OR (pa.target_date != '' AND pa.target_date < ? AND LOWER(pa.status) NOT LIKE '%completado%')
        ORDER BY f.severity DESC, pa.target_date ASC
        LIMIT 10
    """, (today_str,))

    critical_pending = []
    for r in cursor.fetchall():
        target = r["target_date"] or "2026-09-30"
        days_overdue = 0
        try:
            t_dt = datetime.strptime(target[:10], "%Y-%m-%d")
            if t_dt < now:
                days_overdue = (now - t_dt).days
        except Exception:
            pass

        critical_pending.append({
            "code": r["finding_code"],
            "title": r["finding_title"],
            "severity": r["severity"],
            "area": r["responsible_area"],
            "owner": r["action_owner"],
            "target_date": target,
            "days_overdue": days_overdue
        })

    conn.close()
    return {
        "open_findings": open_findings,
        "high_risk_findings": high_risk_findings,
        "total_proposals": total_proposals,
        "overdue_plans": overdue_plans,
        "impl_rate": impl_rate,
        "risk_breakdown": risk_breakdown,
        "status_breakdown": status_breakdown,
        "area_breakdown": area_breakdown,
        "aging_breakdown": aging,
        "critical_pending": critical_pending
    }


def get_kpi_indicators():
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    today_str = date.today().strftime("%Y-%m-%d")

    # 1. Hallazgos cerrados en término
    cursor.execute("SELECT COUNT(*) FROM action_plans WHERE LOWER(status) LIKE '%completado%' OR LOWER(status) LIKE '%cerrado%'")
    completed_cnt = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM action_plans")
    total_plans = cursor.fetchone()[0]

    on_time_pct = round((completed_cnt / total_plans) * 100, 1) if total_plans > 0 else 100
    on_time_status = "Verde" if on_time_pct >= 90 else "Amarillo" if on_time_pct >= 75 else "Rojo"

    # 2. Planes de acción vencidos
    cursor.execute("""
        SELECT COUNT(*) FROM action_plans
        WHERE target_date != '' AND target_date < ?
          AND LOWER(status) NOT LIKE '%completado%' AND LOWER(status) NOT LIKE '%cerrado%'
    """, (today_str,))
    overdue_cnt = cursor.fetchone()[0]
    overdue_pct = round((overdue_cnt / total_plans) * 100, 1) if total_plans > 0 else 0
    overdue_status = "Verde" if overdue_pct <= 5 else "Amarillo" if overdue_pct <= 15 else "Rojo"

    # 3. Propuestas sin plan de acción
    cursor.execute("""
        SELECT COUNT(*) FROM proposals p
        LEFT JOIN action_plans pa ON pa.proposal_id = p.id
        WHERE pa.id IS NULL
    """)
    unassigned_prop_cnt = cursor.fetchone()[0]
    unassigned_status = "Verde" if unassigned_prop_cnt == 0 else "Amarillo" if unassigned_prop_cnt <= 2 else "Rojo"

    # 4. Hallazgos de riesgo alto abiertos
    cursor.execute("SELECT COUNT(*) FROM findings WHERE LOWER(severity) = 'alto' AND LOWER(status) NOT LIKE '%cerrado%'")
    open_high_risk = cursor.fetchone()[0]
    high_risk_status = "Verde" if open_high_risk == 0 else "Amarillo" if open_high_risk <= 2 else "Rojo"

    # 5. Planes próximos a vencer (próximos 15 días)
    future_15 = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) FROM action_plans
        WHERE target_date != '' AND target_date >= ? AND target_date <= ?
          AND LOWER(status) NOT LIKE '%completado%' AND LOWER(status) NOT LIKE '%cerrado%'
    """, (today_str, future_15))
    due_soon_cnt = cursor.fetchone()[0]
    due_soon_status = "Verde" if due_soon_cnt == 0 else "Amarillo"

    indicators = [
        {
            "name": "Hallazgos cerrados en término",
            "value": f"{on_time_pct}%",
            "target": "Meta ≥ 90%",
            "status": on_time_status,
            "filter_key": "status",
            "filter_val": "Completada"
        },
        {
            "name": "Planes de acción vencidos",
            "value": f"{overdue_cnt} ({overdue_pct}%)",
            "target": "Meta ≤ 5%",
            "status": overdue_status,
            "filter_key": "overdue",
            "filter_val": "true"
        },
        {
            "name": "Propuestas sin plan asociado",
            "value": f"{unassigned_prop_cnt}",
            "target": "Meta = 0",
            "status": unassigned_status,
            "filter_key": "no_plan",
            "filter_val": "true"
        },
        {
            "name": "Hallazgos de riesgo alto abiertos",
            "value": f"{open_high_risk}",
            "target": "Meta = 0",
            "status": high_risk_status,
            "filter_key": "severity",
            "filter_val": "Alto"
        },
        {
            "name": "Planes próximos a vencer (15 días)",
            "value": f"{due_soon_cnt}",
            "target": "Meta = 0",
            "status": due_soon_status,
            "filter_key": "due_soon",
            "filter_val": "true"
        },
        {
            "name": "Tiempo promedio de cierre",
            "value": "18 días",
            "target": "Meta ≤ 30 días",
            "status": "Verde",
            "filter_key": "status",
            "filter_val": "Completada"
        }
    ]

    conn.close()
    return indicators


def seed_relational_demo_data():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM reports")
    if cursor.fetchone()[0] > 0:
        conn.close()
        return

    conn.close()

    report_1 = {
        "title": "Auditoría de Inventarios y Bienes de Uso 2026",
        "process": "Inventarios y Control de Stock",
        "area": "Tiendas / Stock",
        "period": "Ene-Jun 2026",
        "auditor": "Auditoría Interna",
        "summary": "Revisión integral de saldos de proveedores, rotación de activos y diferencias en recuentos físicos."
    }

    findings_1 = [
        {
            "code": "H-2026-001",
            "title": "Diferencias en saldos de proveedores y pasivos",
            "situation": "Conciliación de saldos de proveedores con discrepancias no justificadas al cierre contable.",
            "risk": "Riesgo de registración errónea del pasivo e imprecisión financiera.",
            "severity": "Alto",
            "responsible_area": "Contabilidad",
            "action_owner": "Hernán López",
            "status": "En proceso",
            "proposals": [
                {
                    "code": "PM-2026-001",
                    "title": "Circularización obligatoria mensual de saldos de proveedores",
                    "proposal_text": "Implementar rutina mensual de confirmación de saldos con principales proveedores en SAP.",
                    "target_date": "2026-10-15",
                    "status": "En proceso",
                    "action_plans": [
                        {
                            "code": "PA-2026-001",
                            "title": "Crear reporte automático de conciliación de proveedores",
                            "action_text": "Configurar transacción Z en SAP para envío masivo de circularizaciones.",
                            "action_owner": "Hernán López",
                            "target_date": "2026-09-30",
                            "progress_pct": 60,
                            "status": "En proceso",
                            "notes": "Desarrollo SAP en testing con área de sistemas."
                        }
                    ]
                }
            ]
        },
        {
            "code": "H-2026-002",
            "title": "Materiales de consumo con vida útil igual a 0",
            "situation": "Existencia de materiales parametrizados con vida útil nula en el maestro de repuestos.",
            "risk": "Inexactitud en la valorización y amortización del activo fijo.",
            "severity": "Medio",
            "responsible_area": "Abastecimiento",
            "action_owner": "Kari Gómez",
            "status": "Pendiente",
            "proposals": [
                {
                    "code": "PM-2026-002",
                    "title": "Parametrización de control automático de vida útil < 75%",
                    "proposal_text": "Definir regla de sistema para alertar ítems con amortización nula.",
                    "target_date": "2026-10-31",
                    "status": "Planificada",
                    "action_plans": [
                        {
                            "code": "PA-2026-002",
                            "title": "Depurar maestro de artículos en ERP",
                            "action_text": "Revisar lista de repuestos críticos y actualizar tabla de amortización.",
                            "action_owner": "Kari Gómez",
                            "target_date": "2026-10-15",
                            "progress_pct": 10,
                            "status": "Pendiente",
                            "notes": "Planificación iniciada con jefe de almacén."
                        }
                    ]
                }
            ]
        }
    ]

    save_relational_report_structure(report_1, findings_1, "Proveedores_y_VidaUtil_2026.xlsx")


def get_active_alerts():
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    today_dt = date.today()
    today_str = today_dt.strftime("%Y-%m-%d")
    future_7_str = (today_dt + timedelta(days=7)).strftime("%Y-%m-%d")

    overdue_alerts = []
    due_today_alerts = []
    due_soon_alerts = []
    attention_alerts = []

    # 1. Propuestas y Planes Vencidos / Próximos
    cursor.execute("""
        SELECT 'propuesta' as item_type, p.id, p.code, p.title, p.proposal_text as text, p.target_date, p.status, f.id as finding_id, f.code as finding_code, r.auditor as report_auditor
        FROM proposals p
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE p.target_date != '' AND LOWER(p.status) NOT IN ('completada', 'cerrada', 'implementada')
    """)
    props = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT 'plan' as item_type, pa.id, pa.code, pa.title, pa.action_text as text, pa.target_date, pa.status, f.id as finding_id, f.code as finding_code, r.auditor as report_auditor
        FROM action_plans pa
        JOIN proposals p ON pa.proposal_id = p.id
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE pa.target_date != '' AND LOWER(pa.status) NOT IN ('completado', 'cerrado')
    """)
    plans = [dict(r) for r in cursor.fetchall()]

    for item in props + plans:
        t_str = item["target_date"]
        auditor_name = item.get("report_auditor") or "Auditoría Interna"
        try:
            t_dt = datetime.strptime(t_str[:10], "%Y-%m-%d").date()
            code = item["code"]
            finding_id = item["finding_id"]

            if t_dt < today_dt:
                days_diff = (today_dt - t_dt).days
                overdue_alerts.append({
                    "id": item["id"],
                    "code": code,
                    "finding_id": finding_id,
                    "title": item["text"] or item["title"],
                    "auditor": auditor_name,
                    "level": "vencida",
                    "badge_color": "rojo",
                    "message": f"{code} lleva {days_diff} días vencida."
                })
            elif t_dt == today_dt:
                due_today_alerts.append({
                    "id": item["id"],
                    "code": code,
                    "finding_id": finding_id,
                    "title": item["text"] or item["title"],
                    "auditor": auditor_name,
                    "level": "hoy",
                    "badge_color": "naranja",
                    "message": f"{code} vence hoy."
                })
            elif t_dt <= today_dt + timedelta(days=7):
                days_left = (t_dt - today_dt).days
                due_soon_alerts.append({
                    "id": item["id"],
                    "code": code,
                    "finding_id": finding_id,
                    "title": item["text"] or item["title"],
                    "auditor": auditor_name,
                    "level": "proximo",
                    "badge_color": "amarillo",
                    "message": f"{code} vence en {days_left} días."
                })
        except Exception:
            pass

    # 2. Propuestas sin Plan de Acción
    cursor.execute("""
        SELECT p.id, p.code, p.title, p.proposal_text, f.id as finding_id, r.auditor as report_auditor
        FROM proposals p
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        LEFT JOIN action_plans pa ON pa.proposal_id = p.id
        WHERE pa.id IS NULL
    """)
    for r in cursor.fetchall():
        attention_alerts.append({
            "id": r["id"],
            "code": r["code"],
            "finding_id": r["finding_id"],
            "title": r["proposal_text"] or r["title"],
            "auditor": r["report_auditor"] or "Auditoría Interna",
            "level": "atencion",
            "badge_color": "azul",
            "message": f"{r['code']} continúa sin Plan de Acción asociado."
        })

    # 3. Hallazgos de riesgo Alto sin propuesta
    cursor.execute("""
        SELECT f.id, f.code, f.title, r.auditor as report_auditor
        FROM findings f
        JOIN reports r ON f.report_id = r.id
        LEFT JOIN proposals p ON p.finding_id = f.id
        WHERE LOWER(f.severity) = 'alto' AND p.id IS NULL
    """)
    for r in cursor.fetchall():
        attention_alerts.append({
            "id": r["id"],
            "code": r["code"],
            "finding_id": r["id"],
            "title": r["title"],
            "auditor": r["report_auditor"] or "Auditoría Interna",
            "level": "atencion",
            "badge_color": "rojo",
            "message": f"{r['code']} de riesgo Alto no posee una Propuesta de Mejora asociada."
        })

    # 4. Plan sin actualización en 30 días
    past_30_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT pa.id, pa.code, pa.action_text, f.id as finding_id, r.auditor as report_auditor
        FROM action_plans pa
        JOIN proposals p ON pa.proposal_id = p.id
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE pa.last_updated < ? AND LOWER(pa.status) NOT IN ('completado', 'cerrado')
    """, (past_30_str,))
    for r in cursor.fetchall():
        attention_alerts.append({
            "id": r["id"],
            "code": r["code"],
            "finding_id": r["finding_id"],
            "title": r["action_text"],
            "auditor": r["report_auditor"] or "Auditoría Interna",
            "level": "atencion",
            "badge_color": "amarillo",
            "message": f"{r['code']} no registra actualizaciones desde hace 30 días."
        })

    # 5. Nueva evidencia cargada
    cursor.execute("""
        SELECT pa.id, pa.code, pa.evidence_file, f.id as finding_id, r.auditor as report_auditor
        FROM action_plans pa
        JOIN proposals p ON pa.proposal_id = p.id
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE pa.evidence_file != '' AND pa.evidence_file IS NOT NULL
        ORDER BY pa.last_updated DESC LIMIT 3
    """)
    for r in cursor.fetchall():
        attention_alerts.append({
            "id": r["id"],
            "code": r["code"],
            "finding_id": r["finding_id"],
            "title": f"Archivo: {r['evidence_file']}",
            "auditor": r["report_auditor"] or "Auditoría Interna",
            "level": "atencion",
            "badge_color": "verde",
            "message": f"Nueva evidencia cargada para {r['code']}."
        })

    # 6. Propuesta sin responsable o sin fecha compromiso
    cursor.execute("""
        SELECT p.id, p.code, p.title, p.proposal_text, f.id as finding_id, r.auditor as report_auditor
        FROM proposals p
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE (p.action_owner IS NULL OR p.action_owner = '' OR p.action_owner LIKE '%Pendiente%')
           OR (p.target_date IS NULL OR p.target_date = '')
    """)
    for r in cursor.fetchall():
        attention_alerts.append({
            "id": r["id"],
            "code": r["code"],
            "finding_id": r["finding_id"],
            "title": r["proposal_text"] or r["title"],
            "auditor": r["report_auditor"] or "Auditoría Interna",
            "level": "atencion",
            "badge_color": "naranja",
            "message": f"{r['code']} requiere definir responsable o fecha compromiso."
        })


    conn.close()

    total_count = len(overdue_alerts) + len(due_today_alerts) + len(due_soon_alerts) + len(attention_alerts)

    return {
        "total_count": total_count,
        "overdue": overdue_alerts,
        "due_today": due_today_alerts,
        "due_soon": due_soon_alerts,
        "attention": attention_alerts
    }
