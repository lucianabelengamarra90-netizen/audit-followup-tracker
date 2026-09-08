import sqlite3
import os
import uuid
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_tracker.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            code TEXT NOT NULL,
            type TEXT NOT NULL,
            process_step TEXT,
            title TEXT NOT NULL,
            situation TEXT NOT NULL,
            risk TEXT,
            proposal TEXT,
            severity TEXT DEFAULT 'Media',
            responsible_area TEXT,
            action_owner TEXT,
            target_date TEXT,
            status TEXT DEFAULT 'Pendiente',
            follow_up_notes TEXT,
            evidence_file TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def seed_initial_data():
    items = [
        {"code": "H-2026-001", "type": "Hallazgo", "title": "Diferencias en saldos de proveedores", "responsible_area": "Contabilidad", "report_title": "Cierre 30/06/2026", "source_filename": "Proveedores_30-06.xlsx", "severity": "Alto", "status": "En proceso", "action_owner": "Hernán López", "target_date": "30/09/2026", "situation": "Discrepancias no conciliadas en saldos informados por proveedores."},
        {"code": "H-2026-002", "type": "Hallazgo", "title": "Materiales con vida útil igual a 0", "responsible_area": "Abastecimiento", "report_title": "Bienes de Uso 2026", "source_filename": "VidaUtil_2026.xlsx", "severity": "Medio", "status": "Pendiente", "action_owner": "Kari Gómez", "target_date": "15/10/2026", "situation": "Repuestos parametrizados con vida útil nula en el sistema."},
        {"code": "H-2026-003", "type": "Propuesta", "title": "Implementar control automático de VU < 75%", "responsible_area": "Abastecimiento", "report_title": "Bienes de Uso 2026", "source_filename": "VidaUtil_2026.xlsx", "severity": "Medio", "status": "En proceso", "action_owner": "Iván Torres", "target_date": "31/10/2026", "situation": "Validación automática de vida útil remanente."},
        {"code": "H-2026-004", "type": "Hallazgo", "title": "Duplicación de consumos en Gift Cards", "responsible_area": "Operaciones / POS", "report_title": "Gift Cards 2026", "source_filename": "GiftCards_ene-jun.xlsx", "severity": "Alto", "status": "En proceso", "action_owner": "Luis Martínez", "target_date": "15/09/2026", "situation": "Consumos simultáneos en POS con la misma tarjeta."},
        {"code": "H-2026-005", "type": "Propuesta", "title": "Desarrollar alerta de duplicados en POS", "responsible_area": "Operaciones / POS", "report_title": "Gift Cards 2026", "source_filename": "GiftCards_ene-jun.xlsx", "severity": "Alto", "status": "Planificada", "action_owner": "Mariano Ruiz", "target_date": "31/10/2026", "situation": "Candado transaccional para Gift Cards en cajas."},
        {"code": "H-2026-006", "type": "Hallazgo", "title": "Faltantes y sobrantes sin motivo", "responsible_area": "Tiendas", "report_title": "Faltantes y Sobrantes", "source_filename": "Faltantes_Sobrantes.xlsx", "severity": "Medio", "status": "En proceso", "action_owner": "Guadalupe Méndez", "target_date": "20/09/2026", "situation": "Diferencias en recuentos físicos de inventario."},
        {"code": "H-2026-007", "type": "Propuesta", "title": "Actualizar Manual de Conteo a Ciegas", "responsible_area": "Tiendas", "report_title": "Faltantes y Sobrantes", "source_filename": "Manual_Conteo_Ciegas.pdf", "severity": "Bajo", "status": "Completada", "action_owner": "Daniel Jaime", "target_date": "31/08/2026", "situation": "Instructivo de conteo físico para personal de tiendas."},
        {"code": "H-2026-008", "type": "Hallazgo", "title": "Acuerdos comerciales sin aprobación formal", "responsible_area": "Compras", "report_title": "Acuerdos Comerciales", "source_filename": "Acuerdos_Comerciales.xlsx", "severity": "Alto", "status": "En proceso", "action_owner": "Lucas Pereyra", "target_date": "30/09/2026", "situation": "Condiciones acordadas sin firma ni autorización facultada."},
        {"code": "H-2026-009", "type": "Propuesta", "title": "Definir circuito de aprobación en SAP", "responsible_area": "Compras", "report_title": "Acuerdos Comerciales", "source_filename": "Acuerdos_Comerciales.xlsx", "severity": "Alto", "status": "Planificada", "action_owner": "Angie Torres", "target_date": "15/11/2026", "situation": "Flujo de firmas digitales SAP para acuerdos comerciales."},
        {"code": "H-2026-010", "type": "Hallazgo", "title": "Juicios con cobertura inferior al objetivo", "responsible_area": "Legales", "report_title": "Juicios 30/06/2026", "source_filename": "Juicios_2026.xlsx", "severity": "Medio", "status": "En proceso", "action_owner": "Eugenia Rojas", "target_date": "20/09/2026", "situation": "Montos de previsión por debajo de la pretensión judicada."},
        {"code": "H-2026-011", "type": "Propuesta", "title": "Revisión trimestral de previsiones", "responsible_area": "Legales", "report_title": "Juicios 30/06/2026", "source_filename": "Juicios_2026.xlsx", "severity": "Medio", "status": "Pendiente", "action_owner": "Eugenia Rojas", "target_date": "15/10/2026", "situation": "Comité de previsiones legales y contingencias."},
        {"code": "H-2026-012", "type": "Hallazgo", "title": "Oportunidades anuladas sin NC en POS", "responsible_area": "Operaciones / SF", "report_title": "Gift Cards 2026", "source_filename": "GiftCards_ene-jun.xlsx", "severity": "Alto", "status": "En proceso", "action_owner": "Virginia Díaz", "target_date": "30/09/2026", "situation": "Ventas anuladas en POS sin comprobante de Nota de Crédito."},
        {"code": "H-2026-013", "type": "Propuesta", "title": "Consolidación de tarjetas y depuración", "responsible_area": "Operaciones / SF", "report_title": "Gift Cards 2026", "source_filename": "GiftCards_ene-jun.xlsx", "severity": "Medio", "status": "Planificada", "action_owner": "Emmanuel López", "target_date": "31/10/2026", "situation": "Depuración de plásticos inactivos y unificación."},
        {"code": "H-2026-014", "type": "Hallazgo", "title": "Activos con amortización mínima o nula", "responsible_area": "Contabilidad", "report_title": "Bienes de Uso 2026", "source_filename": "BienesdeUso_2026.xlsx", "severity": "Medio", "status": "En proceso", "action_owner": "Kari Gómez", "target_date": "30/09/2026", "situation": "Bienes fijos sin cálculo automático de depreciación."}
    ]

    by_report = {}
    for item in items:
        rep_title = item["report_title"]
        if rep_title not in by_report:
            by_report[rep_title] = []
        by_report[rep_title].append(item)

    for rep_title, rep_items in by_report.items():
        rep_data = {
            "title": rep_title,
            "process": rep_items[0]["responsible_area"],
            "area": rep_items[0]["responsible_area"],
            "period": "2026",
            "auditor": "Luciana Gamarra",
            "summary": f"Informe {rep_title}"
        }
        save_report_and_findings(rep_data, rep_items, rep_items[0]["source_filename"], _is_seeding=True)


