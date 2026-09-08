from flask import Flask, render_template, request, jsonify, send_file
import os
import re
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from database import (
    init_db,
    save_relational_report_structure,
    get_all_reports,
    get_report_detail,
    get_all_findings,
    get_finding_detail,
    get_all_proposals,
    get_all_action_plans,
    create_action_plan,
    update_action_plan,
    delete_finding,
    delete_report,
    get_dashboard_stats,
    get_kpi_indicators,
    get_history_logs,
    add_history_log,
    get_active_alerts
)
from report_parser import parse_audit_report, clean_text

init_db()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {"xlsx", "csv", "docx", "pdf", "txt"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "app": "AuditTrack Relacional"})


@app.route("/upload-report", methods=["POST"])
def upload_report():
    if "file" not in request.files:
        return jsonify({"error": "No se seleccionó ningún archivo de informe."}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "El archivo enviado no es válido."}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Formato no soportado. Usá PDF, Word (.docx) o Excel (.xlsx)."}), 400

    safe_filename = clean_text(file.filename).replace(" ", "_")
    saved_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_filename)
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
    except Exception as exc:
        print(f"Error procesando informe: {exc}")
        return jsonify({"error": f"No se pudo procesar el informe: {str(exc)}"}), 500


@app.route("/reports", methods=["GET"])
def list_reports():
    reports = get_all_reports()
    return jsonify({"reports": reports, "count": len(reports)})


@app.route("/reports/<report_id>", methods=["GET", "DELETE"])
def report_detail_route(report_id):
    if request.method == "DELETE":
        deleted = delete_report(report_id)
        if deleted:
            return jsonify({"message": "Informe eliminado correctamente."})
        return jsonify({"error": "Informe no encontrado."}), 404

    report = get_report_detail(report_id)
    if not report:
        return jsonify({"error": "Informe no encontrado."}), 404
    return jsonify(report)


@app.route("/findings", methods=["GET"])
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
def finding_detail_route(finding_id):
    if request.method == "DELETE":
        deleted = delete_finding(finding_id)
        if deleted:
            return jsonify({"message": "Hallazgo eliminado."})
        return jsonify({"error": "Hallazgo no encontrado."}), 404

    finding = get_finding_detail(finding_id)
    if not finding:
        return jsonify({"error": "Hallazgo no encontrado."}), 404
    return jsonify(finding)


@app.route("/proposals", methods=["GET"])
def list_proposals():
    filters = {
        "status": request.args.get("status"),
        "search": request.args.get("search")
    }
    proposals = get_all_proposals(filters)
    return jsonify({"proposals": proposals, "count": len(proposals)})


@app.route("/action-plans", methods=["GET", "POST"])
def action_plans_route():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        proposal_id = data.get("proposal_id")
        action_text = data.get("action_text") or data.get("title")
        action_owner = data.get("action_owner") or "Auditoría"
        target_date = data.get("target_date") or "2026-10-31"
        status = data.get("status") or "En proceso"
        progress_pct = int(data.get("progress_pct") or 0)
        notes = data.get("notes") or ""

        if not proposal_id or not action_text:
            return jsonify({"error": "Debe seleccionar una Propuesta de Mejora y definir la Acción."}), 400

        plan_id, pa_code = create_action_plan(proposal_id, action_text, action_owner, target_date, status, progress_pct, notes)
        return jsonify({"message": f"Plan de Acción {pa_code} creado exitosamente.", "id": plan_id, "code": pa_code})

    filters = {
        "status": request.args.get("status"),
        "search": request.args.get("search")
    }
    plans = get_all_action_plans(filters)
    return jsonify({"action_plans": plans, "count": len(plans)})


