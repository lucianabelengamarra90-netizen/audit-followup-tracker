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
    if not api_key:
        return None
    try:
        return OpenAI(api_key=api_key)
    except Exception as exc:
        print(f"Error instanciando cliente OpenAI: {exc}")
        return None


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
        "Analizá el siguiente texto de un informe de auditoría y extraé TODOS los hallazgos cualitativos "
        "y sus propuestas de mejora de auditoría.\n\n"
        "REGLAS CRÍTICAS:\n"
        "1. Extraé CADA hallazgo/observación/desviación de control interno presente en el documento.\n"
        "2. Para CADA hallazgo generá:\n"
        "   - 'title': Título conciso del hallazgo (máx 10 palabras).\n"
        "   - 'situation': Redacción profesional y ejecutiva del problema/situación observada (25-50 palabras).\n"
        "   - 'severity': 'Alto', 'Medio' o 'Bajo' según el riesgo.\n"
        "   - 'responsible_area': Área o proceso auditado.\n"
        "   - 'proposal': Propuesta de mejora accionable que empiece con un verbo en infinitivo (Implementar, Establecer, Diseñar, Fortalecer, etc., 25-50 palabras).\n"
        "3. Devolvé ÚNICAMENTE un objeto JSON válido con la siguiente estructura exacta (sin markdown extra):\n"
        "{\n"
        "  \"report_title\": \"Título del informe\",\n"
        "  \"area\": \"Área principal\",\n"
        "  \"auditor\": \"Auditoría Interna\",\n"
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


def parse_flexible_doc(file_path, filename, raw_text):
    explicit_area = extract_explicit_area(raw_text)
    explicit_auditor = extract_explicit_auditor(raw_text)
    report_title = f"Informe de Auditoría - {filename}"

    lines = [clean_text(l) for l in raw_text.splitlines() if clean_text(l)]
    for l in lines[:10]:
        if "informe" in l.lower() or "auditoría" in l.lower() or "auditoria" in l.lower():
            report_title = clean_text(l)
            break

    findings_raw = []
    current_finding = None

    # Flexible regex to identify findings, observations, issues, or numbered items
    finding_pattern = r"^(?:hallazgo|observación|observacion|desviación|desviacion|punto|ítem|item|\d+[\.\-\)])"

    for line in lines:
        norm_line = normalize_text(line)
        is_finding_start = bool(re.match(finding_pattern, line, re.IGNORECASE))
        if not is_finding_start and ("hallazgo" in norm_line or "observacion" in norm_line) and len(line) < 100:
            is_finding_start = True

        if is_finding_start:
            if current_finding:
                findings_raw.append(current_finding)
            
            title = line[:80]
            current_finding = {
                "title": title,
                "situation": line,
                "severity": "Alto" if "alto" in norm_line else ("Bajo" if "bajo" in norm_line else "Medio"),
                "responsible_area": explicit_area,
                "proposal": f"Implementar medidas correctivas y control interno para {title[:50]}."
            }
        elif current_finding:
            if "propuesta" in norm_line or "recomendación" in norm_line or "recomendacion" in norm_line:
                current_finding["proposal"] = line
            else:
                current_finding["situation"] += " " + line

    if current_finding:
        findings_raw.append(current_finding)

    # Fallback if no specific finding headers found: group paragraphs into findings
    if not findings_raw and len(lines) > 0:
        chunk_size = 3
        for i in range(0, len(lines), chunk_size):
            chunk = " ".join(lines[i:i+chunk_size])
            if len(chunk) > 20:
                findings_raw.append({
                    "title": f"Observación {len(findings_raw)+1}: " + chunk[:60] + "...",
                    "situation": chunk,
                    "severity": "Medio",
                    "responsible_area": explicit_area,
                    "proposal": f"Establecer e implementar plan de acción para la observación {len(findings_raw)+1}."
                })

    return {
        "report_title": report_title,
        "area": explicit_area,
        "auditor": explicit_auditor,
        "findings": findings_raw
    }


def parse_audit_report(file_path, filename):
    raw_text = extract_raw_text_from_file(file_path)

    # STEP 1: Attempt AI parsing with GPT-4o-mini
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
        # STEP 2: Flexible native parsing from actual document content
        parsed_doc = parse_flexible_doc(file_path, filename, raw_text)
        if parsed_doc and parsed_doc.get("findings"):
            report_title = parsed_doc.get("report_title")
            explicit_area = parsed_doc.get("area")
            explicit_auditor = parsed_doc.get("auditor")
            extracted_findings = parsed_doc.get("findings")

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
