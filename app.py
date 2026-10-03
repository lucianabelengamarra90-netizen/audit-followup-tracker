from flask import Flask, render_template, request, jsonify, send_file
import os
import re
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from database import (
    init_db,
    get_db,
    save_relational_report_structure,
    get_all_reports,
    get_report_detail,
    get_all_findings,
    get_finding_detail,
    get_all_proposals,
    get_all_action_plans,
    create_action_plan,
    update_action_plan,
    update_finding,
    update_proposal,
    delete_finding,
    delete_report,
    create_proposal_for_finding,
    get_dashboard_stats,
    get_kpi_indicators,
    get_history_logs,
    add_history_log,
    get_active_alerts,
    get_proposal_by_id_or_code,
    get_executive_kpis,
    validate_proposal,
    validate_action_plan
)
from domain.auth import (
    get_current_user, login_user, logout_user, require_auth, require_role,
    ROLE_READER, ROLE_EDITOR, ROLE_VALIDATOR, DEMO_USERS
)
from report_parser import parse_audit_report, clean_text

init_db()

app = Flask(__name__)
is_prod_env = bool(os.environ.get("RENDER") or os.environ.get("IS_PRODUCTION") or os.environ.get("FLASK_ENV") == "production")
if is_prod_env:
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("PRODUCTION CONFIG ERROR: SECRET_KEY environment variable is mandatory in production.")
    app.secret_key = secret
else:
    app.secret_key = os.environ.get("SECRET_KEY", "audittrack-secret-key-2026-v1.2")

app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

import uuid

ALLOWED_EXTENSIONS = {"xlsx", "csv", "docx", "doc", "pdf", "txt"}
MAX_SIZE_MAP = {
    "pdf": 50 * 1024 * 1024,
    "xlsx": 50 * 1024 * 1024,
    "docx": 25 * 1024 * 1024,
    "doc": 25 * 1024 * 1024,
    "csv": 20 * 1024 * 1024,
    "txt": 20 * 1024 * 1024,
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_file_content(file_storage, ext):
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)

    max_size = MAX_SIZE_MAP.get(ext, 25 * 1024 * 1024)
    if size > max_size:
        return False, f"El archivo excede el tamaño máximo permitido ({max_size // (1024 * 1024)} MB).", 413
    if size == 0:
        return False, "El archivo subido está vacío (0 bytes).", 422

    header = file_storage.read(1024)
    file_storage.seek(0)

    if ext == "pdf":
        if not header.startswith(b"%PDF"):
            return False, "Firma de archivo inválida. El archivo no es un documento PDF válido.", 400

    return True, "", 200


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
@app.route("/api/health")
def health():
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    db_engine = "postgresql" if db_url and (db_url.startswith("postgresql://") or db_url.startswith("postgres://")) else "sqlite"
    
    db_status = "unknown"
    row_counts = {}
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        db_status = "connected"

        cursor.execute("SELECT COUNT(*) FROM reports")
        r_cnt = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM findings")
        f_cnt = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM proposals")
        p_cnt = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM action_plans")
        pa_cnt = cursor.fetchone()[0] or 0

        row_counts = {
            "reports": r_cnt,
            "findings": f_cnt,
            "proposals": p_cnt,
            "action_plans": pa_cnt
        }
        conn.close()
    except Exception as exc:
        db_status = f"error: {str(exc)}"

    status_code = 200 if db_status == "connected" else 503
    return jsonify({
        "status": "ok" if db_status == "connected" else "degraded",
        "app": "AuditTrack Relacional",
        "version": "v1.3.0",
        "base_tag": "v1.0.0-base-2026-10-02",
        "db_engine": db_engine,
        "db_status": db_status,
        "counts": row_counts,
        "ai_enabled": False,
        "timestamp": datetime.now().isoformat()
    }), status_code


@app.route("/api/user")
def api_user():
    user = get_current_user()
    return jsonify({"success": bool(user), "user": user})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        
        success, user_info = login_user(username, password)
        if success:
            return jsonify({"success": True, "user": user_info})
        return jsonify({"success": False, "error": "Credenciales inválidas. Verifique usuario y contraseña."}), 401
        
    return jsonify({"success": True, "current_user": get_current_user(), "available_demo_users": ["lector", "editor", "admin"]})


@app.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user()
    return jsonify({"success": True, "message": "Sesión cerrada correctamente."})


