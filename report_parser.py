import os
import re
import json
import uuid
import unicodedata
from openpyxl import load_workbook
from docx import Document
from pypdf import PdfReader
from openai import OpenAI


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text.lower())
    return text.strip()


def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    return OpenAI(api_key=api_key) if api_key else None


def extract_explicit_area(raw_text):
    if not raw_text:
        return "Operaciones"

    patterns = [
        r"(?:área auditada|area auditada)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:proceso auditado|proceso)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:sector)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:gerencia)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:departamento)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:unidad auditada)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:alcance)\s*[:\-]\s*([^\n\r\|]+)"
    ]

    for p in patterns:
        match = re.search(p, raw_text, re.IGNORECASE)
        if match:
            res = clean_text(match.group(1))
            if len(res) > 3 and len(res) < 50:
                return res

    return "Operaciones"


def extract_explicit_auditor(raw_text):
    if not raw_text:
        return "Auditoría Interna"

    patterns = [
        r"(?:auditor responsable|auditor líder|auditor lider|auditor encargado|auditor|elaborado por|realizado por)\s*[:\-]\s*([^\n\r\|]+)"
    ]

    for p in patterns:
        match = re.search(p, raw_text, re.IGNORECASE)
        if match:
            res = clean_text(match.group(1))
            if len(res) > 2 and len(res) < 50:
                return res

    return "Auditoría Interna"


def clean_administrative_phrases(text):
    if not text:
        return ""
    patterns = [
        r"se\s+(?:vio|conversó|habló|consultó|reunió|acordó)\s+con\s+(?:el\s+área|el\s+sector|la\s+gerencia|el\s+responsable)[^.!?]*[.!?]?",
        r"según\s+reunión\s+mantenida[^.!?]*[.!?]?",
        r"de\s+acuerdo\s+con\s+lo\s+informado\s+por[^.!?]*[.!?]?"
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)
    return clean_text(cleaned)


def extract_raw_text_from_file(file_path):
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    text_content = ""

    if ext == "docx":
        try:
            doc = Document(file_path)
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    row_cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if row_cells:
                        paragraphs.append(" | ".join(row_cells))
            text_content = "\n".join(paragraphs)
        except Exception as exc:
            print(f"Error leyendo docx: {exc}")

    elif ext == "pdf":
        try:
            reader = PdfReader(file_path)
            pages_text = []
            for idx, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                if txt.strip():
                    pages_text.append(f"--- PÁGINA {idx + 1} ---\n{txt}")
            text_content = "\n".join(pages_text)
        except Exception as exc:
            print(f"Error leyendo pdf: {exc}")

    elif ext in ("xlsx", "csv"):
        try:
            wb = load_workbook(file_path, data_only=True)
            lines = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                lines.append(f"=== SOLAPA: {sheet_name} ===")
                for row in ws.iter_rows(values_only=True):
                    row_vals = [clean_text(v) for v in row if clean_text(v)]
                    if row_vals:
                        lines.append(" | ".join(row_vals))
            text_content = "\n".join(lines)
        except Exception as exc:
            print(f"Error leyendo excel: {exc}")

    elif ext == "txt":
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_content = f.read()
        except Exception as exc:
            print(f"Error leyendo txt: {exc}")

    return text_content[:60000]


def parse_with_openai_gpt(raw_text, filename):
    client = get_openai_client()
    if not client or not raw_text.strip():
        return None

    prompt = (
        "Sos un Auditor Líder Senior experto en análisis de informes de auditoría interna.\n"
        "Analizá el siguiente texto extraído de un archivo de informe de auditoría "
        "y extraé TODOS los hallazgos y sus correspondientes propuestas de mejora sin omitir ninguno.\n\n"
        "REGLAS CRÍTICAS:\n"
        "1. Extraé CADA hallazgo/observación/desviación encontrada en el documento.\n"
        "2. Para CADA hallazgo generá:\n"
        "   - 'title': Título corto y descriptivo del hallazgo (máx 10 palabras).\n"
        "   - 'situation': Redacción profesional y ejecutiva del problema/situación observada (25-50 palabras). NO copies frases informales administrativas ('se habló con el área...', 'según reunión...').\n"
        "   - 'severity': 'Alto', 'Medio' o 'Bajo' según el riesgo.\n"
        "   - 'responsible_area': Nombre del área o proceso auditado.\n"
        "   - 'proposal': Propuesta de mejora accionable que empiece con un verbo en infinitivo (Implementar, Establecer, Diseñar, Fortalecer, etc., 25-50 palabras).\n"
        "3. Devolvé ÚNICAMENTE un objeto JSON válido con la siguiente estructura exactas sin bloques de código ni markdown adicionales:\n"
        "{\n"
        "  \"report_title\": \"Título del informe de auditoría\",\n"
        "  \"area\": \"Área principal auditada\",\n"
        "  \"auditor\": \"Nombre del auditor o Auditoría Interna\",\n"
        "  \"findings\": [\n"
        "    {\n"
        "      \"title\": \"...\",\n"
        "      \"situation\": \"...\",\n"
        "      \"severity\": \"Alto|Medio|Bajo\",\n"
        "      \"responsible_area\": \"...\",\n"
        "      \"proposal\": \"...\"\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Archivo: {filename}\n\nTexto del informe:\n\n{raw_text[:45000]}"}
            ],
            temperature=0.2,
            max_tokens=4000
        )
        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        if data and isinstance(data.get("findings"), list) and len(data["findings"]) > 0:
            return data
    except Exception as exc:
        print(f"Error procesando con OpenAI GPT: {exc}")

    return None


