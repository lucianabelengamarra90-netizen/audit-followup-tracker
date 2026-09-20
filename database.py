import sqlite3
import os
import uuid
import re
import sys
from datetime import datetime, date, timedelta
from domain.statuses import normalize_status, is_final_status, compute_effective_status
from domain.dates import parse_date_to_iso, format_display_date, is_date_past

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_tracker.db"))



class PGRow(dict):
    """
    Simula la interfaz de sqlite3.Row para PostgreSQL / psycopg2.
    Permite acceso por posición `row[0]`, por clave `row['code']` y conversión a dict `dict(row)`.
    """
    def __init__(self, cursor_description, row_tuple):
        super().__init__()
        self._keys = [col[0] for col in cursor_description]
        self._values = list(row_tuple)
        for k, v in zip(self._keys, self._values):
            self[k] = v

    def __getitem__(self, item):
        if isinstance(item, int):
            return self._values[item]
        return super().__getitem__(item)


class PGCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def connection(self):
        return PGConnWrapper(self._cursor.connection)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def execute(self, sql, params=None):
        sql_clean = sql.strip()

        # Adaptar PRAGMA table_info(table_name) de SQLite a PostgreSQL
        if sql_clean.upper().startswith("PRAGMA TABLE_INFO"):
            m = re.search(r"PRAGMA\s+table_info\(([^)]+)\)", sql_clean, re.IGNORECASE)
            if m:
                tbl_name = m.group(1).strip().strip("'\"")
                pg_sql = "SELECT column_name as name FROM information_schema.columns WHERE table_name = %s"
                self._cursor.execute(pg_sql, (tbl_name,))
                return self

        # Adaptar UPSERT de code_sequences
        if "INSERT OR REPLACE INTO code_sequences" in sql_clean:
            sql_clean = sql_clean.replace(
                "INSERT OR REPLACE INTO code_sequences (entity_type, year, last_value) VALUES (?, ?, ?)",
                "INSERT INTO code_sequences (entity_type, year, last_value) VALUES (%s, %s, %s) ON CONFLICT (entity_type, year) DO UPDATE SET last_value = EXCLUDED.last_value"
            )

        # Reemplazar '?' por '%s' para PostgreSQL
        if "?" in sql_clean:
            sql_clean = sql_clean.replace("?", "%s")

        if params is not None:
            self._cursor.execute(sql_clean, params)
        else:
            self._cursor.execute(sql_clean)
        return self

    def fetchone(self):
        row_tuple = self._cursor.fetchone()
        if row_tuple is None:
            return None
        return PGRow(self._cursor.description, row_tuple)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        desc = self._cursor.description
        return [PGRow(desc, r) for r in rows]


class PGConnWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return PGCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur


