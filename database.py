import sqlite3
import os
import uuid
import re
import sys
from datetime import datetime, date, timedelta
from domain.statuses import normalize_status, is_final_status, compute_effective_status
from domain.dates import parse_date_to_iso, format_display_date, is_date_past, get_argentina_today

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
    strict_pg = os.environ.get("STRICT_POSTGRES", "false").lower() == "true"
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    
    if db_url and (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            return PGConnWrapper(conn)
        except Exception as exc:
            if strict_pg:
                raise RuntimeError(f"PRODUCTION DB ERROR: Connection to PostgreSQL failed ({exc}).") from exc
            print(f"[WARN] Failed connecting to PostgreSQL ({exc}), falling back to local SQLite.")

    if strict_pg:
        raise RuntimeError("PRODUCTION DB ERROR: DATABASE_URL is not set in production environment. PostgreSQL connection is mandatory.")

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
    try:
        conn = get_db()
        cursor = conn.cursor()
    except Exception as exc:
        print(f"[WARN] init_db skipped on startup: {exc}")
        return

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
            validated_at TIMESTAMP,
            validated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (finding_id) REFERENCES findings (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("PRAGMA table_info(proposals)")
    prop_cols = [r['name'] for r in cursor.fetchall()]
    if prop_cols and 'validated_at' not in prop_cols:
        cursor.execute("ALTER TABLE proposals ADD COLUMN validated_at TIMESTAMP")
        cursor.execute("ALTER TABLE proposals ADD COLUMN validated_by TEXT")

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
            validated_at TIMESTAMP,
            validated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (proposal_id) REFERENCES proposals (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("PRAGMA table_info(action_plans)")
    plan_cols = [r['name'] for r in cursor.fetchall()]
    if plan_cols and 'validated_at' not in plan_cols:
        cursor.execute("ALTER TABLE action_plans ADD COLUMN validated_at TIMESTAMP")
        cursor.execute("ALTER TABLE action_plans ADD COLUMN validated_by TEXT")

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

    if not reports:
        conn.close()
        return []

    counts_sql = """
        SELECT r.id as report_id,
               COUNT(DISTINCT f.id) as findings_count,
               COUNT(DISTINCT p.id) as proposals_count,
               COUNT(DISTINCT pa.id) as action_plans_count,
               COUNT(DISTINCT CASE WHEN LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado', 'archivada') THEN pa.id END) as pending_plans_count
        FROM reports r
        LEFT JOIN findings f ON f.report_id = r.id
        LEFT JOIN proposals p ON p.finding_id = f.id
        LEFT JOIN action_plans pa ON pa.proposal_id = p.id
        GROUP BY r.id
    """
    cursor.execute(counts_sql)
    counts_map = {row["report_id"]: dict(row) for row in cursor.fetchall()}

    for rep in reports:
        rep_id = rep["id"]
        c_info = counts_map.get(rep_id, {})
        rep["findings_count"] = c_info.get("findings_count", 0)
        rep["proposals_count"] = c_info.get("proposals_count", 0)
        rep["action_plans_count"] = c_info.get("action_plans_count", 0)
        rep["pending_plans_count"] = c_info.get("pending_plans_count", 0)

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

    if not findings:
        conn.close()
        return []

    finding_ids = [f["id"] for f in findings]

    placeholders = ", ".join(["?"] * len(finding_ids))
    cursor.execute(f"SELECT * FROM proposals WHERE finding_id IN ({placeholders}) ORDER BY code ASC", finding_ids)
    all_proposals = [dict(p) for p in cursor.fetchall()]

    proposal_ids = [p["id"] for p in all_proposals]

    all_plans_map = {}
    if proposal_ids:
        pl_placeholders = ", ".join(["?"] * len(proposal_ids))
        cursor.execute(f"SELECT * FROM action_plans WHERE proposal_id IN ({pl_placeholders}) ORDER BY code ASC", proposal_ids)
        for pa in cursor.fetchall():
            pa_dict = dict(pa)
            pa_dict["effective_status"] = compute_effective_status(pa_dict.get("status"), pa_dict.get("target_date"))
            all_plans_map.setdefault(pa_dict["proposal_id"], []).append(pa_dict)

    proposals_map = {}
    for p in all_proposals:
        p_id = p["id"]
        plans = all_plans_map.get(p_id, [])
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
        proposals_map.setdefault(p["finding_id"], []).append(p)

    for f in findings:
        f_id = f["id"]
        props = proposals_map.get(f_id, [])
        f["proposals"] = props
        f["proposals_count"] = len(props)
        f["action_plans_count"] = sum(p["action_plans_count"] for p in props)

        f_eff = compute_effective_status(f.get("status"), None)
        if f_eff == "En proceso" and props:
            if any(p.get("effective_status") == "Vencido" for p in props):
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

    if not proposals:
        conn.close()
        return []

    prop_ids = [p["id"] for p in proposals]
    pl_placeholders = ", ".join(["?"] * len(prop_ids))
    cursor.execute(f"SELECT * FROM action_plans WHERE proposal_id IN ({pl_placeholders}) ORDER BY code ASC", prop_ids)

    plans_map = {}
    for pa in cursor.fetchall():
        pa_dict = dict(pa)
        pa_dict["effective_status"] = compute_effective_status(pa_dict.get("status"), pa_dict.get("target_date"))
        plans_map.setdefault(pa_dict["proposal_id"], []).append(pa_dict)

    conn.close()

    for p in proposals:
        p_id = p["id"]
        plans = plans_map.get(p_id, [])
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

    if new_p_status == "En proceso":
        cursor.execute("UPDATE proposals SET status = ?, validated_at = NULL, validated_by = NULL, last_updated = CURRENT_TIMESTAMP WHERE id = ?", (new_p_status, proposal_id))
    elif new_p_status != p_curr_status:
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

    if new_f_status == "En proceso":
        cursor.execute("UPDATE findings SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", (new_f_status, finding_id))
    elif new_f_status != f_curr_status:
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
                if confirm_finalize:
                    final_status = "Finalizado"
                else:
                    final_status = "Pendiente de validación"
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
            fields.append("validated_at = NULL")
            fields.append("validated_by = NULL")

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


def get_executive_kpis(filters=None):
    filters = filters or {}
    report_filter = filters.get("report_id") or filters.get("report")
    area_filter = filters.get("area")
    period_filter = filters.get("period")

    init_db()
    conn = get_db()
    cursor = conn.cursor()

    try:
        today_date = get_argentina_today()
        today_str = today_date.strftime("%Y-%m-%d")

        # 0. Dropdown options for filters
        cursor.execute("SELECT DISTINCT id, code, title FROM reports ORDER BY title")
        report_options = [{"id": r["id"], "code": r["code"], "title": r["title"]} for r in cursor.fetchall()]

        cursor.execute("""
            SELECT DISTINCT responsible_area FROM findings WHERE responsible_area IS NOT NULL AND responsible_area != ''
            UNION
            SELECT DISTINCT responsible_area FROM proposals WHERE responsible_area IS NOT NULL AND responsible_area != ''
            ORDER BY 1
        """)
        area_options = [r[0] for r in cursor.fetchall() if r[0]]

        cursor.execute("SELECT DISTINCT period FROM reports WHERE period IS NOT NULL AND period != '' ORDER BY period DESC")
        period_options = [r[0] for r in cursor.fetchall() if r[0]]

        # Base filter clauses
        w_clauses = []
        params = []
        if report_filter:
            w_clauses.append("(r.id = ? OR r.code = ?)")
            params.extend([report_filter, report_filter])
        if area_filter:
            w_clauses.append("(f.responsible_area = ? OR p.responsible_area = ?)")
            params.extend([area_filter, area_filter])
        if period_filter:
            w_clauses.append("(r.period = ? OR r.period LIKE ?)")
            params.extend([period_filter, f"%{period_filter}%"])

        where_str = (" WHERE " + " AND ".join(w_clauses)) if w_clauses else ""

        # Scope Counts
        cursor.execute(f"""
            SELECT COUNT(DISTINCT r.id), COUNT(DISTINCT f.id), COUNT(DISTINCT p.id), COUNT(DISTINCT pa.id)
            FROM reports r
            LEFT JOIN findings f ON f.report_id = r.id
            LEFT JOIN proposals p ON p.finding_id = f.id
            LEFT JOIN action_plans pa ON pa.proposal_id = p.id
            {where_str}
        """, params)
        sc = cursor.fetchone()
        scope_counts = {
            "reports": sc[0] or 0,
            "findings": sc[1] or 0,
            "proposals": sc[2] or 0,
            "plans": sc[3] or 0,
            "action_plans": sc[3] or 0
        }

        # 1. Riesgo Alto Abierto
        w_high = list(params)
        where_high = where_str + (" AND " if where_str else " WHERE ") + "LOWER(f.severity) = 'alto' AND LOWER(f.status) NOT IN ('finalizado', 'completado', 'cerrado', 'archivado')"
        cursor.execute(f"""
            SELECT COUNT(DISTINCT f.id), COUNT(DISTINCT f.report_id)
            FROM findings f
            JOIN reports r ON f.report_id = r.id
            LEFT JOIN proposals p ON p.finding_id = f.id
            LEFT JOIN action_plans pa ON pa.proposal_id = p.id
            {where_high}
        """, w_high)
        r_high = cursor.fetchone()
        high_risk_cnt = r_high[0] or 0
        high_risk_reports = r_high[1] or 0

        # 2. Compromisos Vencidos (Activos vs Suspendidos)
        where_overdue = where_str + (" AND " if where_str else " WHERE ") + """
            pa.target_date IS NOT NULL AND pa.target_date != '' AND pa.target_date < ?
            AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado', 'archivado', 'en suspensión', 'en suspension', 'stand-by')
        """
        w_overdue = list(params) + [today_str]
        cursor.execute(f"""
            SELECT COUNT(DISTINCT pa.id), COUNT(DISTINCT p.finding_id)
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_overdue}
        """, w_overdue)
        r_ov = cursor.fetchone()
        overdue_plans_cnt = r_ov[0] or 0
        overdue_findings_cnt = r_ov[1] or 0

        where_susp = where_str + (" AND " if where_str else " WHERE ") + """
            pa.target_date IS NOT NULL AND pa.target_date != '' AND pa.target_date < ?
            AND LOWER(pa.status) IN ('en suspensión', 'en suspension', 'stand-by')
        """
        w_susp = list(params) + [today_str]
        cursor.execute(f"""
            SELECT COUNT(DISTINCT pa.id)
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_susp}
        """, w_susp)
        suspended_overdue_cnt = cursor.fetchone()[0] or 0

        # 3. Implementación Validada
        cursor.execute(f"""
            SELECT COUNT(DISTINCT p.id)
            FROM proposals p
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            LEFT JOIN action_plans pa ON pa.proposal_id = p.id
            {where_str}
        """, params)
        total_proposals = cursor.fetchone()[0] or 0

        where_val = where_str + (" AND " if where_str else " WHERE ") + """
            LOWER(p.status) IN ('finalizado', 'completado', 'cerrado', 'validado')
            AND p.validated_by IS NOT NULL AND p.validated_by != ''
        """
        cursor.execute(f"""
            SELECT COUNT(DISTINCT p.id)
            FROM proposals p
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            LEFT JOIN action_plans pa ON pa.proposal_id = p.id
            {where_val}
        """, params)
        validated_proposals_cnt = cursor.fetchone()[0] or 0

        if total_proposals > 0:
            val_pct = round((validated_proposals_cnt / total_proposals) * 100, 1)
            val_pct_str = f"{val_pct}%"
            val_context = f"{validated_proposals_cnt} de {total_proposals} propuestas"
            val_has_data = True
        else:
            val_pct_str = "Sin datos suficientes"
            val_context = "0 propuestas en el alcance seleccionado"
            val_has_data = False

        # 4. Cierre en Plazo
        where_closed_ontime = where_str + (" AND " if where_str else " WHERE ") + """
            LOWER(pa.status) IN ('finalizado', 'completado', 'cerrado')
            AND pa.closed_date IS NOT NULL AND pa.closed_date != ''
            AND pa.target_date IS NOT NULL AND pa.target_date != ''
            AND pa.closed_date <= pa.target_date
        """
        cursor.execute(f"""
            SELECT COUNT(DISTINCT pa.id)
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_closed_ontime}
        """, params)
        on_time_cnt = cursor.fetchone()[0] or 0

        where_closed_eval = where_str + (" AND " if where_str else " WHERE ") + """
            LOWER(pa.status) IN ('finalizado', 'completado', 'cerrado')
            AND pa.closed_date IS NOT NULL AND pa.closed_date != ''
            AND pa.target_date IS NOT NULL AND pa.target_date != ''
        """
        cursor.execute(f"""
            SELECT COUNT(DISTINCT pa.id)
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_closed_eval}
        """, params)
        evaluable_cnt = cursor.fetchone()[0] or 0

        where_closed_excl = where_str + (" AND " if where_str else " WHERE ") + """
            LOWER(pa.status) IN ('finalizado', 'completado', 'cerrado')
            AND (pa.closed_date IS NULL OR pa.closed_date = '' OR pa.target_date IS NULL OR pa.target_date = '')
        """
        cursor.execute(f"""
            SELECT COUNT(DISTINCT pa.id)
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_closed_excl}
        """, params)
        excluded_cnt = cursor.fetchone()[0] or 0

        if evaluable_cnt > 0:
            ontime_pct = round((on_time_cnt / evaluable_cnt) * 100, 1)
            ontime_pct_str = f"{ontime_pct}%"
            ontime_context = f"{on_time_cnt} de {evaluable_cnt} cierres evaluables ({excluded_cnt} excluidos sin fecha)"
            ontime_has_data = True
        else:
            ontime_pct_str = "Sin datos suficientes"
            ontime_context = f"Sin cierres evaluables ({excluded_cnt} excluidos sin fecha)"
            ontime_has_data = False

        # 5. Pendiente de Validación
        where_pending = where_str + (" AND " if where_str else " WHERE ") + """
            (pa.progress_pct = 100 OR LOWER(pa.status) = 'pendiente de validación')
            AND LOWER(pa.status) NOT IN ('finalizado', 'validado', 'cerrado')
        """
        cursor.execute(f"""
            SELECT COUNT(DISTINCT pa.id)
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_pending}
        """, params)
        pending_val_cnt = cursor.fetchone()[0] or 0

        # Chart 1: Comparación por Área
        cursor.execute(f"""
            SELECT COALESCE(NULLIF(f.responsible_area, ''), NULLIF(p.responsible_area, ''), 'Sin área') as area_name,
                   COUNT(DISTINCT CASE WHEN LOWER(f.severity) = 'alto' AND LOWER(f.status) NOT IN ('finalizado', 'completado', 'cerrado') THEN f.id END) as high_risk_cnt,
                   COUNT(DISTINCT CASE WHEN pa.target_date IS NOT NULL AND pa.target_date != '' AND pa.target_date < ? AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado', 'en suspensión', 'en suspension', 'stand-by') THEN pa.id END) as overdue_cnt
            FROM reports r
            JOIN findings f ON f.report_id = r.id
            LEFT JOIN proposals p ON p.finding_id = f.id
            LEFT JOIN action_plans pa ON pa.proposal_id = p.id
            {where_str}
            GROUP BY area_name
            HAVING high_risk_cnt > 0 OR overdue_cnt > 0
            ORDER BY overdue_cnt DESC, high_risk_cnt DESC
        """, [today_str] + params)
        area_rows = cursor.fetchall()
        chart_area = {
            "labels": [r["area_name"] for r in area_rows],
            "high_risk": [r["high_risk_cnt"] for r in area_rows],
            "overdue": [r["overdue_cnt"] for r in area_rows]
        }

        # Chart 2: Antigüedad de Vencimientos
        cursor.execute(f"""
            SELECT pa.target_date
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            {where_overdue}
        """, w_overdue)
        aging_counts = {"1-30 días": 0, "31-60 días": 0, "Más de 60 días": 0}
        for row in cursor.fetchall():
            t_str = row["target_date"]
            try:
                t_dt = datetime.strptime(t_str[:10], "%Y-%m-%d").date()
                delta = (today_date - t_dt).days
                if delta <= 30:
                    aging_counts["1-30 días"] += 1
                elif delta <= 60:
                    aging_counts["31-60 días"] += 1
                else:
                    aging_counts["Más de 60 días"] += 1
            except Exception:
                aging_counts["1-30 días"] += 1

        chart_aging = {
            "labels": list(aging_counts.keys()),
            "data": list(aging_counts.values())
        }

        # Follow-up Agenda
        cursor.execute(f"""
            SELECT pa.id as plan_id, pa.code as plan_code, pa.title as plan_title, pa.action_text, pa.target_date, pa.status as plan_status, pa.progress_pct, pa.action_owner, p.responsible_area as plan_area,
                   p.id as proposal_id, p.code as proposal_code, p.title as proposal_title, p.severity as proposal_severity,
                   f.id as finding_id, f.code as finding_code, f.title as finding_title, f.severity as finding_severity, f.responsible_area as finding_area,
                   r.code as report_code, r.title as report_title
            FROM action_plans pa
            JOIN proposals p ON pa.proposal_id = p.id
            JOIN findings f ON p.finding_id = f.id
            JOIN reports r ON f.report_id = r.id
            WHERE (
               (pa.target_date IS NOT NULL AND pa.target_date != '' AND pa.target_date < ? AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado', 'en suspensión', 'en suspension', 'stand-by'))
               OR (pa.progress_pct = 100 OR LOWER(pa.status) = 'pendiente de validación') AND LOWER(pa.status) NOT IN ('finalizado', 'validado', 'cerrado')
            )
            {" AND " + " AND ".join(w_clauses) if w_clauses else ""}
        """, [today_str] + params)

        agenda_items = []
        for r in cursor.fetchall():
            t_str = r["target_date"]
            days_ov = 0
            if t_str:
                try:
                    t_dt = datetime.strptime(t_str[:10], "%Y-%m-%d").date()
                    if t_dt < today_date:
                        days_ov = (today_date - t_dt).days
                except Exception:
                    pass

            p_pct = r["progress_pct"] or 0
            p_stat = r["plan_status"]
            sev = r["finding_severity"] or r["proposal_severity"] or "Medio"

            if p_pct == 100 or str(p_stat).lower() == "pendiente de validación":
                next_act = "Validación formal por auditoría"
            elif days_ov > 0:
                next_act = "Seguimiento plan vencido con área"
            else:
                next_act = "Gestionar implementación"

            agenda_items.append({
                "plan_id": r["plan_id"],
                "plan_code": r["plan_code"],
                "proposal_code": r["proposal_code"],
                "finding_code": r["finding_code"],
                "finding_title": r["finding_title"],
                "proposal_title": r["proposal_title"],
                "report_title": r["report_title"],
                "action_text": r["action_text"] or r["plan_title"],
                "description": r["action_text"] or r["plan_title"] or r["proposal_title"],
                "responsible_area": r["plan_area"] or r["finding_area"] or "Sin área",
                "area": r["plan_area"] or r["finding_area"] or "Sin área",
                "action_owner": r["action_owner"] or "Sin asignar",
                "owner": r["action_owner"] or "Sin asignar",
                "target_date": t_str or "Sin fecha",
                "days_overdue": days_ov,
                "overdue_days": days_ov,
                "status": p_stat,
                "progress_pct": p_pct,
                "severity": sev,
                "risk_level": sev,
                "next_action": next_act
            })

        sev_rank = {"Alto": 1, "Medio": 2, "Bajo": 3}
        agenda_items.sort(key=lambda x: (sev_rank.get(x["severity"], 2), -x["days_overdue"]))

        formatted_kpis = {
            "high_risk_open": {
                "count": high_risk_cnt,
                "subtitle": f"{high_risk_cnt} hallazgos de riesgo alto abiertos"
            },
            "overdue_commitments": {
                "count": overdue_plans_cnt,
                "subtitle": f"{overdue_plans_cnt} planes activos vencidos ({suspended_overdue_cnt} suspendidos)"
            },
            "pending_validation": {
                "count": pending_val_cnt,
                "subtitle": f"{pending_val_cnt} compromisos al 100%"
            },
            "validated_implementation": {
                "count": validated_proposals_cnt,
                "total": total_proposals,
                "rate": round(val_pct, 1) if val_has_data else None,
                "percentage": val_pct_str,
                "subtitle": val_context
            },
            "on_time_closing": {
                "count": on_time_cnt,
                "evaluable_total": evaluable_cnt,
                "excluded_count": excluded_cnt,
                "rate": round(ontime_pct, 1) if ontime_has_data else None,
                "percentage": ontime_pct_str,
                "subtitle": ontime_context
            },
            "on_time_closure": {
                "count": on_time_cnt,
                "evaluable_total": evaluable_cnt,
                "excluded_count": excluded_cnt,
                "rate": round(ontime_pct, 1) if ontime_has_data else None,
                "percentage": ontime_pct_str,
                "subtitle": ontime_context
            }
        }

        area_chart_list = []
        for r in area_rows:
            area_chart_list.append({
                "area": r["area_name"],
                "high_risk_open": r["high_risk_cnt"],
                "overdue": r["overdue_cnt"]
            })

        formatted_charts = {
            "by_area": area_chart_list,
            "aging": {
                "1_30": aging_counts.get("1-30 días", 0),
                "31_60": aging_counts.get("31-60 días", 0),
                ">60": aging_counts.get("Más de 60 días", 0)
            },
            "area_comparison": chart_area,
            "aging_breakdown": chart_aging
        }

        return {
            "cut_date": today_str,
            "scope": scope_counts,
            "filter_options": {
                "reports": report_options,
                "areas": area_options,
                "periods": period_options
            },
            "kpis": formatted_kpis,
            "cards": {
                "high_risk_open": {
                    "count": high_risk_cnt,
                    "affected_reports": high_risk_reports,
                    "label": "Riesgo Alto Abierto",
                    "context": f"{high_risk_cnt} hallazgos de riesgo alto abiertos"
                },
                "overdue_commitments": {
                    "count": overdue_plans_cnt,
                    "affected_findings": overdue_findings_cnt,
                    "suspended_count": suspended_overdue_cnt,
                    "label": "Compromisos Vencidos",
                    "context": f"{overdue_plans_cnt} planes activos vencidos ({suspended_overdue_cnt} suspendidos)"
                },
                "pending_validation": {
                    "count": pending_val_cnt,
                    "label": "Pendiente de Validación",
                    "context": f"{pending_val_cnt} compromisos al 100%"
                },
                "validated_implementation": {
                    "count": validated_proposals_cnt,
                    "total": total_proposals,
                    "percentage": val_pct_str,
                    "has_data": val_has_data,
                    "label": "Implementación Validada",
                    "context": val_context
                },
                "on_time_closure": {
                    "count": on_time_cnt,
                    "evaluable_total": evaluable_cnt,
                    "excluded_count": excluded_cnt,
                    "percentage": ontime_pct_str,
                    "has_data": ontime_has_data,
                    "label": "Cierre en Plazo",
                    "context": ontime_context
                }
            },
            "charts": formatted_charts,
            "agenda": agenda_items
        }
    finally:
        conn.close()


def get_executive_drilldown(metric_key, filters=None):
    filters = filters or {}
    report_filter = filters.get("report_id") or filters.get("report")
    area_filter = filters.get("area")
    period_filter = filters.get("period")

    init_db()
    conn = get_db()
    cursor = conn.cursor()

    try:
        today_date = get_argentina_today()
        today_str = today_date.strftime("%Y-%m-%d")

        w_clauses = []
        params = []
        if report_filter:
            w_clauses.append("(r.id = ? OR r.code = ?)")
            params.extend([report_filter, report_filter])
        if area_filter:
            w_clauses.append("(f.responsible_area = ? OR p.responsible_area = ?)")
            params.extend([area_filter, area_filter])
        if period_filter:
            w_clauses.append("(r.period = ? OR r.period LIKE ?)")
            params.extend([period_filter, f"%{period_filter}%"])

        where_str = (" WHERE " + " AND ".join(w_clauses)) if w_clauses else ""

        rows = []
        if metric_key == "high_risk_open":
            where_h = where_str + (" AND " if where_str else " WHERE ") + "LOWER(f.severity) = 'alto' AND LOWER(f.status) NOT IN ('finalizado', 'completado', 'cerrado', 'archivado')"
            cursor.execute(f"""
                SELECT DISTINCT f.id, f.code, f.title, f.severity, f.responsible_area, f.status, r.title as report_title
                FROM findings f
                JOIN reports r ON f.report_id = r.id
                LEFT JOIN proposals p ON p.finding_id = f.id
                LEFT JOIN action_plans pa ON pa.proposal_id = p.id
                {where_h}
                ORDER BY f.code ASC
            """, params)
            for r in cursor.fetchall():
                rows.append({
                    "id": r["id"],
                    "code": r["code"],
                    "title": r["title"],
                    "risk_level": r["severity"],
                    "severity": r["severity"],
                    "area": r["responsible_area"] or "Sin área",
                    "status": r["status"],
                    "report_title": r["report_title"]
                })

        elif metric_key == "overdue_commitments":
            where_ov = where_str + (" AND " if where_str else " WHERE ") + """
                pa.target_date IS NOT NULL AND pa.target_date != '' AND pa.target_date < ?
                AND LOWER(pa.status) NOT IN ('finalizado', 'completado', 'cerrado', 'archivado', 'en suspensión', 'en suspension', 'stand-by')
            """
            cursor.execute(f"""
                SELECT DISTINCT pa.id, pa.code, pa.title, pa.action_text, pa.target_date, pa.status, pa.progress_pct, pa.action_owner, p.responsible_area,
                       f.code as finding_code, f.severity as finding_severity, r.title as report_title
                FROM action_plans pa
                JOIN proposals p ON pa.proposal_id = p.id
                JOIN findings f ON p.finding_id = f.id
                JOIN reports r ON f.report_id = r.id
                {where_ov}
                ORDER BY pa.target_date ASC
            """, [today_str] + params)
            for r in cursor.fetchall():
                t_str = r["target_date"]
                days_ov = 0
                if t_str:
                    try:
                        t_dt = datetime.strptime(t_str[:10], "%Y-%m-%d").date()
                        days_ov = (today_date - t_dt).days
                    except Exception:
                        pass
                rows.append({
                    "id": r["id"],
                    "code": r["code"],
                    "finding_code": r["finding_code"],
                    "title": r["action_text"] or r["title"],
                    "target_date": t_str,
                    "days_overdue": days_ov,
                    "status": r["status"],
                    "risk_level": r["finding_severity"] or "Medio",
                    "progress_pct": r["progress_pct"] or 0,
                    "owner": r["action_owner"] or "Sin asignar",
                    "area": r["responsible_area"] or "Sin área",
                    "report_title": r["report_title"]
                })

        elif metric_key == "pending_validation":
            where_pv = where_str + (" AND " if where_str else " WHERE ") + """
                (pa.progress_pct = 100 OR LOWER(pa.status) = 'pendiente de validación')
                AND LOWER(pa.status) NOT IN ('finalizado', 'validado', 'cerrado')
            """
            cursor.execute(f"""
                SELECT DISTINCT pa.id, pa.code, pa.title, pa.action_text, pa.target_date, pa.status, pa.progress_pct, pa.action_owner, p.responsible_area,
                       f.code as finding_code, f.severity as finding_severity, r.title as report_title
                FROM action_plans pa
                JOIN proposals p ON pa.proposal_id = p.id
                JOIN findings f ON p.finding_id = f.id
                JOIN reports r ON f.report_id = r.id
                {where_pv}
                ORDER BY pa.code ASC
            """, params)
            for r in cursor.fetchall():
                rows.append({
                    "id": r["id"],
                    "code": r["code"],
                    "finding_code": r["finding_code"],
                    "title": r["action_text"] or r["title"],
                    "target_date": r["target_date"] or "Sin fecha",
                    "status": r["status"],
                    "risk_level": r["finding_severity"] or "Medio",
                    "progress_pct": r["progress_pct"] or 100,
                    "owner": r["action_owner"] or "Sin asignar",
                    "area": r["responsible_area"] or "Sin área",
                    "report_title": r["report_title"]
                })

        elif metric_key == "validated_implementation":
            cursor.execute(f"""
                SELECT DISTINCT p.id, p.code, p.title, p.status, p.validated_at, p.validated_by, p.responsible_area, f.code as finding_code, f.severity as finding_severity, r.title as report_title
                FROM proposals p
                JOIN findings f ON p.finding_id = f.id
                JOIN reports r ON f.report_id = r.id
                LEFT JOIN action_plans pa ON pa.proposal_id = p.id
                {where_str}
                ORDER BY p.code ASC
            """, params)
            for r in cursor.fetchall():
                is_val = bool(r["validated_by"] and str(r["status"]).lower() in ('finalizado', 'completado', 'cerrado', 'validado'))
                rows.append({
                    "id": r["id"],
                    "code": r["code"],
                    "finding_code": r["finding_code"],
                    "title": r["title"],
                    "status": r["status"],
                    "risk_level": r["finding_severity"] or "Medio",
                    "is_validated": is_val,
                    "validated_by": r["validated_by"] or "Sin validar",
                    "validated_at": r["validated_at"] or "-",
                    "area": r["responsible_area"] or "Sin área",
                    "report_title": r["report_title"]
                })

        elif metric_key in ("on_time_closure", "on_time_closing"):
            where_cl = where_str + (" AND " if where_str else " WHERE ") + "LOWER(pa.status) IN ('finalizado', 'completado', 'cerrado')"
            cursor.execute(f"""
                SELECT DISTINCT pa.id, pa.code, pa.title, pa.action_text, pa.target_date, pa.closed_date, pa.status, p.responsible_area,
                       f.code as finding_code, f.severity as finding_severity, r.title as report_title
                FROM action_plans pa
                JOIN proposals p ON pa.proposal_id = p.id
                JOIN findings f ON p.finding_id = f.id
                JOIN reports r ON f.report_id = r.id
                {where_cl}
                ORDER BY pa.code ASC
            """, params)
            for r in cursor.fetchall():
                t_str = r["target_date"]
                c_str = r["closed_date"]
                if t_str and c_str:
                    compliance = "En plazo" if c_str <= t_str else "Fuera de plazo"
                else:
                    compliance = "Excluido (sin fecha)"
                rows.append({
                    "id": r["id"],
                    "code": r["code"],
                    "finding_code": r["finding_code"],
                    "title": r["action_text"] or r["title"],
                    "target_date": t_str or "Sin fecha",
                    "closed_date": c_str or "Sin fecha",
                    "compliance": compliance,
                    "status": r["status"],
                    "risk_level": r["finding_severity"] or "Medio",
                    "area": r["responsible_area"] or "Sin área",
                    "report_title": r["report_title"]
                })

        return rows
    finally:
        conn.close()


def get_kpi_indicators(filters=None):
    return get_executive_kpis(filters)


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


def validate_proposal(proposal_id: str, user_name: str = "Luciana Gamarra"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, finding_id, status FROM proposals WHERE id = ? OR code = ?", (proposal_id, proposal_id))
        p_row = cursor.fetchone()
        if not p_row:
            return False, "Propuesta de mejora no encontrada."

        actual_id = p_row[0]
        finding_id = p_row[1]

        cursor.execute("""
            SELECT COUNT(*), 
                   SUM(CASE WHEN progress_pct < 100 OR LOWER(status) IN ('en proceso', 'en suspensión', 'en suspension', 'stand-by') THEN 1 ELSE 0 END)
            FROM action_plans
            WHERE proposal_id = ?
        """, (actual_id,))
        plans_stat = cursor.fetchone()
        plan_count = plans_stat[0] or 0
        incomplete_plans = plans_stat[1] or 0

        if plan_count > 0 and incomplete_plans > 0:
            return False, "No se puede validar una propuesta que tiene planes incompletos o en suspensión."

        now_str = datetime.now().isoformat()
        today_str = date.today().strftime("%Y-%m-%d")

        cursor.execute("""
            UPDATE proposals
            SET status = 'Finalizado', validated_at = ?, validated_by = ?, last_updated = ?
            WHERE id = ?
        """, (now_str, user_name, now_str, actual_id))

        if plan_count > 0:
            cursor.execute("""
                UPDATE action_plans
                SET status = 'Finalizado', progress_pct = 100, closed_date = ?, validated_at = ?, validated_by = ?, last_updated = ?
                WHERE proposal_id = ?
            """, (today_str, now_str, user_name, now_str, actual_id))

        add_history_log("proposal", actual_id, user_name, f"Validación formal registrada por {user_name}", cursor=cursor)

        if finding_id:
            _evaluate_finding_cascade(cursor, finding_id)

        conn.commit()
        return True, "Propuesta validada exitosamente."
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def validate_action_plan(plan_id: str, user_name: str = "Luciana Gamarra"):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, proposal_id FROM action_plans WHERE id = ? OR code = ?", (plan_id, plan_id))
        pa_row = cursor.fetchone()
        if not pa_row:
            return False, "Plan de acción no encontrado."

        actual_id = pa_row[0]
        prop_id = pa_row[1]

        now_str = datetime.now().isoformat()
        today_str = date.today().strftime("%Y-%m-%d")

        cursor.execute("""
            UPDATE action_plans
            SET status = 'Finalizado', progress_pct = 100, closed_date = ?, validated_at = ?, validated_by = ?, last_updated = ?
            WHERE id = ?
        """, (today_str, now_str, user_name, now_str, actual_id))

        add_history_log("action_plan", actual_id, user_name, f"Validación formal registrada por {user_name}", cursor=cursor)

        if prop_id:
            _evaluate_proposal_cascade(cursor, prop_id)

        conn.commit()
        return True, "Plan de acción validado exitosamente."
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