def parse_excel_findings(file_path, filename):
    wb = load_workbook(file_path, data_only=True)
    findings = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # Skip headers if present
        start_row = 0
        header_text = " ".join([str(c) for c in rows[0] if c]).lower()
        if any(kw in header_text for kw in ["hallazgo", "titulo", "observacion", "riesgo", "descripcion", "area"]):
            start_row = 1

        for idx, row in enumerate(rows[start_row:], start=1):
            cell_texts = [clean_text(c) for c in row if clean_text(c)]
            if not cell_texts or len(cell_texts) < 1:
                continue

            row_str = " ".join(cell_texts)
            if len(row_str) < 10:
                continue

            title = cell_texts[0] if len(cell_texts[0]) < 80 else cell_texts[0][:75] + "..."
            situation = cell_texts[1] if len(cell_texts) > 1 else row_str
            proposal = cell_texts[2] if len(cell_texts) > 2 else f"Implementar plan de remediación para {title}."

            severity = "Medio"
            norm_row = normalize_text(row_str)
            if "alto" in norm_row or "critico" in norm_row:
                severity = "Alto"
            elif "bajo" in norm_row:
                severity = "Bajo"

            area = "Operaciones"
            for cell in cell_texts:
                if len(cell) < 30 and any(a in normalize_text(cell) for a in ["finanzas", "compras", "sistemas", "it", "rrhh", "operaciones", "logistica", "ventas", "creditos"]):
                    area = cell
                    break

            findings.append({
                "title": title,
                "situation": situation,
                "severity": severity,
                "responsible_area": area,
                "proposal": proposal
            })

    return findings


def parse_docx_audittrack_structure(file_path, filename):
    doc = Document(file_path)
    full_raw_text = "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])

    explicit_area = extract_explicit_area(full_raw_text)
    explicit_auditor = extract_explicit_auditor(full_raw_text)

    lines = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]

    report_title = f"Informe de Auditoría - {filename}"
    for l in lines[:10]:
        if "informe" in l.lower() or "auditoría" in l.lower():
            report_title = clean_text(l)
            break

    findings_raw = []
    proposals_raw = []

    current_severity = "Alto"
    in_section_7 = False
    in_section_8 = False
    current_finding = None

    for txt in lines:
        norm_txt = normalize_text(txt)

        if "7. hallazgos" in norm_txt or "7.hallazgos" in norm_txt:
            in_section_7 = True
            in_section_8 = False
            continue
        elif "8. propuestas de mejora" in norm_txt or "8.propuestas" in norm_txt:
            in_section_7 = False
            in_section_8 = True
            if current_finding:
                findings_raw.append(current_finding)
                current_finding = None
            continue
        elif "10. conclusion" in norm_txt or "anexos" in norm_txt:
            in_section_7 = False
            in_section_8 = False
            if current_finding:
                findings_raw.append(current_finding)
                current_finding = None
            continue

        if in_section_7:
            if "riesgo alto" in norm_txt:
                current_severity = "Alto"
            elif "riesgo medio" in norm_txt:
                current_severity = "Medio"
            elif "riesgo bajo" in norm_txt:
                current_severity = "Bajo"

            match_h = re.match(r"^hallazgo\s*(\d+)[:\s]*(.*)", txt, re.IGNORECASE)
            if match_h:
                if current_finding:
                    findings_raw.append(current_finding)
                num = match_h.group(1)
                title = clean_text(match_h.group(2)) or f"Hallazgo {num}"
                current_finding = {
                    "num": int(num),
                    "title": title,
                    "severity": current_severity,
                    "situation": [],
                    "risk": []
                }
            elif current_finding:
                if "conclusión:" in norm_txt or "conclusion:" in norm_txt:
                    current_finding["risk"].append(txt)
                else:
                    current_finding["situation"].append(txt)

        elif in_section_8:
            if len(txt) > 20 and not txt.startswith("8."):
                proposals_raw.append(txt)

    if current_finding:
        findings_raw.append(current_finding)

    if not findings_raw:
        return None

    relational_findings = []
    for idx, f in enumerate(findings_raw, start=1):
        raw_sit = " ".join(f["situation"]) if f["situation"] else f["title"]
        raw_risk = " ".join(f["risk"]) if f["risk"] else "Riesgo de control interno."

        matched_prop = ""
        for p in proposals_raw:
            if f"hallazgo {f['num']}" in normalize_text(p):
                matched_prop = p
                break
        if not matched_prop and proposals_raw and idx <= len(proposals_raw):
            matched_prop = proposals_raw[idx - 1]

        prop_text = matched_prop if matched_prop else f"Implementar medidas de control y remediación para {f['title']}."

        relational_findings.append({
            "title": f["title"],
            "situation": raw_sit,
            "severity": f["severity"],
            "responsible_area": explicit_area,
            "proposal": prop_text
        })

    return {
        "report_title": report_title,
        "area": explicit_area,
        "auditor": explicit_auditor,
        "findings": relational_findings
    }