def get_db():
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if db_url and (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        import psycopg2
        conn = psycopg2.connect(db_url)
        return PGConnWrapper(conn)

    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn



def generate_next_code(entity_type: str, year: int = None, cursor=None) -> str:
    """
    Genera el siguiente código formateado de forma segura y transaccional mediante la tabla `code_sequences`.
    Evita colisiones y duplicados tras eliminaciones.
    Ejemplos: AUD-2026-001, H-2026-001, PM-2026-001, PA-2026-001
    """
    if year is None:
        year = datetime.now().year
        
    entity_prefix = {
        "report": "AUD",
        "finding": "H",
        "proposal": "PM",
        "action_plan": "PA"
    }.get(entity_type.lower(), entity_type.upper())

    table_map = {
        "report": "reports",
        "finding": "findings",
        "proposal": "proposals",
        "action_plan": "action_plans"
    }

    close_cursor = False
    if cursor is None:
        conn = get_db()
        cursor = conn.cursor()
        close_cursor = True

    try:
        cursor.execute("SELECT last_value FROM code_sequences WHERE entity_type = ? AND year = ?", (entity_type, year))
        row = cursor.fetchone()
        next_val = (row[0] + 1) if row else 1

        table_name = table_map.get(entity_type.lower())
        if table_name:
            while True:
                candidate = f"{entity_prefix}-{year}-{next_val:03d}"
                cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE code = ?", (candidate,))
                if cursor.fetchone()[0] == 0:
                    break
                next_val += 1

        cursor.execute("INSERT OR REPLACE INTO code_sequences (entity_type, year, last_value) VALUES (?, ?, ?)", (entity_type, year, next_val))
        return f"{entity_prefix}-{year}-{next_val:03d}"
    finally:
        if close_cursor:
            cursor.connection.commit()
            cursor.connection.close()


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 0. Tabla de Control de Migraciones
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 0. Tabla de Secuencias Persistentes para Generación de Códigos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_sequences (
            entity_type TEXT NOT NULL,
            year INTEGER NOT NULL,
            last_value INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (entity_type, year)
        )
    """)

    # Verify if old schema exists and migrate code column safely without DROP TABLE
    cursor.execute("PRAGMA table_info(reports)")
    cols = [r["name"] for r in cursor.fetchall()]
    if cols and "code" not in cols:
        cursor.execute("ALTER TABLE reports ADD COLUMN code TEXT")
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
            status TEXT DEFAULT 'En proceso',
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
            status TEXT DEFAULT 'En proceso',
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
            status TEXT DEFAULT 'En proceso',
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

    sync_code_sequences(cursor)
    conn.commit()
    conn.close()






def sync_code_sequences(cursor):
    """
    Sincroniza la tabla `code_sequences` escaneando los códigos existentes en
    `reports`, `findings`, `proposals` y `action_plans`.
    Asegura que el contador sea siempre mayor o igual al valor numérico máximo existente.
    """
    entity_configs = [
        ("report", "reports", "AUD"),
        ("finding", "findings", "H"),
        ("proposal", "proposals", "PM"),
        ("action_plan", "action_plans", "PA")
    ]

    current_year = datetime.now().year

    for entity_type, table_name, prefix in entity_configs:
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = [r["name"] for r in cursor.fetchall()]
        if "code" not in cols:
            continue

        cursor.execute(f"SELECT code FROM {table_name} WHERE code IS NOT NULL AND code != ''")
        rows = cursor.fetchall()
        max_by_year = {}

        for row in rows:
            code_str = str(row[0]).strip()
            parts = code_str.split("-")
            if len(parts) >= 3:
                try:
                    yr = int(parts[1])
                    seq = int(parts[2])
                    if yr not in max_by_year or seq > max_by_year[yr]:
                        max_by_year[yr] = seq
                except ValueError:
                    pass

        if current_year not in max_by_year:
            max_by_year[current_year] = 0

        for yr, max_val in max_by_year.items():
            cursor.execute("SELECT last_value FROM code_sequences WHERE entity_type = ? AND year = ?", (entity_type, yr))
            seq_row = cursor.fetchone()
            if not seq_row:
                cursor.execute("INSERT INTO code_sequences (entity_type, year, last_value) VALUES (?, ?, ?)", (entity_type, yr, max_val))
            elif max_val > seq_row[0]:
                cursor.execute("UPDATE code_sequences SET last_value = ? WHERE entity_type = ? AND year = ?", (max_val, entity_type, yr))


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
    Guarda la relación Informe -> Hallazgos -> Propuestas -> Planes de manera atómica con rollback en caso de error.
    Utiliza secuencias persistentes `generate_next_code`.
    """
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    try:
        report_id = str(uuid.uuid4())
        rep_code = generate_next_code("report", cursor=cursor)

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

        for f_item in findings_hierarchy:
            finding_id = str(uuid.uuid4())
            f_code = f_item.get("code") or generate_next_code("finding", cursor=cursor)

            cursor.execute("SELECT COUNT(*) FROM findings WHERE code = ?", (f_code,))
            if cursor.fetchone()[0] > 0:
                f_code = generate_next_code("finding", cursor=cursor)

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
            status = normalize_status(f_item.get("status") or "En proceso")

            cursor.execute("""
                INSERT INTO findings (id, report_id, code, title, situation, risk, severity, responsible_area, action_owner, status, observations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (finding_id, report_id, f_code, f_title, situation, risk, severity, responsible_area, action_owner, status, f_item.get("observations", "")))

            add_history_log("finding", finding_id, auditor, f"Creación de hallazgo {f_code}: {f_title}", cursor=cursor)

            proposals_list = f_item.get("proposals") or []
            if not proposals_list and f_item.get("proposal"):
                proposals_list = [{"title": f_item.get("title"), "proposal_text": f_item.get("proposal")}]

            for p_item in proposals_list:
                proposal_id = str(uuid.uuid4())
                p_code = p_item.get("code") or generate_next_code("proposal", cursor=cursor)

                cursor.execute("SELECT COUNT(*) FROM proposals WHERE code = ?", (p_code,))
                if cursor.fetchone()[0] > 0:
                    p_code = generate_next_code("proposal", cursor=cursor)

                p_title = (p_item.get("title") or f"Propuesta para {f_title}").strip()
                p_text = (p_item.get("proposal_text") or p_item.get("proposal") or p_title).strip()
                p_target_date = parse_date_to_iso(p_item.get("target_date") or "")
                p_status = normalize_status(p_item.get("status") or status)

                cursor.execute("""
                    INSERT INTO proposals (id, finding_id, code, title, proposal_text, severity, responsible_area, action_owner, target_date, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (proposal_id, finding_id, p_code, p_title, p_text, severity, responsible_area, action_owner, p_target_date, p_status))

                add_history_log("proposal", proposal_id, auditor, f"Creación de propuesta {p_code} vinculada a {f_code}", cursor=cursor)

                plans_list = p_item.get("action_plans") or []

                for pa_item in plans_list:
                    plan_id = str(uuid.uuid4())
                    pa_code = pa_item.get("code") or generate_next_code("action_plan", cursor=cursor)

                    cursor.execute("SELECT COUNT(*) FROM action_plans WHERE code = ?", (pa_code,))
                    if cursor.fetchone()[0] > 0:
                        pa_code = generate_next_code("action_plan", cursor=cursor)

                    pa_title = (pa_item.get("title") or f"Acción para {p_code}").strip()
                    pa_text = (pa_item.get("action_text") or pa_title).strip()
                    pa_owner = (pa_item.get("action_owner") or action_owner).strip()
                    pa_date = parse_date_to_iso(pa_item.get("target_date") or p_target_date)
                    try:
                        pa_pct = max(0, min(100, int(pa_item.get("progress_pct") or 0)))
                    except Exception:
                        pa_pct = 0
                    pa_status = normalize_status(pa_item.get("status") or p_status)
                    if pa_pct == 100 and pa_status != "En suspensión":
                        pa_status = "Finalizado"

                    closed_dt = datetime.now().strftime("%Y-%m-%d") if pa_status == "Finalizado" else None
                    pa_notes = (pa_item.get("notes") or "").strip()
                    pa_evidence = (pa_item.get("evidence_file") or "").strip()

                    cursor.execute("""
                        INSERT INTO action_plans (id, proposal_id, code, title, action_text, action_owner, target_date, status, progress_pct, notes, evidence_file, closed_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (plan_id, proposal_id, pa_code, pa_title, pa_text, pa_owner, pa_date, pa_status, pa_pct, pa_notes, pa_evidence, closed_dt))

                    add_history_log("action_plan", plan_id, pa_owner, f"Creación de plan de acción {pa_code} vinculado a propuesta {p_code}", cursor=cursor)

        conn.commit()
        return report_id
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        conn.close()


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
            WHERE f.report_id = ? AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado', 'archivada')
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

    for f in findings:
        f_id = f["id"]
        cursor.execute("SELECT * FROM proposals WHERE finding_id = ? ORDER BY code ASC", (f_id,))
        proposals = [dict(p) for p in cursor.fetchall()]

        for p in proposals:
            p_id = p["id"]
            cursor.execute("SELECT * FROM action_plans WHERE proposal_id = ? ORDER BY code ASC", (p_id,))
            plans = [dict(pa) for pa in cursor.fetchall()]
            for pa in plans:
                pa["effective_status"] = compute_effective_status(pa.get("status"), pa.get("target_date"))
            p["action_plans"] = plans
            
            p_target = p.get("target_date")
            if not p_target and plans:
                dates = [pa["target_date"] for pa in plans if pa.get("target_date")]
                p_target = min(dates) if dates else None
            p_eff = compute_effective_status(p.get("status"), p_target)
            if p_eff == "En proceso" and plans:
                if any(pa.get("effective_status") == "Vencido" for pa in plans):
                    p_eff = "Vencido"
            p["effective_status"] = p_eff

        f["proposals"] = proposals
        f["proposals_count"] = len(proposals)
        f["action_plans_count"] = sum(len(p["action_plans"]) for p in proposals)

        f_eff = compute_effective_status(f.get("status"), None)
        if f_eff == "En proceso" and proposals:
            if any(p.get("effective_status") == "Vencido" for p in proposals):
                f_eff = "Vencido"
        f["effective_status"] = f_eff

    conn.close()

    if filters and filters.get("status"):
        st_filter = filters["status"].strip().lower()
        if st_filter == "vencido":
            findings = [f for f in findings if f["effective_status"].lower() == "vencido"]
        elif st_filter == "en proceso":
            findings = [f for f in findings if f["effective_status"].lower() == "en proceso"]
        elif st_filter in ("en suspensión", "en suspension", "stand-by"):
            findings = [f for f in findings if f["effective_status"].lower() == "en suspensión"]
        elif st_filter in ("finalizado", "completado", "cerrado"):
            findings = [f for f in findings if f["effective_status"].lower() == "finalizado"]

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

    try:
        cursor.execute("SELECT id, status FROM findings WHERE id = ? OR code = ?", (finding_id, finding_id))
        f_row = cursor.fetchone()
        if not f_row:
            return False

        actual_finding_id = f_row[0]

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
                val = data[key]
                if key == "status":
                    val = normalize_status(val)
                fields.append(f"{col} = ?")
                params.append(val)
                changes.append(f"{key}={val}")

        if len(fields) <= 1:
            return False

        params.append(actual_finding_id)
        params.append(actual_finding_id)
        sql = f"UPDATE findings SET {', '.join(fields)} WHERE id = ? OR code = ?"
        cursor.execute(sql, params)
        updated = cursor.rowcount > 0

        if updated:
            add_history_log("finding", actual_finding_id, user_name, f"Edición inline: {', '.join(changes)}", cursor=cursor)

            if "status" in data and data["status"] is not None:
                st_clean = normalize_status(data["status"])
                today_str = datetime.now().strftime("%Y-%m-%d")

                if st_clean == "Finalizado":
                    cursor.execute("""
                        UPDATE proposals SET status = 'Finalizado'
                        WHERE finding_id = ? AND status != 'En suspensión'
                    """, (actual_finding_id,))
                    cursor.execute("""
                        UPDATE action_plans SET status = 'Finalizado', progress_pct = 100, closed_date = ?
                        WHERE proposal_id IN (SELECT id FROM proposals WHERE finding_id = ?) AND status != 'En suspensión'
                    """, (today_str, actual_finding_id))
                elif st_clean == "En proceso":
                    cursor.execute("""
                        UPDATE proposals SET status = 'En proceso'
                        WHERE finding_id = ? AND status = 'Finalizado'
                    """, (actual_finding_id,))
                    cursor.execute("""
                        UPDATE action_plans SET status = 'En proceso', closed_date = NULL
                        WHERE proposal_id IN (SELECT id FROM proposals WHERE finding_id = ?) AND status = 'Finalizado'
                    """, (actual_finding_id,))

        conn.commit()
        return updated
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        conn.close()


def update_proposal(proposal_id, data, user_name="Auditoría Interna"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, finding_id, status FROM proposals WHERE id = ? OR code = ?", (proposal_id, proposal_id))
        p_row = cursor.fetchone()
        if not p_row:
            return False

        actual_proposal_id = p_row[0]
        finding_id = p_row[1]

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
                val = data[key]
                if key == "target_date":
                    val = parse_date_to_iso(val)
                elif key == "status":
                    val = normalize_status(val)
                fields.append(f"{col} = ?")
                params.append(val)
                changes.append(f"{key}={val}")

        if len(fields) <= 1:
            return False

        params.append(actual_proposal_id)
        params.append(actual_proposal_id)
        sql = f"UPDATE proposals SET {', '.join(fields)} WHERE id = ? OR code = ?"
        cursor.execute(sql, params)
        updated = cursor.rowcount > 0

        if updated:
            add_history_log("proposal", actual_proposal_id, user_name, f"Edición inline: {', '.join(changes)}", cursor=cursor)

            if "status" in data and data["status"] is not None:
                st_clean = normalize_status(data["status"])
                today_str = datetime.now().strftime("%Y-%m-%d")

                if st_clean == "Finalizado":
                    cursor.execute("""
                        UPDATE action_plans 
                        SET status = 'Finalizado', progress_pct = 100, closed_date = ?
                        WHERE proposal_id = ? AND status != 'En suspensión'
                    """, (today_str, actual_proposal_id))
                elif st_clean == "En proceso":
                    cursor.execute("""
                        UPDATE action_plans 
                        SET status = 'En proceso', closed_date = NULL
                        WHERE proposal_id = ? AND status = 'Finalizado'
                    """, (actual_proposal_id,))

                if finding_id:
                    _evaluate_finding_cascade(cursor, finding_id)

        conn.commit()
        return updated
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        conn.close()


def create_proposal_for_finding(finding_id, proposal_text, action_owner="", target_date="", status="En proceso", user_name="Auditoría Interna"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM findings WHERE id = ? OR code = ?", (finding_id, finding_id))
    f_row = cursor.fetchone()
    if not f_row:
        conn.close()
        return None, None
    finding = dict(f_row)
    actual_finding_id = finding["id"]

    proposal_id = str(uuid.uuid4())
    pm_code = generate_next_code("proposal", cursor=cursor)

    p_title = f"Propuesta {pm_code}"
    iso_target_date = parse_date_to_iso(target_date)
    norm_status = normalize_status(status)

    cursor.execute("""
        INSERT INTO proposals (id, finding_id, code, title, proposal_text, severity, responsible_area, action_owner, target_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (proposal_id, actual_finding_id, pm_code, p_title, proposal_text, finding.get("severity", ""), finding.get("responsible_area", ""), action_owner or finding.get("action_owner", ""), iso_target_date, norm_status))

    add_history_log("proposal", proposal_id, user_name, f"Nueva propuesta agregada {pm_code}: {proposal_text}", cursor=cursor)
    conn.commit()
    conn.close()
    return proposal_id, pm_code


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
    if filters and filters.get("search"):
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
        for pa in plans:
            pa["effective_status"] = compute_effective_status(pa.get("status"), pa.get("target_date"))
        p["action_plans"] = plans
        p["action_plans_count"] = len(plans)

        p_target = p.get("target_date")
        if not p_target and plans:
            dates = [pa["target_date"] for pa in plans if pa.get("target_date")]
            p_target = min(dates) if dates else None

        p_eff = compute_effective_status(p.get("status"), p_target)
        if p_eff == "En proceso" and plans:
            if any(pa.get("effective_status") == "Vencido" for pa in plans):
                p_eff = "Vencido"
        p["effective_status"] = p_eff

    conn.close()

    if filters and filters.get("status"):
        st_filter = filters["status"].strip().lower()
        if st_filter == "vencido":
            proposals = [p for p in proposals if p["effective_status"].lower() == "vencido"]
        elif st_filter == "en proceso":
            proposals = [p for p in proposals if p["effective_status"].lower() == "en proceso"]
        elif st_filter in ("en suspensión", "en suspension", "stand-by"):
            proposals = [p for p in proposals if p["effective_status"].lower() == "en suspensión"]
        elif st_filter in ("finalizado", "completado", "cerrado"):
            proposals = [p for p in proposals if p["effective_status"].lower() == "finalizado"]

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
    if filters and filters.get("search"):
        q = f"%{filters['search'].lower()}%"
        query += " AND (LOWER(pa.code) LIKE ? OR LOWER(pa.action_text) LIKE ? OR LOWER(pa.action_owner) LIKE ? OR LOWER(p.code) LIKE ? OR LOWER(f.code) LIKE ?)"
        params.extend([q, q, q, q, q])

    query += " ORDER BY pa.code ASC"
    cursor.execute(query, params)
    plans = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for pa in plans:
        pa["effective_status"] = compute_effective_status(pa.get("status"), pa.get("target_date"))

    if filters and filters.get("status"):
        st_filter = filters["status"].strip().lower()
        if st_filter == "vencido":
            plans = [pa for pa in plans if pa["effective_status"].lower() == "vencido"]
        elif st_filter == "en proceso":
            plans = [pa for pa in plans if pa["effective_status"].lower() == "en proceso"]
        elif st_filter in ("en suspensión", "en suspension", "stand-by"):
            plans = [pa for pa in plans if pa["effective_status"].lower() == "en suspensión"]
        elif st_filter in ("finalizado", "completado", "cerrado"):
            plans = [pa for pa in plans if pa["effective_status"].lower() == "finalizado"]

    return plans


def get_proposal_by_id_or_code(prop_identifier):
    if not prop_identifier:
        return None
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proposals WHERE id = ? OR code = ?", (prop_identifier, prop_identifier))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_action_plan(proposal_id, action_text, action_owner, target_date, status="En proceso", progress_pct=0, notes="", evidence_file=""):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    p_chk = conn.cursor()
    p_chk.execute("SELECT id FROM proposals WHERE id = ? OR code = ?", (proposal_id, proposal_id))
    p_row = p_chk.fetchone()
    if p_row:
        proposal_id = p_row[0]

    plan_id = str(uuid.uuid4())
    pa_code = generate_next_code("action_plan", cursor=cursor)

    title = f"Acción comprometida {pa_code}"
    iso_date = parse_date_to_iso(target_date)
    norm_status = normalize_status(status)
    clean_pct = max(0, min(100, int(progress_pct or 0)))
    if clean_pct == 100 and norm_status != "En suspensión":
        norm_status = "Finalizado"

    closed_dt = datetime.now().strftime("%Y-%m-%d") if norm_status == "Finalizado" else None

    cursor.execute("""
        INSERT INTO action_plans (id, proposal_id, code, title, action_text, action_owner, target_date, status, progress_pct, notes, evidence_file, closed_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (plan_id, proposal_id, pa_code, title, action_text, action_owner, iso_date, norm_status, clean_pct, notes, evidence_file, closed_dt))

    add_history_log("action_plan", plan_id, action_owner, f"Nuevo plan de acción creado {pa_code}: {action_text}", cursor=cursor)
    conn.commit()
    conn.close()
    return plan_id, pa_code


def _evaluate_proposal_cascade(cursor, proposal_id):
    """
    Evalúa y actualiza la cascada ascendente del estado de una Propuesta.
    Reglas v2:
    - Si la propuesta está 'En suspensión' (manual), NO se sobrescribe.
    - Si la propuesta tiene 0 planes de acción, NO se finaliza automáticamente.
    - Si TODOS los planes de acción del grupo están 'Finalizado' (y hay al menos 1), la propuesta pasa a 'Finalizado'.
    - Si al menos un plan vuelve a 'En proceso', la propuesta pasa a 'En proceso'.
    """
    cursor.execute("SELECT status, finding_id FROM proposals WHERE id = ?", (proposal_id,))
    p_row = cursor.fetchone()
    if not p_row:
        return

    p_curr_status = normalize_status(p_row[0])
    finding_id = p_row[1]

    if p_curr_status == "En suspensión":
        if finding_id:
            _evaluate_finding_cascade(cursor, finding_id)
        return

    cursor.execute("SELECT status FROM action_plans WHERE proposal_id = ?", (proposal_id,))
    child_plans = [normalize_status(r[0]) for r in cursor.fetchall()]

    if not child_plans:
        if finding_id:
            _evaluate_finding_cascade(cursor, finding_id)
        return

    all_plans_final = all(s == "Finalizado" for s in child_plans)
    new_p_status = "Finalizado" if all_plans_final else "En proceso"

    if new_p_status != p_curr_status:
        cursor.execute("UPDATE proposals SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", (new_p_status, proposal_id))

    if finding_id:
        _evaluate_finding_cascade(cursor, finding_id)


def _evaluate_finding_cascade(cursor, finding_id):
    """
    Evalúa y actualiza la cascada ascendente del estado de un Hallazgo.
    Reglas v2:
    - Si el hallazgo está 'En suspensión' (manual), NO se sobrescribe.
    - Si el hallazgo tiene 0 propuestas, NO se finaliza automáticamente.
    - Si TODAS las propuestas del grupo están 'Finalizado' (y hay al menos 1), el hallazgo pasa a 'Finalizado'.
    - Si al menos una propuesta vuelve a 'En proceso', el hallazgo pasa a 'En proceso'.
    """
    cursor.execute("SELECT status FROM findings WHERE id = ?", (finding_id,))
    f_row = cursor.fetchone()
    if not f_row:
        return

    f_curr_status = normalize_status(f_row[0])

    if f_curr_status == "En suspensión":
        return

    cursor.execute("SELECT status FROM proposals WHERE finding_id = ?", (finding_id,))
    child_props = [normalize_status(r[0]) for r in cursor.fetchall()]

    if not child_props:
        return

    all_props_final = all(s == "Finalizado" for s in child_props)
    new_f_status = "Finalizado" if all_props_final else "En proceso"

    if new_f_status != f_curr_status:
        cursor.execute("UPDATE findings SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", (new_f_status, finding_id))


def update_action_plan(plan_id, status=None, progress_pct=None, notes=None, target_date=None, evidence_file=None, action_owner=None, user_name="Auditoría Interna", confirm_finalize=False):
    """
    Actualización atómica de Plan de Acción con sincronización inteligente de Avance (0-100),
    Estado efectivo ('En proceso', 'En suspensión', 'Finalizado') y cascada en Proposal y Finding.
    """
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, proposal_id, status, progress_pct, notes FROM action_plans WHERE id = ? OR code = ?", (plan_id, plan_id))
        current_plan = cursor.fetchone()
        if not current_plan:
            return False

        actual_plan_id = current_plan[0]
        proposal_id = current_plan[1]
        curr_status = normalize_status(current_plan[2])
        curr_pct = current_plan[3] if current_plan[3] is not None else 0
        curr_notes = current_plan[4] or ""

        target_pct = curr_pct
        if progress_pct is not None:
            try:
                target_pct = max(0, min(100, int(progress_pct)))
            except Exception:
                pass

        req_status = curr_status
        if status is not None:
            req_status = normalize_status(status)

        if req_status == "Finalizado":
            target_pct = 100

        if target_pct == 100:
            if req_status != "En suspensión":
                if (status is not None and normalize_status(status) == "Finalizado") or confirm_finalize:
                    final_status = "Finalizado"
                else:
                    final_status = "En proceso"
                    if "Pendiente de validación" not in (notes or curr_notes):
                        val_tag = " (Pendiente de validación)"
                        notes = (notes + val_tag) if notes is not None else (curr_notes + val_tag if curr_notes else "Pendiente de validación")
            else:
                final_status = "En suspensión"
        else:
            final_status = req_status

        fields = ["last_updated = CURRENT_TIMESTAMP"]
        params = []

        fields.append("status = ?")
        params.append(final_status)

        if final_status == "Finalizado":
            fields.append("closed_date = ?")
            params.append(datetime.now().strftime("%Y-%m-%d"))
        else:
            fields.append("closed_date = NULL")

        fields.append("progress_pct = ?")
        params.append(target_pct)

        if notes is not None:
            fields.append("notes = ?")
            params.append(notes)
        if target_date is not None:
            fields.append("target_date = ?")
            params.append(parse_date_to_iso(target_date))
        if evidence_file is not None:
            fields.append("evidence_file = ?")
            params.append(evidence_file)
        if action_owner is not None:
            fields.append("action_owner = ?")
            params.append(action_owner)

        params.append(actual_plan_id)
        params.append(actual_plan_id)
        sql = f"UPDATE action_plans SET {', '.join(fields)} WHERE id = ? OR code = ?"

        cursor.execute(sql, params)
        updated = cursor.rowcount > 0

        if updated:
            add_history_log("action_plan", actual_plan_id, user_name, f"Actualización de plan de acción: Estado={final_status}, Avance={target_pct}%", cursor=cursor)

            if proposal_id:
                _evaluate_proposal_cascade(cursor, proposal_id)

        conn.commit()
        return updated
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        conn.close()


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

    cursor.execute("SELECT COUNT(*) FROM findings WHERE LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')")
    open_findings = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM findings WHERE LOWER(severity) = 'alto' AND LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')")
    high_risk_findings = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM proposals")
    total_proposals = cursor.fetchone()[0]

    today_str = date.today().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) FROM action_plans
        WHERE target_date != '' AND target_date IS NOT NULL AND target_date < ?
          AND LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')
    """, (today_str,))
    overdue_plans = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM action_plans")
    total_plans = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM action_plans WHERE LOWER(status) IN ('finalizado', 'completado', 'cerrado')")
    completed_plans = cursor.fetchone()[0]
    impl_rate = round((completed_plans / total_plans) * 100, 1) if total_plans > 0 else 0

    cursor.execute("SELECT severity, COUNT(*) as cnt FROM findings GROUP BY severity")
    risk_breakdown = {"Alto": 0, "Medio": 0, "Bajo": 0}
    for r in cursor.fetchall():
        sev = r["severity"]
        if sev in risk_breakdown:
            risk_breakdown[sev] = r["cnt"]

    cursor.execute("SELECT status, COUNT(*) as cnt FROM findings GROUP BY status")
    status_breakdown = {normalize_status(r["status"]): r["cnt"] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT responsible_area, COUNT(*) as total,
               SUM(CASE WHEN LOWER(status) IN ('finalizado', 'completado', 'cerrado') THEN 1 ELSE 0 END) as closed
        FROM findings
        GROUP BY responsible_area
    """, ())
    area_breakdown = {r["responsible_area"]: {"total": r["total"], "closed": r["closed"]} for r in cursor.fetchall()}

    cursor.execute("SELECT created_at, status FROM findings WHERE LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')")
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

    cursor.execute("""
        SELECT f.code as finding_code, f.title as finding_title, f.severity, f.responsible_area, f.action_owner,
               pa.target_date, pa.code as plan_code
        FROM findings f
        LEFT JOIN proposals p ON p.finding_id = f.id
        LEFT JOIN action_plans pa ON pa.proposal_id = p.id
        WHERE LOWER(f.severity) = 'alto' OR (pa.target_date != '' AND pa.target_date IS NOT NULL AND pa.target_date < ? AND LOWER(pa.status) NOT IN ('finalizado', 'completado'))
        ORDER BY f.severity DESC, pa.target_date ASC
        LIMIT 10
    """, (today_str,))

    critical_pending = []
    for r in cursor.fetchall():
        target = r["target_date"] or today_str
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
    """
    Retorna indicadores reales sin fallbacks duros inventados.
    Calcula el tiempo promedio real de cierre o devuelve 'Sin datos'.
    """
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    today_str = date.today().strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM action_plans WHERE LOWER(status) IN ('finalizado', 'completado', 'cerrado')")
    completed_cnt = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM action_plans")
    total_plans = cursor.fetchone()[0]

    on_time_pct = round((completed_cnt / total_plans) * 100, 1) if total_plans > 0 else 0
    on_time_status = "Verde" if on_time_pct >= 90 else "Amarillo" if on_time_pct >= 75 else "Rojo"

    cursor.execute("""
        SELECT COUNT(*) FROM action_plans
        WHERE target_date != '' AND target_date IS NOT NULL AND target_date < ?
          AND LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')
    """, (today_str,))
    overdue_cnt = cursor.fetchone()[0]
    overdue_pct = round((overdue_cnt / total_plans) * 100, 1) if total_plans > 0 else 0
    overdue_status = "Verde" if overdue_pct <= 5 else "Amarillo" if overdue_pct <= 15 else "Rojo"

    cursor.execute("""
        SELECT COUNT(*) FROM proposals p
        LEFT JOIN action_plans pa ON pa.proposal_id = p.id
        WHERE pa.id IS NULL
    """)
    unassigned_prop_cnt = cursor.fetchone()[0]
    unassigned_status = "Verde" if unassigned_prop_cnt == 0 else "Amarillo" if unassigned_prop_cnt <= 2 else "Rojo"

    cursor.execute("SELECT COUNT(*) FROM findings WHERE LOWER(severity) = 'alto' AND LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')")
    open_high_risk = cursor.fetchone()[0]
    high_risk_status = "Verde" if open_high_risk == 0 else "Amarillo" if open_high_risk <= 2 else "Rojo"

    future_15 = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) FROM action_plans
        WHERE target_date != '' AND target_date IS NOT NULL AND target_date >= ? AND target_date <= ?
          AND LOWER(status) NOT IN ('finalizado', 'completado', 'cerrado')
    """, (today_str, future_15))
    due_soon_cnt = cursor.fetchone()[0]
    due_soon_status = "Verde" if due_soon_cnt == 0 else "Amarillo"

    cursor.execute("""
        SELECT created_at, closed_date FROM action_plans
        WHERE closed_date IS NOT NULL AND closed_date != '' AND closed_date != 'None'
    """)
    closed_rows = cursor.fetchall()
    if closed_rows:
        tot_days = 0
        cnt_c = 0
        for r in closed_rows:
            try:
                st = datetime.strptime(str(r["created_at"])[:10], "%Y-%m-%d")
                cl = datetime.strptime(str(r["closed_date"])[:10], "%Y-%m-%d")
                diff = (cl - st).days
                if diff >= 0:
                    tot_days += diff
                    cnt_c += 1
            except Exception:
                pass
        avg_days = round(tot_days / cnt_c) if cnt_c > 0 else 0
        avg_days_val = f"{avg_days} días"
        avg_status = "Verde" if avg_days <= 30 else "Amarillo" if avg_days <= 60 else "Rojo"
    else:
        avg_days_val = "Sin datos"
        avg_status = "Verde"

    indicators = [
        {
            "name": "Hallazgos cerrados en término",
            "value": f"{on_time_pct}%" if total_plans > 0 else "Sin datos",
            "target": "Meta ≥ 90%",
            "status": on_time_status,
            "filter_key": "status",
            "filter_val": "Finalizado"
        },
        {
            "name": "Planes de acción vencidos",
            "value": f"{overdue_cnt} ({overdue_pct}%)" if total_plans > 0 else "Sin datos",
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
            "value": avg_days_val,
            "target": "Meta ≤ 30 días",
            "status": avg_status,
            "filter_key": "status",
            "filter_val": "Finalizado"
        }
    ]

    conn.close()
    return indicators


def get_active_alerts():
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    today_dt = date.today()
    today_str = today_dt.strftime("%Y-%m-%d")

    overdue_alerts = []
    due_today_alerts = []
    due_soon_alerts = []
    attention_alerts = []

    cursor.execute("""
        SELECT 'propuesta' as item_type, p.id, p.code, p.title, p.proposal_text as text, p.target_date, p.status, f.id as finding_id, f.code as finding_code, r.auditor as report_auditor
        FROM proposals p
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE p.target_date != '' AND p.target_date IS NOT NULL AND LOWER(p.status) NOT IN ('finalizado', 'completada', 'cerrada', 'implementada')
    """)
    props = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT 'plan' as item_type, pa.id, pa.code, pa.title, pa.action_text as text, pa.target_date, pa.status, f.id as finding_id, f.code as finding_code, r.auditor as report_auditor
        FROM action_plans pa
        JOIN proposals p ON pa.proposal_id = p.id
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE pa.target_date != '' AND pa.target_date IS NOT NULL AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado')
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

    past_30_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT pa.id, pa.code, pa.action_text, f.id as finding_id, r.auditor as report_auditor
        FROM action_plans pa
        JOIN proposals p ON pa.proposal_id = p.id
        JOIN findings f ON p.finding_id = f.id
        JOIN reports r ON f.report_id = r.id
        WHERE pa.last_updated < ? AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado')
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
