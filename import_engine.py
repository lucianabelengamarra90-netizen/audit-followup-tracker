"""
Motor de importación idempotente para la jerarquía Informe → Hallazgo → Propuesta → Plan.

Principios:
- Identidad por contenido: cada importación se registra con la huella (SHA-256) de su
  contenido normalizado y, si está disponible, del archivo original. Un archivo idéntico
  (misma huella o mismos bytes) nunca modifica datos.
- Un archivo distinto que coincide con datos existentes exige una decisión explícita:
    * "merge":     actualiza el informe existente y conserva las ediciones manuales.
    * "overwrite": actualiza el informe existente y sobrescribe también las ediciones manuales.
    * "new":       crea un informe nuevo (los códigos que colisionan se renumeran).
- Ediciones manuales: cada entidad guarda `import_baseline`, los valores escritos por la última
  importación. Si el valor actual difiere de esa línea base, el campo fue modificado en la
  aplicación (riesgo, observaciones, estado, etc.) y "merge" no lo pisa.
- Relaciones exactas: nunca se reasigna una propuesta o un plan a otro padre. Si un código
  pertenece a otro hallazgo/propuesta, la relación se rechaza y se informa.
"""
import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime

import database as db
from domain.statuses import normalize_status
from domain.dates import parse_date_to_iso

IMPORT_MODES = ("auto", "new", "merge", "overwrite")

# Códigos con formato de secuencia global (AUD-2026-001, H-2026-001, PM-..., PA-...).
STRONG_CODE_RE = re.compile(r"^(AUD|H|PM|PA)-\d{4}-\d{3,}$")
# Referencias temporales generadas por el parser cuando la planilla no trae código.
GENERATED_CODE_RE = re.compile(r"^(H|PM|PA)-IMP(-S\d+)?-\d+$", re.IGNORECASE)

FINDING_FIELDS = ("title", "situation", "severity", "responsible_area", "action_owner", "status", "observations")
PROPOSAL_FIELDS = ("title", "proposal_text", "responsible_area", "action_owner", "target_date", "status")
PLAN_FIELDS = ("title", "action_text", "action_owner", "target_date", "status", "progress_pct", "notes")

FIELD_LABELS = {
    "title": "título", "situation": "situación", "severity": "riesgo",
    "responsible_area": "área", "action_owner": "responsable", "status": "estado",
    "observations": "observaciones", "proposal_text": "texto", "target_date": "fecha compromiso",
    "action_text": "acción", "progress_pct": "avance", "notes": "notas/evidencia",
}