def parse_audit_report(file_path, filename):
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    raw_text = extract_raw_text_from_file(file_path)

    # STEP 1: AI Parse with OpenAI (Primary & Most Intelligent Extraction)
    ai_result = parse_with_openai_gpt(raw_text, filename)
    extracted_findings = []
    report_title = f"Informe de Auditoría - {filename}"
    explicit_area = extract_explicit_area(raw_text)
    explicit_auditor = extract_explicit_auditor(raw_text)

    if ai_result and ai_result.get("findings"):
        report_title = ai_result.get("report_title") or report_title
        explicit_area = ai_result.get("area") or explicit_area
        explicit_auditor = ai_result.get("auditor") or explicit_auditor
        extracted_findings = ai_result.get("findings")
    else:
        # STEP 2: Heuristic Fallbacks if AI fails or no API key configured
        if ext == "docx":
            parsed_doc = parse_docx_audittrack_structure(file_path, filename)
            if parsed_doc and parsed_doc.get("findings"):
                report_title = parsed_doc.get("report_title")
                explicit_area = parsed_doc.get("area")
                explicit_auditor = parsed_doc.get("auditor")
                extracted_findings = parsed_doc.get("findings")

        if not extracted_findings and ext in ("xlsx", "csv"):
            extracted_findings = parse_excel_findings(file_path, filename)

        # STEP 3: Paragraph/Item Fallback for TXT/PDF/Docs (split by numbered items or paragraphs)
        if not extracted_findings and raw_text:
            lines = [clean_text(l) for l in raw_text.splitlines() if clean_text(l)]
            current_item = []
            items = []
            for l in lines:
                if re.match(r"^(?:hallazgo|observación|observacion|punto|ítem|item|\d+[\.\-\)])", l, re.IGNORECASE):
                    if current_item:
                        items.append(" ".join(current_item))
                        current_item = []
                current_item.append(l)
            if current_item:
                items.append(" ".join(current_item))

            for idx, item_str in enumerate(items, start=1):
                if len(item_str) > 15:
                    extracted_findings.append({
                        "title": f"Hallazgo {idx}: " + (item_str[:60] + "..."),
                        "situation": item_str,
                        "severity": "Medio",
                        "responsible_area": explicit_area,
                        "proposal": f"Establecer e implementar medidas correctivas inmediatas para el hallazgo {idx}."
                    })

    # If still empty, provide minimal fallback
    if not extracted_findings:
        extracted_findings = [{
            "title": f"Revisión de Control Interno - {filename}",
            "situation": raw_text[:300] if raw_text else "Se requiere revisión de los controles identificados en el informe.",
            "severity": "Medio",
            "responsible_area": explicit_area,
            "proposal": "Implementar plan de acción de remediación según recomendaciones de auditoría."
        }]

    # Format findings into AuditTrack relational structure
    relational_findings = []
    for idx, f in enumerate(extracted_findings, start=1):
        h_code = f"H-2026-{idx:03d}"
        pm_code = f"PM-2026-{idx:03d}"
        pa_code = f"PA-2026-{idx:03d}"

        title = clean_text(f.get("title", f"Hallazgo {idx}"))
        situation = clean_text(f.get("situation", title))
        proposal = clean_text(f.get("proposal", f"Implementar recomendaciones para {title}"))
        severity = f.get("severity", "Medio")
        area = f.get("responsible_area") or explicit_area

        relational_findings.append({
            "code": h_code,
            "title": title,
            "situation": situation,
            "risk": f"Riesgo de control interno asociado a {title}.",
            "severity": severity if severity in ("Alto", "Medio", "Bajo") else "Medio",
            "responsible_area": area,
            "action_owner": "Pendiente de definir",
            "status": "En proceso",
            "proposals": [
                {
                    "code": pm_code,
                    "title": proposal,
                    "proposal_text": proposal,
                    "target_date": "2026-10-31",
                    "status": "En proceso",
                    "action_plans": [
                        {
                            "code": pa_code,
                            "title": f"Acción comprometida {pa_code}",
                            "action_text": "Pendiente de definición por el área responsable",
                            "action_owner": "Pendiente de definir",
                            "target_date": "2026-10-15",
                            "progress_pct": 0,
                            "status": "Pendiente",
                            "notes": ""
                        }
                    ]
                }
            ]
        })

    return {
        "report": {
            "title": report_title,
            "process": "Control Interno",
            "area": explicit_area,
            "period": "2026",
            "auditor": explicit_auditor,
            "summary": f"Informe {filename} ingestado con {len(relational_findings)} hallazgos."
        },
        "findings": relational_findings
    }