@app.route("/upload-report", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def upload_report():
    if "file" not in request.files:
        return jsonify({"error": "No se seleccionó ningún archivo de informe."}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "El archivo enviado no es válido."}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Formato no soportado. Usá PDF (.pdf), Word (.docx), Excel (.xlsx), CSV (.csv) o Texto (.txt)."}), 400

    ext = file.filename.rsplit(".", 1)[1].lower() if "." in file.filename else ""
    valid, err_msg, status_code = validate_file_content(file, ext)
    if not valid:
        return jsonify({"error": err_msg}), status_code

    safe_filename = clean_text(file.filename).replace(" ", "_")
    unique_filename = f"{uuid.uuid4().hex}_{safe_filename}"
    saved_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
    file.save(saved_path)

    try:
        parsed_data = parse_audit_report(saved_path, safe_filename)
        report_info = parsed_data.get("report", {})
        findings_hierarchy = parsed_data.get("findings", [])

        report_id = save_relational_report_structure(report_info, findings_hierarchy, safe_filename)

        return jsonify({
            "message": f"Informe '{report_info.get('title')}' ingresado correctamente con relaciones integradas.",
            "report_id": report_id,
            "findings_count": len(findings_hierarchy)
        })
    except ValueError as ve:
        err_str = str(ve)
        status = 422 if ("requiere OCR" in err_str or "vacío" in err_str or "texto" in err_str) else 400
        return jsonify({"error": err_str}), status
    except Exception as exc:
        print(f"Error procesando informe: {exc}")
        return jsonify({"error": f"No se pudo procesar el informe: {str(exc)}"}), 500
    finally:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except Exception as e:
                print(f"Error eliminando archivo temporal: {e}")


@app.route("/parse-preview", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def parse_preview():
    if "file" not in request.files:
        return jsonify({"error": "No se seleccionó ningún archivo de informe."}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "El archivo enviado no es válido."}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Formato no soportado. Usá PDF (.pdf), Word (.docx), Excel (.xlsx), CSV (.csv) o Texto (.txt)."}), 400

    ext = file.filename.rsplit(".", 1)[1].lower() if "." in file.filename else ""
    valid, err_msg, status_code = validate_file_content(file, ext)
    if not valid:
        return jsonify({"error": err_msg}), status_code

    safe_filename = clean_text(file.filename).replace(" ", "_")
    unique_filename = f"{uuid.uuid4().hex}_{safe_filename}"
    saved_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
    file.save(saved_path)

    try:
        parsed_data = parse_audit_report(saved_path, safe_filename)
        return jsonify({
            "report_file": safe_filename,
            "report": parsed_data.get("report", {}),
            "findings": parsed_data.get("findings", [])
        })
    except ValueError as ve:
        err_str = str(ve)
        status = 422 if ("requiere OCR" in err_str or "vacío" in err_str or "texto" in err_str) else 400
        return jsonify({"error": err_str}), status
    except Exception as exc:
        print(f"Error procesando vista previa: {exc}")
        return jsonify({"error": f"No se pudo procesar la vista previa: {str(exc)}"}), 500
    finally:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except Exception as e:
                print(f"Error eliminando archivo temporal: {e}")


@app.route("/save-validated-report", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def save_validated_report():
    data = request.get_json(silent=True) or {}
    report_info = data.get("report", {})
    findings_hierarchy = data.get("findings", [])
    source_filename = data.get("report_file", "Informe.docx")

    if not report_info or not findings_hierarchy:
        return jsonify({"error": "No hay datos validados para guardar."}), 400

    try:
        report_id = save_relational_report_structure(report_info, findings_hierarchy, source_filename)
        return jsonify({
            "message": f"Informe '{report_info.get('title')}' ingresado correctamente en AuditTrack.",
            "report_id": report_id,
            "findings_count": len(findings_hierarchy)
        })
    except Exception as exc:
        print(f"Error guardando informe validado: {exc}")
        return jsonify({"error": f"No se pudo guardar el informe: {str(exc)}"}), 500


@app.route("/reports", methods=["GET"])
@require_auth
def list_reports():
    reports = get_all_reports()
    return jsonify({"reports": reports, "count": len(reports)})


@app.route("/reports/<report_id>", methods=["GET", "DELETE"])
@require_auth
def report_detail_route(report_id):
    if request.method == "DELETE":
        user = get_current_user()
        if not user or user.get("role") != ROLE_VALIDATOR:
            return jsonify({"error": "Permisos insuficientes. Se requiere el rol Validador para eliminar informes."}), 403
        deleted = delete_report(report_id)
        if deleted:
            return jsonify({"message": "Informe eliminado correctamente."})
        return jsonify({"error": "Informe no encontrado."}), 404

    report = get_report_detail(report_id)
    if not report:
        return jsonify({"error": "Informe no encontrado."}), 404
    return jsonify(report)


@app.route("/findings", methods=["GET"])
@require_auth
def list_findings():
    filters = {
        "status": request.args.get("status"),
        "severity": request.args.get("severity"),
        "area": request.args.get("area"),
        "search": request.args.get("search")
    }
    findings = get_all_findings(filters)
    return jsonify({"findings": findings, "count": len(findings)})


@app.route("/findings/<finding_id>", methods=["GET", "DELETE"])
@require_auth
def finding_detail_route(finding_id):
    if request.method == "DELETE":
        user = get_current_user()
        if not user or user.get("role") != ROLE_VALIDATOR:
            return jsonify({"error": "Permisos insuficientes. Se requiere el rol Validador para eliminar hallazgos."}), 403
        deleted = delete_finding(finding_id)
        if deleted:
            return jsonify({"message": "Hallazgo eliminado."})
        return jsonify({"error": "Hallazgo no encontrado."}), 404

    finding = get_finding_detail(finding_id)
    if not finding:
        return jsonify({"error": "Hallazgo no encontrado."}), 404
    return jsonify(finding)


@app.route("/findings/<finding_id>/update", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def update_finding_route(finding_id):
    data = request.get_json(silent=True) or {}
    user = get_current_user()
    user_name = user.get("name") if user else "Auditoría Interna"
    updated = update_finding(finding_id, data, user_name)
    if updated:
        return jsonify({"message": "Hallazgo actualizado correctamente."})
    return jsonify({"error": "Hallazgo no encontrado o sin cambios."}), 404


@app.route("/findings/<finding_id>/add-proposal", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def add_proposal_to_finding_route(finding_id):
    data = request.get_json(silent=True) or {}
    proposal_text = data.get("proposal_text")
    if not proposal_text:
        return jsonify({"error": "La propuesta de mejora no puede estar vacía."}), 400

    user = get_current_user()
    user_name = user.get("name") if user else "Auditoría Interna"
    prop_id, p_code = create_proposal_for_finding(finding_id, proposal_text, user_name)
    if prop_id:
        return jsonify({"message": f"Propuesta {p_code} creada exitosamente.", "id": prop_id, "code": p_code})
    return jsonify({"error": "Hallazgo no encontrado."}), 404


@app.route("/proposals/<proposal_id>/update", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def update_proposal_route(proposal_id):
    data = request.get_json(silent=True) or {}
    user = get_current_user()
    user_name = user.get("name") if user else "Auditoría Interna"
    updated = update_proposal(proposal_id, data, user_name)
    if updated:
        return jsonify({"message": "Propuesta actualizada correctamente."})
    return jsonify({"error": "Propuesta no encontrada o sin cambios."}), 404


@app.route("/proposals", methods=["GET"])
@require_auth
def list_proposals():
    filters = {
        "status": request.args.get("status"),
        "search": request.args.get("search")
    }
    proposals = get_all_proposals(filters)
    return jsonify({"proposals": proposals, "count": len(proposals)})


@app.route("/action-plans", methods=["GET", "POST"])
@require_auth
def action_plans_route():
    if request.method == "POST":
        user = get_current_user()
        if not user or user.get("role") not in (ROLE_EDITOR, ROLE_VALIDATOR):
            return jsonify({"error": "Permisos insuficientes. Se requiere rol Editor o Validador para crear planes."}), 403

        data = request.get_json(silent=True) or {}
        finding_id = data.get("finding_id")
        proposal_id = data.get("proposal_id")

        if proposal_id in ("undefined", "null", ""):
            proposal_id = None

        if finding_id in ("undefined", "null", ""):
            finding_id = None

        action_text = data.get("action_text") or data.get("title")
        action_owner = data.get("action_owner") or (user.get("name") if user else "Auditoría Interna")
        target_date = data.get("target_date") or "2026-10-31"
        status = data.get("status") or "En proceso"
        progress_pct = int(data.get("progress_pct") or 0)
        notes = data.get("notes") or ""

        if not action_text:
            return jsonify({"error": "Debe definir la Acción Comprometida."}), 400

        if proposal_id:
            resolved_p = get_proposal_by_id_or_code(proposal_id)
            if resolved_p:
                proposal_id = resolved_p["id"]
            else:
                proposal_id = None

        if not proposal_id and finding_id:
            finding = get_finding_detail(finding_id)
            if finding:
                proposals = finding.get("proposals") or []
                if proposals:
                    proposal_id = proposals[0]["id"]
                else:
                    prop_text = f"Propuesta recomendada para {finding.get('code', 'Hallazgo')}: {action_text}"
                    new_prop_id, _ = create_proposal_for_finding(
                        finding_id=finding["id"],
                        proposal_text=prop_text,
                        action_owner=action_owner,
                        target_date=target_date,
                        status="En proceso"
                    )
                    proposal_id = new_prop_id

        if not proposal_id:
            return jsonify({"error": "Debe seleccionar una Propuesta de Mejora o un Hallazgo válido."}), 400

        plan_id, pa_code = create_action_plan(proposal_id, action_text, action_owner, target_date, status, progress_pct, notes)
        return jsonify({"message": f"Plan de Acción {pa_code} creado exitosamente.", "id": plan_id, "code": pa_code})

    filters = {
        "status": request.args.get("status"),
        "search": request.args.get("search")
    }
    plans = get_all_action_plans(filters)
    return jsonify({"action_plans": plans, "count": len(plans)})


@app.route("/action-plans/<plan_id>", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def update_action_plan_route(plan_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    progress_pct = data.get("progress_pct")
    notes = data.get("notes")
    target_date = data.get("target_date")
    evidence_file = data.get("evidence_file")
    action_owner = data.get("action_owner")
    user_name = data.get("user_name") or "Auditoría Interna"
    confirm_finalize = bool(data.get("confirm_finalize", False) or data.get("confirmed", False))

    updated = update_action_plan(plan_id, status, progress_pct, notes, target_date, evidence_file, action_owner, user_name, confirm_finalize)
    if updated:
        return jsonify({"message": "Plan de Acción actualizado."})
    return jsonify({"error": "Plan de Acción no encontrado."}), 404


@app.route("/dashboard-stats")
@require_auth
def dashboard_stats_route():
    stats = get_dashboard_stats()
    return jsonify(stats)


@app.route("/kpi-indicators")
@app.route("/api/kpi-executive")
@require_auth
def kpi_indicators_route():
    kpis = get_executive_kpis()
    return jsonify({"indicators": kpis, "kpis": kpis})


@app.route("/api/notifications")
@require_auth
def api_notifications():
    alerts = get_active_alerts()
    return jsonify(alerts)


def compute_effective_status(status_raw, target_date_str):
    raw = (status_raw or "En proceso").strip().lower()
    if any(w in raw for w in ["finalizado", "finalizada", "completada", "completado", "implementada", "archivada"]):
        return "Finalizado"
    if "suspensión" in raw or "suspension" in raw or "stand-by" in raw:
        return "En suspensión"

    if target_date_str:
        try:
            today = datetime.now().date()
            d = None
            t_str = str(target_date_str).strip()[:10]
            if "-" in t_str and len(t_str) == 10:
                d = datetime.strptime(t_str, "%Y-%m-%d").date()
            elif "/" in t_str:
                d = datetime.strptime(t_str, "%d/%m/%Y").date()

            if d and d < today:
                return "Vencido"
        except Exception:
            pass

    return "En proceso"


@app.route("/export-excel", methods=["POST", "GET"])
@require_auth
def export_excel():
    findings = get_all_findings()
    proposals = get_all_proposals()
    action_plans = get_all_action_plans()

    wb = Workbook()

    NAVY = "0F172A"
    TEXT_COLOR = "1F2937"
    BORDER_COLOR = "E2E8F0"
    THIN_BORDER = Border(
        left=Side(style="thin", color=BORDER_COLOR),
        right=Side(style="thin", color=BORDER_COLOR),
        top=Side(style="thin", color=BORDER_COLOR),
        bottom=Side(style="thin", color=BORDER_COLOR)
    )

    def apply_header_styles(ws, headers):
        ws.freeze_panes = "A2"
        for col_idx, h in enumerate(headers, start=1):
            c = ws.cell(row=1, column=col_idx, value=h)
            c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=NAVY)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = THIN_BORDER
        ws.row_dimensions[1].height = 28

    def auto_fit_and_filter(ws, max_cols):
        ws.auto_filter.ref = ws.dimensions
        for col_idx in range(1, max_cols + 1):
            col_letter = get_column_letter(col_idx)
            max_len = 0
            for row in ws.iter_rows(min_col=col_idx, max_col=col_idx):
                val = str(row[0].value or "")
                if len(val) > max_len:
                    max_len = len(val)
            ws.column_dimensions[col_letter].width = max(14, min(max_len + 3, 50))

    # --------------------------------------------------
    # SOLAPA 1: Hallazgos y Propuestas
    # --------------------------------------------------
    ws1 = wb.active
    ws1.title = "Hallazgos y Propuestas"
    headers_1 = [
        "Área / Proceso", "ID Hallazgo", "Hallazgo (Situación Observada)",
        "ID Propuesta", "Propuesta de Mejora (Recomendación)", "Riesgo",
        "Responsable", "Fecha compromiso", "Estado", "Avance", "Acciones", "Observaciones"
    ]
    apply_header_styles(ws1, headers_1)

    row_idx = 2
    for f in findings:
        props = f.get("proposals") or [None]
        for p in props:
            first_action = (p.get("action_plans") or [{}])[0] if p and p.get("action_plans") else {}
            owner = (p.get("action_owner") if p else None) or first_action.get("action_owner") or f.get("action_owner", "Sin asignar")
            target_date = (p.get("target_date") if p else None) or first_action.get("target_date") or ""
            raw_status = (p.get("status") if p else None) or f.get("status", "En proceso")
            eff_status = compute_effective_status(raw_status, target_date)
            pct = first_action.get("progress_pct", 0) if first_action else 0

            vals = [
                f.get("responsible_area") or "Pendiente de definir",
                f.get("code", ""),
                f.get("situation") or f.get("title", ""),
                p.get("code", "Sin propuesta") if p else "Sin propuesta",
                p.get("proposal_text") or p.get("title", "") if p else "",
                f.get("severity", "Medio"),
                owner,
                target_date,
                eff_status,
                f"{pct}%",
                first_action.get("action_text") or first_action.get("title", ""),
                f.get("observations", "")
            ]
            for col_idx, v in enumerate(vals, start=1):
                c = ws1.cell(row=row_idx, column=col_idx, value=v)
                c.font = Font(name="Calibri", size=9, color=TEXT_COLOR)
                c.border = THIN_BORDER
                c.alignment = Alignment(vertical="center", horizontal="center" if col_idx in [2, 4, 6, 8, 9, 10] else "left", wrap_text=True)
            ws1.row_dimensions[row_idx].height = 30
            row_idx += 1
    auto_fit_and_filter(ws1, len(headers_1))

    # --------------------------------------------------
    # SOLAPA 2: Propuestas de Mejora
    # --------------------------------------------------
    ws2 = wb.create_sheet("Propuestas de Mejora")
    headers_2 = [
        "ID Propuesta", "Propuesta de Mejora", "Hallazgo Origen", "Área / Proceso",
        "Estado de Implementación", "Responsable", "Planes de Acción",
        "Fecha Compromiso", "Repositorio / Acción", "Informe"
    ]
    apply_header_styles(ws2, headers_2)

    row_idx = 2
    for p in proposals:
        eff_status = compute_effective_status(p.get("status"), p.get("target_date"))
        is_archived = eff_status.lower() in ["finalizado", "completada", "implementada", "archivada"]
        repo_action = "🗃️ Plan 2026" if is_archived else "Archivar"

        vals = [
            p.get("code", ""),
            p.get("proposal_text") or p.get("title", ""),
            p.get("finding_code", ""),
            p.get("responsible_area", "Operaciones"),
            eff_status,
            p.get("action_owner", "Auditoría"),
            f"{p.get('action_plans_count', 0)} plan(es)",
            p.get("target_date", ""),
            repo_action,
            p.get("report_title", "")
        ]
        for col_idx, v in enumerate(vals, start=1):
            c = ws2.cell(row=row_idx, column=col_idx, value=v)
            c.font = Font(name="Calibri", size=9, color=TEXT_COLOR)
            c.border = THIN_BORDER
            c.alignment = Alignment(vertical="center", horizontal="center" if col_idx in [1, 3, 5, 7, 8, 9] else "left", wrap_text=True)
        ws2.row_dimensions[row_idx].height = 28
        row_idx += 1
    auto_fit_and_filter(ws2, len(headers_2))

    # --------------------------------------------------
    # SOLAPA 3: Planes de Acción
    # --------------------------------------------------
    ws3 = wb.create_sheet("Planes de Acción")
    headers_3 = [
        "ID Plan", "Acción Compromiso", "Propuesta Vinculada", "Hallazgo",
        "Informe", "Responsable", "Fecha Compromiso", "% Avance",
        "Estado", "Evidencia", "Acciones"
    ]
    apply_header_styles(ws3, headers_3)

    row_idx = 2
    for pa in action_plans:
        vals = [
            pa.get("code") or f"PA-{pa.get('id', '')}",
            pa.get("action_text") or pa.get("title", ""),
            pa.get("proposal_code", ""),
            pa.get("finding_code", ""),
            pa.get("report_title", ""),
            pa.get("action_owner", ""),
            pa.get("target_date", ""),
            f"{pa.get('progress_pct', 0)}%",
            pa.get("status", "En proceso"),
            pa.get("notes", ""),
            ""
        ]
        for col_idx, v in enumerate(vals, start=1):
            c = ws3.cell(row=row_idx, column=col_idx, value=v)
            c.font = Font(name="Calibri", size=9, color=TEXT_COLOR)
            c.border = THIN_BORDER
            c.alignment = Alignment(vertical="center", horizontal="center" if col_idx in [1, 3, 4, 7, 8, 9] else "left", wrap_text=True)
        ws3.row_dimensions[row_idx].height = 28
        row_idx += 1
    auto_fit_and_filter(ws3, len(headers_3))

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"Reporte_AuditTrack_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/download-template")
@require_auth
def download_template():
    wb = Workbook()
    ws = wb.active
    ws.title = "Hallazgos y Propuestas"

    NAVY = "0F172A"
    TEXT_COLOR = "1F2937"
    BORDER_COLOR = "E2E8F0"
    THIN_BORDER = Border(
        left=Side(style="thin", color=BORDER_COLOR),
        right=Side(style="thin", color=BORDER_COLOR),
        top=Side(style="thin", color=BORDER_COLOR),
        bottom=Side(style="thin", color=BORDER_COLOR)
    )

    headers = [
        "Área / Proceso", "ID Hallazgo", "Hallazgo (Situación Observada)",
        "ID Propuesta", "Propuesta de Mejora (Recomendación)", "Riesgo",
        "Responsable", "Fecha compromiso", "Estado", "Avance", "Acciones", "Observaciones"
    ]

    ws.freeze_panes = "A2"
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = THIN_BORDER
    ws.row_dimensions[1].height = 28

    sample_row = [
        "Tesorería",
        "H-2026-001",
        "Faltante de caja no justificado en arqueo diario",
        "PM-2026-001",
        "Implementar arqueos sorpresivos y conciliación diaria",
        "Alto",
        "Guadalupe Méndez",
        "31/10/2026",
        "En proceso",
        "50%",
        "Revisión de normativa enviada",
        "Pendiente de informe final"
    ]

    for col_idx, v in enumerate(sample_row, start=1):
        c = ws.cell(row=2, column=col_idx, value=v)
        c.font = Font(name="Calibri", size=9, color=TEXT_COLOR)
        c.border = THIN_BORDER
        c.alignment = Alignment(vertical="center", horizontal="center" if col_idx in [2, 4, 6, 8, 9, 10] else "left", wrap_text=True)
    ws.row_dimensions[2].height = 30

    ws.auto_filter.ref = ws.dimensions

    widths = [20, 14, 38, 14, 42, 12, 22, 16, 14, 12, 28, 28]
    for idx, w in enumerate(widths, start=1):
        col_letter = get_column_letter(idx)
        ws.column_dimensions[col_letter].width = w

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="Plantilla_Importacion_AuditTrack.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def parse_excel_pct(val):
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        if isinstance(val, float) and 0.0 < val <= 1.0:
            return int(round(val * 100))
        return max(0, min(100, int(round(val))))

    raw_str = str(val).strip()
    if not raw_str:
        return 0

    has_percent = "%" in raw_str
    clean_str = raw_str.replace("%", "").strip()

    try:
        f = float(clean_str)
        if has_percent:
            return max(0, min(100, int(round(f))))
        else:
            if 0.0 < f < 1.0:
                return int(round(f * 100))
            return max(0, min(100, int(round(f))))
    except Exception:
        return 0


@app.route("/import-excel", methods=["POST"])
@require_auth
@require_role(ROLE_EDITOR, ROLE_VALIDATOR)
def import_excel():
    if "file" not in request.files:
        return jsonify({"error": "No se envió ningún archivo"}), 400

    file = request.files["file"]
    if not file or not file.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "Formato inválido. Debe ser un archivo Excel (.xlsx)"}), 400

    try:
        content_bytes = file.read()
        wb = load_workbook(filename=BytesIO(content_bytes), data_only=True)
        
        ws1 = wb["Hallazgos y Propuestas"] if "Hallazgos y Propuestas" in wb.sheetnames else wb.active
        ws3 = wb["Planes de Acción"] if "Planes de Acción" in wb.sheetnames else None

        rows1 = list(ws1.iter_rows(values_only=True))
        if len(rows1) < 2:
            return jsonify({"error": "El archivo Excel está vacío o no contiene filas de datos"}), 400

        report_filename = file.filename
        report_title = f"Importación Excel - {os.path.splitext(report_filename)[0]}"

        findings_map = {}
        findings_order = []

        for idx, row in enumerate(rows1[1:], start=1):
            if not row or not any(row):
                continue

            area = str(row[0] or "").strip()
            h_code = str(row[1] or "").strip()
            situation = str(row[2] or "").strip()
            p_code = str(row[3] or "").strip()
            prop_text = str(row[4] or "").strip()
            risk = str(row[5] or "Medio").strip()
            owner = str(row[6] or "").strip()
            target_date = str(row[7] or "").strip()
            status = str(row[8] or "En proceso").strip()
            pct = parse_excel_pct(row[9] if len(row) > 9 else 0)
            actions = str(row[10] or "").strip() if len(row) > 10 else ""
            obs = str(row[11] or "").strip() if len(row) > 11 else ""

            if not h_code and not situation and not prop_text:
                continue

            if not h_code:
                h_code = f"H-IMP-{idx:03d}"
            if not p_code:
                p_code = f"PM-IMP-{idx:03d}"

            clean_status = "En proceso"
            if "suspensión" in status.lower() or "suspension" in status.lower():
                clean_status = "En suspensión"
            elif any(w in status.lower() for w in ["finalizado", "finalizada", "completada", "completado", "cerrado"]):
                clean_status = "Finalizado"

            clean_risk = "Medio"
            if "alto" in risk.lower():
                clean_risk = "Alto"
            elif "bajo" in risk.lower():
                clean_risk = "Bajo"

            if h_code not in findings_map:
                finding_item = {
                    "code": h_code,
                    "title": situation[:100] if situation else f"Hallazgo {h_code}",
                    "situation": situation or "Sin detalle",
                    "risk": clean_risk,
                    "severity": clean_risk,
                    "responsible_area": area or "Operaciones",
                    "action_owner": owner or "Auditoría",
                    "status": clean_status,
                    "observations": obs,
                    "proposals": []
                }
                findings_map[h_code] = finding_item
                findings_order.append(h_code)

            finding_item = findings_map[h_code]

            existing_prop = next((p for p in finding_item["proposals"] if p["code"] == p_code), None)
            if not existing_prop:
                prop_item = {
                    "code": p_code,
                    "title": prop_text[:100] if prop_text else f"Propuesta {p_code}",
                    "proposal_text": prop_text or "Sin detalle de propuesta",
                    "severity": clean_risk,
                    "responsible_area": area or "Operaciones",
                    "action_owner": owner or "Auditoría",
                    "target_date": target_date,
                    "status": clean_status,
                    "action_plans": []
                }
                finding_item["proposals"].append(prop_item)
                existing_prop = prop_item

            if not ws3 and (actions or pct > 0):
                pa_code = f"PA-IMP-{idx:03d}"
                plan_item = {
                    "code": pa_code,
                    "title": actions or prop_text[:100] or "Plan de Acción",
                    "action_text": actions or prop_text or "Plan de Acción",
                    "action_owner": owner or "Auditoría",
                    "target_date": target_date,
                    "status": clean_status,
                    "progress_pct": pct,
                    "notes": obs
                }
                existing_prop["action_plans"].append(plan_item)

        if ws3:
            rows3 = list(ws3.iter_rows(values_only=True))
            if len(rows3) >= 2:
                for idx, r3 in enumerate(rows3[1:], start=1):
                    if not r3 or not any(r3):
                        continue
                    pa_code = str(r3[0] or "").strip()
                    pa_text = str(r3[1] or "").strip()
                    p_code_link = str(r3[2] or "").strip()
                    h_code_link = str(r3[3] or "").strip()
                    pa_owner = str(r3[5] or "").strip()
                    pa_target = str(r3[6] or "").strip()
                    pa_pct = parse_excel_pct(r3[7] if len(r3) > 7 else 0)
                    pa_status = str(r3[8] or "En proceso").strip() if len(r3) > 8 else "En proceso"
                    pa_notes = str(r3[9] or "").strip() if len(r3) > 9 else ""

                    if not pa_text and not pa_code:
                        continue

                    clean_pa_status = "En proceso"
                    if "suspensión" in pa_status.lower() or "suspension" in pa_status.lower():
                        clean_pa_status = "En suspensión"
                    elif any(w in pa_status.lower() for w in ["finalizado", "completada", "completado", "cerrado"]):
                        clean_pa_status = "Finalizado"

                    matched_prop = None
                    for f in findings_map.values():
                        for p in f["proposals"]:
                            if p["code"] == p_code_link or (h_code_link and f["code"] == h_code_link):
                                matched_prop = p
                                break
                        if matched_prop:
                            break

                    if matched_prop:
                        if not any(plan["code"] == pa_code for plan in matched_prop["action_plans"] if pa_code):
                            plan_item = {
                                "code": pa_code or f"PA-IMP3-{idx:03d}",
                                "title": pa_text[:100] if pa_text else f"Plan {pa_code}",
                                "action_text": pa_text or "Plan de Acción",
                                "action_owner": pa_owner or matched_prop["action_owner"],
                                "target_date": pa_target or matched_prop["target_date"],
                                "status": clean_pa_status,
                                "progress_pct": pa_pct,
                                "notes": pa_notes
                            }
                            matched_prop["action_plans"].append(plan_item)

        findings_to_insert = [findings_map[h] for h in findings_order]

        if not findings_to_insert:
            return jsonify({"error": "No se encontraron filas válidas en la planilla Excel"}), 400

        report_data = {
            "title": report_title,
            "filename": report_filename,
            "summary": f"Importación relacional desde planilla Excel ({len(findings_to_insert)} hallazgos)."
        }

        report_id = save_relational_report_structure(report_data, findings_to_insert, report_filename)

        return jsonify({
            "success": True,
            "message": f"Se importaron exitosamente {len(findings_to_insert)} hallazgos agrupados con sus propuestas y planes desde Excel.",
            "report_id": report_id,
            "imported_count": len(findings_to_insert)
        })

    except Exception as exc:
        print(f"Error procesando importación Excel: {exc}")
        return jsonify({"error": f"No se pudo procesar la planilla Excel: {str(exc)}"}), 500


@app.route("/api/proposals/<proposal_id>/validate", methods=["POST"])
@app.route("/proposals/<proposal_id>/validate", methods=["POST"])
@require_auth
@require_role(ROLE_VALIDATOR)
def validate_proposal_route(proposal_id):
    user = get_current_user()
    user_name = user.get("name") if user else "Auditoría Interna"
    success, msg = validate_proposal(proposal_id, user_name=user_name)
    if success:
        return jsonify({"success": True, "message": msg, "id": proposal_id})
    return jsonify({"success": False, "error": msg}), 400


@app.route("/api/action-plans/<plan_id>/validate", methods=["POST"])
@app.route("/action-plans/<plan_id>/validate", methods=["POST"])
@require_auth
@require_role(ROLE_VALIDATOR)
def validate_action_plan_route(plan_id):
    user = get_current_user()
    user_name = user.get("name") if user else "Auditoría Interna"
    success, msg = validate_action_plan(plan_id, user_name=user_name)
    if success:
        return jsonify({"success": True, "message": msg, "id": plan_id})
    return jsonify({"success": False, "error": msg}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