def save_report_and_findings(report_data, findings_data, source_filename="", _is_seeding=False):
    if not _is_seeding:
        init_db()
    conn = get_db()
    cursor = conn.cursor()

    report_id = str(uuid.uuid4())
    title = (report_data.get("title") or "Informe de Auditoría").strip()
    process = (report_data.get("process") or "Proceso General").strip()
    area = (report_data.get("area") or "Operaciones").strip()
    period = (report_data.get("period") or "").strip()
    auditor = (report_data.get("auditor") or "Auditoría Interna").strip()
    summary = (report_data.get("summary") or "").strip()

    cursor.execute("""
        INSERT INTO reports (id, title, process, area, period, auditor, summary, source_filename)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (report_id, title, process, area, period, auditor, summary, source_filename))

    saved_findings = []
    for idx, f in enumerate(findings_data, start=1):
        finding_id = str(uuid.uuid4())
        code = (f.get("code") or f"OBS-{idx:02d}").strip()
        f_type = (f.get("type") or "Oportunidad de Mejora").strip()
        process_step = (f.get("process_step") or process).strip()
        f_title = (f.get("title") or f"Mejora {idx}").strip()
        situation = (f.get("situation") or f.get("text") or "").strip()
        risk = (f.get("risk") or "").strip()
        proposal = (f.get("proposal") or "").strip()
        severity = (f.get("severity") or "Medio").strip()
        if severity in ("Alta", "ALTA", "alto", "ALTO"):
            severity = "Alto"
        elif severity in ("Baja", "BAJA", "bajo", "BAJO"):
            severity = "Bajo"
        else:
            severity = "Medio"
        responsible_area = (f.get("responsible_area") or area).strip()
        action_owner = (f.get("action_owner") or "Responsable del Plan").strip()
        target_date = (f.get("target_date") or "").strip()
        status = (f.get("status") or "Pendiente").strip()

        cursor.execute("""
            INSERT INTO findings (id, report_id, code, type, process_step, title, situation, risk, proposal, severity, responsible_area, action_owner, target_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (finding_id, report_id, code, f_type, process_step, f_title, situation, risk, proposal, severity, responsible_area, action_owner, target_date, status))

        saved_findings.append(finding_id)

    conn.commit()
    conn.close()
    return report_id, len(saved_findings)


def get_all_reports():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports ORDER BY created_at DESC")
    reports = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return reports


def get_report(report_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
    report = cursor.fetchone()
    if not report:
        conn.close()
        return None
    res = dict(report)
    cursor.execute("SELECT * FROM findings WHERE report_id = ? ORDER BY code ASC", (report_id,))
    res["findings"] = [dict(f) for f in cursor.fetchall()]
    conn.close()
    return res


def get_findings(filters=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT f.*, r.title as report_title, r.process as report_process, r.source_filename
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
        if filters.get("process"):
            query += " AND (LOWER(r.process) LIKE ? OR LOWER(f.process_step) LIKE ?)"
            params.append(f"%{filters['process'].lower()}%")
            params.append(f"%{filters['process'].lower()}%")
        if filters.get("area"):
            query += " AND LOWER(f.responsible_area) LIKE ?"
            params.append(f"%{filters['area'].lower()}%")
        if filters.get("search"):
            q = f"%{filters['search'].lower()}%"
            query += " AND (LOWER(f.title) LIKE ? OR LOWER(f.situation) LIKE ? OR LOWER(f.code) LIKE ?)"
            params.extend([q, q, q])

    query += " ORDER BY f.last_updated DESC"
    cursor.execute(query, params)
    findings = [dict(f) for f in cursor.fetchall()]
    conn.close()
    return findings


def get_finding(finding_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.*, r.title as report_title, r.process as report_process, r.source_filename
        FROM findings f
        JOIN reports r ON f.report_id = r.id
        WHERE f.id = ?
    """, (finding_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_finding_status(finding_id, status, notes=None, target_date=None, evidence_file=None):
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    fields = ["status = ?", "last_updated = CURRENT_TIMESTAMP"]
    params = [status]

    if notes is not None:
        fields.append("follow_up_notes = ?")
        params.append(notes)
    if target_date is not None:
        fields.append("target_date = ?")
        params.append(target_date)
    if evidence_file is not None:
        fields.append("evidence_file = ?")
        params.append(evidence_file)

    params.append(finding_id)
    sql = f"UPDATE findings SET {', '.join(fields)} WHERE id = ?"
    cursor.execute(sql, params)
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def delete_finding(finding_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM findings WHERE id = ?", (finding_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_report(report_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM findings WHERE report_id = ?", (report_id,))
    cursor.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_dashboard_kpis():
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM reports")
    total_reports = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM findings")
    total_findings = cursor.fetchone()[0]

    if total_findings == 0:
        conn.close()
        return {
            "total_reports": total_reports,
            "total_findings": 0,
            "closed_findings": 0,
            "pending_findings": 0,
            "in_progress_findings": 0,
            "in_review_findings": 0,
            "overdue_findings": 0,
            "resolution_rate": 0,
            "severity_breakdown": {"Alta": 0, "Media": 0, "Baja": 0},
            "status_breakdown": {},
            "area_breakdown": {}
        }

    cursor.execute("""
        SELECT status, COUNT(*) as cnt FROM findings GROUP BY status
    """)
    status_breakdown = {r["status"]: r["cnt"] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT severity, COUNT(*) as cnt FROM findings GROUP BY severity
    """)
    severity_breakdown = {"Alta": 0, "Media": 0, "Baja": 0}
    for r in cursor.fetchall():
        if r["severity"] in severity_breakdown:
            severity_breakdown[r["severity"]] = r["cnt"]

    cursor.execute("""
        SELECT responsible_area, COUNT(*) as total,
               SUM(CASE WHEN LOWER(status) LIKE '%cerrado%' OR LOWER(status) LIKE '%implementado%' THEN 1 ELSE 0 END) as closed
        FROM findings
        GROUP BY responsible_area
    """)
    area_breakdown = {
        r["responsible_area"]: {"total": r["total"], "closed": r["closed"]}
        for r in cursor.fetchall()
    }

    closed = status_breakdown.get("Implementado / Cerrado", 0) + status_breakdown.get("Cerrado", 0) + status_breakdown.get("Implementado", 0)
    pending = status_breakdown.get("Pendiente", 0)
    in_progress = status_breakdown.get("En Proceso", 0)
    in_review = status_breakdown.get("En Revisión", 0)

    # Hallazgos vencidos (fecha compromiso < hoy y no cerrado)
    today_str = date.today().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) FROM findings
        WHERE target_date != '' AND target_date < ?
          AND LOWER(status) NOT LIKE '%cerrado%' AND LOWER(status) NOT LIKE '%implementado%'
    """, (today_str,))
    overdue = cursor.fetchone()[0]

    resolution_rate = round((closed / total_findings) * 100, 1) if total_findings > 0 else 0

    conn.close()
    return {
        "total_reports": total_reports,
        "total_findings": total_findings,
        "closed_findings": closed,
        "pending_findings": pending,
        "in_progress_findings": in_progress,
        "in_review_findings": in_review,
        "overdue_findings": overdue,
        "resolution_rate": resolution_rate,
        "severity_breakdown": severity_breakdown,
        "status_breakdown": status_breakdown,
        "area_breakdown": area_breakdown
    }