@app.route("/action-plans/<plan_id>", methods=["POST"])
def update_action_plan_route(plan_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    progress_pct = data.get("progress_pct")
    notes = data.get("notes")
    target_date = data.get("target_date")
    evidence_file = data.get("evidence_file")
    action_owner = data.get("action_owner")
    user_name = data.get("user_name") or "Auditoría Interna"

    updated = update_action_plan(plan_id, status, progress_pct, notes, target_date, evidence_file, action_owner, user_name)
    if updated:
        return jsonify({"message": "Plan de Acción actualizado."})
    return jsonify({"error": "Plan de Acción no encontrado."}), 404


@app.route("/dashboard-stats")
def dashboard_stats_route():
    stats = get_dashboard_stats()
    return jsonify(stats)


@app.route("/kpi-indicators")
def kpi_indicators_route():
    kpis = get_kpi_indicators()
    return jsonify({"indicators": kpis})


@app.route("/api/notifications")
def api_notifications():
    alerts = get_active_alerts()
    return jsonify(alerts)


@app.route("/export-excel", methods=["POST"])
def export_excel():
    findings = get_all_findings()
    stats = get_dashboard_stats()

    wb = Workbook()
    ws_kpi = wb.active
    ws_kpi.title = "Tablero de Control"

    NAVY = "17365D"
    BLUE = "1F4E78"
    TEXT_COLOR = "1F2937"
    BORDER_COLOR = "D0D7DE"
    THIN_BORDER = Border(
        left=Side(style="thin", color=BORDER_COLOR),
        right=Side(style="thin", color=BORDER_COLOR),
        top=Side(style="thin", color=BORDER_COLOR),
        bottom=Side(style="thin", color=BORDER_COLOR)
    )

    ws_kpi.merge_cells("A1:G1")
    title_cell = ws_kpi["A1"]
    title_cell.value = "REPORTE CONSOLIDADO AUDITTRACK - TRAZABILIDAD INTEGRAL"
    title_cell.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill("solid", fgColor=NAVY)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws_kpi.row_dimensions[1].height = 40

    kpi_items = [
        ("Hallazgos Abiertos", stats.get("open_findings", 0)),
        ("Riesgo Alto", stats.get("high_risk_findings", 0)),
        ("Total Propuestas", stats.get("total_proposals", 0)),
        ("Planes Vencidos", stats.get("overdue_plans", 0)),
        ("% Implementación", f"{stats.get('impl_rate', 0)}%"),
    ]

    for col_idx, (label, val) in enumerate(kpi_items, start=1):
        cell_lbl = ws_kpi.cell(row=3, column=col_idx, value=label)
        cell_lbl.font = Font(name="Calibri", size=9, bold=True, color="FFFFFF")
        cell_lbl.fill = PatternFill("solid", fgColor=BLUE)
        cell_lbl.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        cell_val = ws_kpi.cell(row=4, column=col_idx, value=val)
        cell_val.font = Font(name="Calibri", size=14, bold=True, color=TEXT_COLOR)
        cell_val.alignment = Alignment(horizontal="center", vertical="center")
        cell_val.border = THIN_BORDER

    ws_detail = wb.create_sheet("Trazabilidad Completa")

    headers = [
        "Área", "ID Hallazgo", "Informe", "Archivo", "Hallazgo (Situación)",
        "Riesgo", "Propuesta de Mejora (Recomendación)", "Plan de Acción",
        "Responsable", "Fecha Compromiso", "Estado"
    ]

    for col_idx, h in enumerate(headers, start=1):
        c = ws_detail.cell(row=1, column=col_idx, value=h)
        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = THIN_BORDER

    row_idx = 2
    for f in findings:
        props = f.get("proposals") or [{}]
        for p in props:
            plans = p.get("action_plans") or [{}]
            for pa in plans:
                vals = [
                    f.get("responsible_area", ""),
                    f.get("code", ""),
                    f.get("report_title", ""),
                    f.get("source_filename", ""),
                    f.get("title", ""),
                    f.get("severity", ""),
                    p.get("proposal_text", p.get("title", "")),
                    pa.get("action_text", pa.get("title", "")),
                    pa.get("action_owner", f.get("action_owner", "")),
                    pa.get("target_date", p.get("target_date", "")),
                    pa.get("status", f.get("status", ""))
                ]
                for col_idx, v in enumerate(vals, start=1):
                    c = ws_detail.cell(row=row_idx, column=col_idx, value=v)
                    c.font = Font(name="Calibri", size=9, color=TEXT_COLOR)
                    c.border = THIN_BORDER
                    c.alignment = Alignment(vertical="center", wrap_text=True)

                ws_detail.row_dimensions[row_idx].height = 32
                row_idx += 1

    widths = [20, 14, 28, 22, 35, 12, 40, 40, 20, 16, 16]
    for idx, w in enumerate(widths, start=1):
        col_letter = get_column_letter(idx)
        ws_detail.column_dimensions[col_letter].width = w

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