class ImportDecisionRequired(Exception):
    """El contenido coincide con un informe existente y no se indicó cómo actualizarlo."""

    def __init__(self, analysis):
        super().__init__(
            "El archivo contiene datos que ya existen en AuditTrack. "
            "Elegí explícitamente si querés actualizar el informe existente o crear uno nuevo."
        )
        self.analysis = analysis


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def norm_key(value):
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_code(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def explicit_code(item):
    """Código real informado en el archivo (vacío si es una referencia generada por el parser)."""
    code = norm_code(item.get("code"))
    if not code or item.get("code_generated") or GENERATED_CODE_RE.match(code):
        return ""
    return code


def clean_value(field, value):
    if field == "severity":
        return db.normalize_severity(value)
    if field == "status":
        return normalize_status(value or "En proceso")
    if field == "target_date":
        if value in (None, "", "None"):
            return ""
        return parse_date_to_iso(value) or ""
    if field == "progress_pct":
        try:
            return max(0, min(100, int(float(value or 0))))
        except (TypeError, ValueError):
            return 0
    if value is None:
        return ""
    return str(value).strip()


def proposal_list(f_item):
    props = f_item.get("proposals") or []
    if not props and f_item.get("proposal"):
        props = [{"title": f_item.get("title"), "proposal_text": f_item.get("proposal")}]
    return props


def finding_values(f, report_area):
    title = str(f.get("title") or "Observación de Auditoría").strip()
    return {
        "title": title,
        "situation": str(f.get("situation") or title).strip(),
        "severity": clean_value("severity", f.get("severity") or f.get("risk")),
        "responsible_area": str(f.get("responsible_area") or report_area or "Operaciones").strip(),
        "action_owner": str(f.get("action_owner") or "Pendiente de definir").strip(),
        "status": clean_value("status", f.get("status")),
        "observations": str(f.get("observations") or "").strip(),
    }


def proposal_values(p, fv):
    title = str(p.get("title") or f"Propuesta para {fv['title']}").strip()
    return {
        "title": title,
        "proposal_text": str(p.get("proposal_text") or p.get("proposal") or title).strip(),
        "responsible_area": str(p.get("responsible_area") or fv["responsible_area"]).strip(),
        "action_owner": str(p.get("action_owner") or fv["action_owner"]).strip(),
        "target_date": clean_value("target_date", p.get("target_date")),
        "status": clean_value("status", p.get("status") or fv["status"]),
    }


def plan_values(pa, pv, p_label):
    title = str(pa.get("title") or f"Acción para {p_label}").strip()
    pct = clean_value("progress_pct", pa.get("progress_pct"))
    status = clean_value("status", pa.get("status") or pv["status"])
    if pct == 100 and status not in ("En suspensión", "Finalizado"):
        status = "Pendiente de validación"
    return {
        "title": title,
        "action_text": str(pa.get("action_text") or title).strip(),
        "action_owner": str(pa.get("action_owner") or pv["action_owner"]).strip(),
        "target_date": clean_value("target_date", pa.get("target_date") or pv["target_date"]),
        "status": status,
        "progress_pct": pct,
        "notes": str(pa.get("notes") or "").strip(),
    }


def compute_fingerprint(findings, report_area="Operaciones"):
    """Huella del contenido normalizado (independiente del nombre de archivo y del título)."""
    canon = []
    for f in findings or []:
        fv = finding_values(f, report_area)
        props = []
        for p in proposal_list(f):
            pv = proposal_values(p, fv)
            p_label = explicit_code(p) or "propuesta"
            plans = [dict(code=explicit_code(pa), **plan_values(pa, pv, p_label)) for pa in (p.get("action_plans") or [])]
            props.append(dict(code=explicit_code(p), plans=plans, **pv))
        canon.append(dict(code=explicit_code(f), proposals=props, **fv))
    raw = json.dumps(canon, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Análisis previo (sin escrituras)
# ---------------------------------------------------------------------------

def _row_to_dict(row):
    return dict(row) if row is not None else None


def _find_identical(cur, fingerprint, file_hash):
    params = [fingerprint]
    sql = """
        SELECT b.report_id, b.created_at, b.source_filename, r.code, r.title
        FROM import_batches b JOIN reports r ON r.id = b.report_id
        WHERE b.content_fingerprint = ?
    """
    if file_hash:
        sql += " OR b.file_hash = ?"
        params.append(file_hash)
    sql += " ORDER BY b.created_at DESC"
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["created_at"] = str(d.get("created_at") or "")
    return d


def analyze_import(report_data, findings, cursor=None, file_hash=None):
    """Clasifica el contenido como 'identical', 'overlap' (coincide con informes existentes) o 'new'."""
    own_conn = None
    if cursor is None:
        db.init_db()
        own_conn = db.get_db()
        cursor = own_conn.cursor()
    try:
        area = (report_data or {}).get("area") or "Operaciones"
        fingerprint = compute_fingerprint(findings, area)
        identical = _find_identical(cursor, fingerprint, file_hash)

        cursor.execute("SELECT id, report_id, code, situation, title FROM findings")
        f_rows = [_row_to_dict(r) for r in cursor.fetchall()]
        f_by_code = {norm_code(r["code"]): r for r in f_rows if r.get("code")}
        f_by_key = {}
        for r in f_rows:
            f_by_key.setdefault(norm_key(r.get("situation") or r.get("title")), r)

        cursor.execute("SELECT p.code, f.report_id FROM proposals p JOIN findings f ON f.id = p.finding_id")
        p_by_code = {norm_code(r[0]): r[1] for r in cursor.fetchall() if r[0]}
        cursor.execute("""
            SELECT pa.code, f.report_id FROM action_plans pa
            JOIN proposals p ON p.id = pa.proposal_id
            JOIN findings f ON f.id = p.finding_id
        """)
        pa_by_code = {norm_code(r[0]): r[1] for r in cursor.fetchall() if r[0]}

        shared = defaultdict(lambda: {"findings": 0, "proposals": 0, "plans": 0})
        for f in findings or []:
            code = explicit_code(f)
            match = f_by_code.get(code) if code else None
            if not match:
                fv = finding_values(f, area)
                match = f_by_key.get(norm_key(fv["situation"]))
            if match:
                shared[match["report_id"]]["findings"] += 1
            for p in proposal_list(f):
                pc = explicit_code(p)
                if pc and pc in p_by_code:
                    shared[p_by_code[pc]]["proposals"] += 1
                for pa in p.get("action_plans") or []:
                    pac = explicit_code(pa)
                    if pac and pac in pa_by_code:
                        shared[pa_by_code[pac]]["plans"] += 1

        candidates = []
        if shared:
            ids = list(shared.keys())
            placeholders = ",".join("?" for _ in ids)
            cursor.execute(f"SELECT id, code, title, source_filename, created_at FROM reports WHERE id IN ({placeholders})", ids)
            for r in cursor.fetchall():
                d = _row_to_dict(r)
                s = shared[d["id"]]
                candidates.append({
                    "report_id": d["id"], "code": d.get("code"), "title": d.get("title"),
                    "source_filename": d.get("source_filename"), "created_at": str(d.get("created_at") or ""),
                    "shared_findings": s["findings"], "shared_proposals": s["proposals"], "shared_plans": s["plans"],
                })
            candidates.sort(key=lambda c: (c["shared_findings"] + c["shared_proposals"] + c["shared_plans"]), reverse=True)

        status = "identical" if identical else ("overlap" if candidates else "new")
        return {
            "status": status,
            "fingerprint": fingerprint,
            "file_hash": file_hash,
            "identical_report": identical,
            "candidates": candidates,
        }
    finally:
        if own_conn is not None:
            own_conn.close()


# ---------------------------------------------------------------------------
# Importación
# ---------------------------------------------------------------------------

def _empty_counts():
    return {
        "findings": {"created": 0, "updated": 0, "unchanged": 0, "rejected": 0},
        "proposals": {"created": 0, "updated": 0, "unchanged": 0, "rejected": 0},
        "plans": {"created": 0, "updated": 0, "unchanged": 0, "rejected": 0},
    }


class _ImportContext:
    def __init__(self, cur, mode, user_name):
        self.cur = cur
        self.mode = mode
        self.user = user_name or "Auditoría Interna"
        self.counts = _empty_counts()
        self.preserved = []
        self.overwritten = []
        self.conflicts = []
        self.renamed = []
        self.used_ids = set()
        self.today = datetime.now().strftime("%Y-%m-%d")

    @property
    def updating(self):
        return self.mode in ("merge", "overwrite")


def _code_taken(cur, table, code):
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE UPPER(code) = ?", (code,))
    return (cur.fetchone()[0] or 0) > 0


def _assign_code(ctx, table, entity_type, code, label):
    if code and not _code_taken(ctx.cur, table, code):
        return code
    new_code = db.generate_next_code(entity_type, cursor=ctx.cur)
    if code:
        ctx.renamed.append(f"{label} {code} → {new_code} (el código ya existía)")
    return new_code


def _apply_fields(ctx, table, entity_type, row, values, label):
    """Aplica valores importados a una entidad existente respetando las ediciones manuales."""
    try:
        baseline = json.loads(row.get("import_baseline") or "{}")
        if not isinstance(baseline, dict):
            baseline = {}
    except (TypeError, ValueError):
        baseline = {}
    original_baseline = json.dumps(baseline, sort_keys=True, ensure_ascii=False)

    sets = {}
    for field, new_val in values.items():
        if new_val == "":
            continue  # El archivo no aporta dato: nunca se borra información existente.
        current = clean_value(field, row.get(field))
        if new_val == current:
            baseline[field] = new_val
            continue
        unchanged_since_import = field in baseline and clean_value(field, baseline[field]) == current
        if ctx.mode == "overwrite":
            if not unchanged_since_import and current != "":
                ctx.overwritten.append(f"{label} · {FIELD_LABELS.get(field, field)}")
            sets[field] = new_val
            baseline[field] = new_val
        elif unchanged_since_import or current == "":
            sets[field] = new_val
            baseline[field] = new_val
        else:
            ctx.preserved.append(f"{label} · {FIELD_LABELS.get(field, field)}")

    new_baseline = json.dumps(baseline, sort_keys=True, ensure_ascii=False)
    if sets:
        cols = dict(sets)
        if table == "findings" and "severity" in cols:
            cols["risk"] = cols["severity"]
        if table == "action_plans" and "status" in cols:
            cols["closed_date"] = ctx.today if cols["status"] == "Finalizado" else None
        assignments = ", ".join(f"{k} = ?" for k in cols)
        ctx.cur.execute(
            f"UPDATE {table} SET {assignments}, import_baseline = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
            list(cols.values()) + [new_baseline, row["id"]],
        )
        changes = ", ".join(FIELD_LABELS.get(k, k) for k in sets)
        db.add_history_log(entity_type, row["id"], ctx.user, f"Reimportación ({ctx.mode}): actualizó {changes}", cursor=ctx.cur)
        return True
    if new_baseline != original_baseline:
        ctx.cur.execute(f"UPDATE {table} SET import_baseline = ? WHERE id = ?", (new_baseline, row["id"]))
    return False


def _import_plan(ctx, pa, proposal_id, p_code, pv, existing_plans):
    values = plan_values(pa, pv, p_code)
    code = explicit_code(pa)
    row = None
    if code:
        row = next((r for r in existing_plans if norm_code(r.get("code")) == code and r["id"] not in ctx.used_ids), None)
        if not row and ctx.updating:
            ctx.cur.execute("""
                SELECT pa.id, p.code AS proposal_code FROM action_plans pa
                JOIN proposals p ON p.id = pa.proposal_id WHERE UPPER(pa.code) = ?
            """, (code,))
            other = ctx.cur.fetchone()
            if other and STRONG_CODE_RE.match(code):
                ctx.conflicts.append(
                    f"Plan {code}: pertenece a la propuesta {other['proposal_code']}, no a {p_code}. Relación rechazada."
                )
                ctx.counts["plans"]["rejected"] += 1
                return
    if not row and ctx.updating:
        key = norm_key(values["action_text"])
        row = next((r for r in existing_plans if r["id"] not in ctx.used_ids and norm_key(r.get("action_text")) == key), None)

    if row:
        ctx.used_ids.add(row["id"])
        changed = _apply_fields(ctx, "action_plans", "action_plan", row, values, row.get("code") or "Plan")
        ctx.counts["plans"]["updated" if changed else "unchanged"] += 1
        return

    pa_code = _assign_code(ctx, "action_plans", "action_plan", code, "Plan")
    plan_id = str(uuid.uuid4())
    closed_dt = ctx.today if values["status"] == "Finalizado" else None
    ctx.cur.execute("""
        INSERT INTO action_plans (id, proposal_id, code, title, action_text, action_owner, target_date, status,
                                  progress_pct, notes, evidence_file, closed_date, import_baseline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (plan_id, proposal_id, pa_code, values["title"], values["action_text"], values["action_owner"],
          values["target_date"], values["status"], values["progress_pct"], values["notes"],
          str(pa.get("evidence_file") or "").strip(), closed_dt,
          json.dumps(values, sort_keys=True, ensure_ascii=False)))
    db.add_history_log("action_plan", plan_id, values["action_owner"], f"Creación de plan de acción {pa_code} vinculado a propuesta {p_code}", cursor=ctx.cur)
    ctx.counts["plans"]["created"] += 1


def _import_proposal(ctx, p, finding_id, f_code, fv, existing_props):
    values = proposal_values(p, fv)
    code = explicit_code(p)
    plans = p.get("action_plans") or []
    row = None
    if code:
        row = next((r for r in existing_props if norm_code(r.get("code")) == code and r["id"] not in ctx.used_ids), None)
        if not row and ctx.updating:
            ctx.cur.execute("""
                SELECT p.id, f.code AS finding_code FROM proposals p
                JOIN findings f ON f.id = p.finding_id WHERE UPPER(p.code) = ?
            """, (code,))
            other = ctx.cur.fetchone()
            if other and STRONG_CODE_RE.match(code):
                ctx.conflicts.append(
                    f"Propuesta {code}: pertenece al hallazgo {other['finding_code']}, no a {f_code}. "
                    f"Relación rechazada (no se importaron la propuesta ni sus {len(plans)} plan(es))."
                )
                ctx.counts["proposals"]["rejected"] += 1
                ctx.counts["plans"]["rejected"] += len(plans)
                return
    if not row and ctx.updating:
        key = norm_key(values["proposal_text"])
        row = next((r for r in existing_props if r["id"] not in ctx.used_ids and norm_key(r.get("proposal_text")) == key), None)

    if row:
        ctx.used_ids.add(row["id"])
        proposal_id = row["id"]
        p_code = row.get("code")
        changed = _apply_fields(ctx, "proposals", "proposal", row, values, p_code or "Propuesta")
        ctx.counts["proposals"]["updated" if changed else "unchanged"] += 1
        ctx.cur.execute("SELECT * FROM action_plans WHERE proposal_id = ?", (proposal_id,))
        existing_plans = [_row_to_dict(r) for r in ctx.cur.fetchall()]
    else:
        p_code = _assign_code(ctx, "proposals", "proposal", code, "Propuesta")
        proposal_id = str(uuid.uuid4())
        ctx.cur.execute("""
            INSERT INTO proposals (id, finding_id, code, title, proposal_text, severity, responsible_area,
                                   action_owner, target_date, status, import_baseline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (proposal_id, finding_id, p_code, values["title"], values["proposal_text"], fv["severity"],
              values["responsible_area"], values["action_owner"], values["target_date"], values["status"],
              json.dumps(values, sort_keys=True, ensure_ascii=False)))
        db.add_history_log("proposal", proposal_id, ctx.user, f"Creación de propuesta {p_code} vinculada a {f_code}", cursor=ctx.cur)
        ctx.counts["proposals"]["created"] += 1
        existing_plans = []

    for pa in plans:
        _import_plan(ctx, pa, proposal_id, p_code, values, existing_plans)


def _import_finding(ctx, f, report_id, report_area, target_findings):
    values = finding_values(f, report_area)
    code = explicit_code(f)
    row = None
    if ctx.updating:
        if code:
            row = next((r for r in target_findings if norm_code(r.get("code")) == code and r["id"] not in ctx.used_ids), None)
            if not row and STRONG_CODE_RE.match(code):
                ctx.cur.execute("""
                    SELECT f.id, f.code, r.code AS report_code, r.id AS report_id FROM findings f
                    JOIN reports r ON r.id = f.report_id WHERE UPPER(f.code) = ?
                """, (code,))
                other = ctx.cur.fetchone()
                if other:
                    other_dict = _row_to_dict(other)
                    if other_dict["report_id"] != report_id:
                        ctx.conflicts.append(
                            f"Hallazgo {code}: pertenece al informe {other_dict['report_code']}, no al informe seleccionado. Relación rechazada."
                        )
                        ctx.counts["findings"]["rejected"] += 1
                        return
                    elif other_dict["id"] in ctx.used_ids:
                        row = None
        if not row:
            key = norm_key(values["situation"])
            row = next((r for r in target_findings if r["id"] not in ctx.used_ids
                        and norm_key(r.get("situation") or r.get("title")) == key), None)

    if row:
        ctx.used_ids.add(row["id"])
        finding_id = row["id"]
        f_code = row.get("code")
        changed = _apply_fields(ctx, "findings", "finding", row, values, f_code or "Hallazgo")
        ctx.counts["findings"]["updated" if changed else "unchanged"] += 1
        ctx.cur.execute("SELECT * FROM proposals WHERE finding_id = ?", (finding_id,))
        existing_props = [_row_to_dict(r) for r in ctx.cur.fetchall()]
    else:
        f_code = _assign_code(ctx, "findings", "finding", code, "Hallazgo")
        finding_id = str(uuid.uuid4())
        ctx.cur.execute("""
            INSERT INTO findings (id, report_id, code, title, situation, risk, severity, responsible_area,
                                  action_owner, status, observations, import_baseline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (finding_id, report_id, f_code, values["title"], values["situation"], values["severity"],
              values["severity"], values["responsible_area"], values["action_owner"], values["status"],
              values["observations"], json.dumps(values, sort_keys=True, ensure_ascii=False)))
        db.add_history_log("finding", finding_id, ctx.user, f"Creación de hallazgo {f_code}: {values['title']}", cursor=ctx.cur)
        ctx.counts["findings"]["created"] += 1
        existing_props = []

    for p in proposal_list(f):
        _import_proposal(ctx, p, finding_id, f_code, values, existing_props)


def import_report_structure(report_data, findings, source_filename="", mode="auto",
                            target_report_id=None, file_hash=None, user_name=None):
    """
    Importa (o reimporta) un informe de forma atómica e idempotente.
    Devuelve un dict con status ('created' | 'updated' | 'unchanged'), report_id, conteos,
    campos manuales conservados/sobrescritos, conflictos rechazados y códigos renumerados.
    Lanza ImportDecisionRequired si mode='auto' y el contenido coincide con un informe existente.
    """
    mode = (mode or "auto").strip().lower()
    if mode not in IMPORT_MODES:
        raise ValueError(f"Modo de importación inválido: '{mode}'. Usá: new, merge u overwrite.")
    report_data = report_data or {}
    findings = findings or []

    db.init_db()
    conn = db.get_db()
    cur = conn.cursor()
    try:
        analysis = analyze_import(report_data, findings, cursor=cur, file_hash=file_hash)

        if analysis["status"] == "identical":
            ident = analysis["identical_report"]
            return {
                "status": "unchanged", "mode": mode, "report_id": ident["report_id"], "report_code": ident.get("code"),
                "message": (f"Archivo idéntico a una importación previa ({ident.get('code')} · {ident.get('title')}). "
                            "No se modificó ningún dato."),
                "counts": _empty_counts(), "preserved_manual": [], "overwritten_manual": [],
                "conflicts": [], "renamed_codes": [], "analysis": analysis,
            }

        if mode == "auto":
            if analysis["candidates"]:
                raise ImportDecisionRequired(analysis)
            mode = "new"

        ctx = _ImportContext(cur, mode, user_name or report_data.get("auditor"))
        title = str(report_data.get("title") or "Informe de Auditoría").strip()
        process = str(report_data.get("process") or "Proceso General").strip()
        area = str(report_data.get("area") or "Operaciones").strip()
        period = str(report_data.get("period") or "2026").strip()
        auditor = str(report_data.get("auditor") or "Auditoría Interna").strip()
        summary = str(report_data.get("summary") or "").strip()

        target = target_report_id or (analysis["candidates"][0]["report_id"] if analysis["candidates"] else None)
        if not target:
            mode = "new"

        if mode == "new":
            report_id = str(uuid.uuid4())
            rep_code = db.generate_next_code("report", cursor=cur)
            cur.execute("""
                INSERT INTO reports (id, code, title, process, area, period, auditor, summary, source_filename)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (report_id, rep_code, title, process, area, period, auditor, summary, source_filename))
            db.add_history_log("report", report_id, auditor, f"Carga inicial de informe {title} ({source_filename})", cursor=cur)
            target_findings = []
        else:
            cur.execute("SELECT id, code, area FROM reports WHERE id = ? OR code = ?", (target, target))
            rep_row = cur.fetchone()
            if not rep_row:
                raise ValueError(f"El informe seleccionado para actualizar ({target}) no existe.")
            report_id, rep_code = rep_row[0], rep_row[1]
            area = rep_row[2] or area
            if mode == "overwrite":
                cur.execute("UPDATE reports SET process = ?, period = ?, auditor = ?, summary = ? WHERE id = ?",
                            (process, period, auditor, summary, report_id))
            cur.execute("SELECT * FROM findings WHERE report_id = ?", (report_id,))
            target_findings = [_row_to_dict(r) for r in cur.fetchall()]

        for f in findings:
            _import_finding(ctx, f, report_id, area, target_findings)

        counts = ctx.counts
        any_change = any(counts[k]["created"] or counts[k]["updated"] for k in counts)
        status = "created" if mode == "new" else ("updated" if any_change else "unchanged")

        cur.execute("""
            INSERT INTO import_batches (id, report_id, content_fingerprint, file_hash, source_filename, mode, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), report_id, analysis["fingerprint"], file_hash, source_filename, mode,
              json.dumps({"counts": counts, "conflicts": len(ctx.conflicts), "preserved": len(ctx.preserved)}, ensure_ascii=False)))
        if mode != "new":
            db.add_history_log("report", report_id, auditor,
                               f"Reimportación ({mode}) desde {source_filename}: "
                               f"{counts['findings']['created']} hallazgos nuevos, {counts['findings']['updated']} actualizados, "
                               f"{len(ctx.preserved)} ediciones manuales conservadas", cursor=cur)
        conn.commit()

        return {
            "status": status, "mode": mode, "report_id": report_id, "report_code": rep_code,
            "counts": counts,
            "preserved_manual": ctx.preserved,
            "overwritten_manual": ctx.overwritten,
            "conflicts": ctx.conflicts,
            "renamed_codes": ctx.renamed,
            "analysis": analysis,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
