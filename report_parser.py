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
        "Analizá el siguiente texto de un informe de auditoría y extraé ÚNICAMENTE los hallazgos de auditoría "
        "con sus propuestas de mejora tal como están redactados en el documento.\n\n"
        "REGLAS CRÍTICAS:\n"
        "1. Un hallazgo es una desviación, observación, debilidad de control o incumplimiento detectado en la auditoría. "
        "NO son hallazgos: el alcance, la metodología, los objetivos, la introducción, las conclusiones generales "
        "ni ninguna sección administrativa del informe.\n"
        "2. Para CADA hallazgo real extraé:\n"
        "   - 'title': Título conciso del hallazgo (máx 10 palabras). Tomalo del documento si existe.\n"
        "   - 'situation': La situación o condición observada, redactada de forma profesional y ejecutiva "
        "(entre 25 y 60 palabras). Basate en el texto original del documento, mejorá la redacción si es informal.\n"
        "   - 'severity': 'Alto', 'Medio' o 'Bajo' según el riesgo indicado en el documento o tu criterio.\n"
        "   - 'responsible_area': Área, proceso o gerencia a la que corresponde el hallazgo.\n"
        "   - 'proposal': La propuesta de mejora ORIGINAL del documento asociada a este hallazgo. "
        "Si el documento tiene una propuesta de mejora explícita para este hallazgo, COPIÁLA (mejorada pero fiel). "
        "Si no hay propuesta en el documento, generá una accionable que empiece con verbo infinitivo "
        "(Implementar, Establecer, Diseñar, Fortalecer, etc.), entre 25 y 50 palabras.\n"
        "3. Si el documento tiene 5 hallazgos, devolvé 5. Si tiene 10, devolvé 10. Extraé TODOS los que haya.\n"
        "4. Devolvé ÚNICAMENTE un objeto JSON válido con la siguiente estructura (sin markdown extra):\n"
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
            temperature=0.1,
            max_tokens=6000
        )
        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        if data and isinstance(data.get("findings"), list) and len(data["findings"]) > 0:
            return data
    except Exception as exc:
        print(f"Error procesando con OpenAI GPT: {exc}")

    return None


# Sections that are NOT findings — used to avoid false positives in heuristic parser
NON_FINDING_SECTIONS = {
    "alcance", "metodologia", "metodología", "objetivo", "objetivos",
    "introduccion", "introducción", "antecedentes", "contexto",
    "conclusion", "conclusión", "conclusiones", "resumen ejecutivo",
    "anexo", "anexos", "referencias", "glosario", "índice", "indice",
    "marco normativo", "marco de referencia", "cronograma", "equipo auditor",
    "distribucion", "distribución", "carátula", "caratula", "portada",
    "periodo", "período", "plan de mejoras", "plan de accion",
}


def _is_non_finding_section(line_norm):
    """Return True if the line is a known document section that is NOT a finding."""
    for kw in NON_FINDING_SECTIONS:
        # Match "7. Alcance", "2.1 Metodología", "Alcance:", etc.
        if re.search(r"(?:^|\d[\.\s]+)" + re.escape(kw) + r"s?\b", line_norm):
            return True
        if line_norm.strip() == kw:
            return True
    return False


def _extract_section_blocks(lines):
    """
    Identify the start index of the Hallazgos section and, optionally,
    the Propuestas de Mejora section in the document.
    Returns (hallazgos_start, proposals_start) where values are line indices or None.
    """
    hallazgos_start = None
    proposals_start = None

    hallazgo_section_re = re.compile(
        r"(?:\d+[\.\-\s]*)?(?:hallazgo|hallazgos|observacion|observaciones|desviacion|desviaciones|"
        r"resultados de la auditoria|resultados de auditoría)\s*$",
        re.IGNORECASE
    )
    proposal_section_re = re.compile(
        r"(?:\d+[\.\-\s]*)?(?:propuesta|propuestas|recomendacion|recomendaciones|plan de mejora|"
        r"mejoras recomendadas|acciones correctivas)\s*$",
        re.IGNORECASE
    )

    for i, line in enumerate(lines):
        norm = normalize_text(line)
        if hallazgo_section_re.match(norm) and len(line) < 80:
            hallazgos_start = i
        elif proposal_section_re.match(norm) and len(line) < 80:
            proposals_start = i

    return hallazgos_start, proposals_start


def parse_flexible_doc(file_path, filename, raw_text):
    """
    Heuristic parser used when no OpenAI API key is available.
    Scans the document for a "Hallazgos" section and extracts each finding
    together with its inline "Propuesta de mejora".
    """
    explicit_area = extract_explicit_area(raw_text)
    explicit_auditor = extract_explicit_auditor(raw_text)
    report_title = f"Informe de Auditoría - {filename}"

    lines = [clean_text(l) for l in raw_text.splitlines() if clean_text(l)]

    # Try to detect report title from first meaningful lines
    for l in lines[:10]:
        if "informe" in l.lower() or "auditoría" in l.lower() or "auditoria" in l.lower():
            report_title = clean_text(l)
            break

    hallazgos_start, proposals_start = _extract_section_blocks(lines)

    # Determine the range to scan for findings
    scan_start = (hallazgos_start + 1) if hallazgos_start is not None else 0
    scan_end = proposals_start if proposals_start is not None else len(lines)

    findings_raw = []
    current_finding = None

    # Pattern: explicit "Hallazgo N" or "Observación N" labels
    finding_label_re = re.compile(
        r"^(?:hallazgo|observaci[oó]n|desviaci[oó]n|punto|ítem|item)\s*(?:n[°º]?\s*)?\d+",
        re.IGNORECASE
    )
    # Pattern: "Propuesta de mejora" or "Recomendación" inline label
    proposal_label_re = re.compile(
        r"^(?:propuesta\s+de\s+mejora|recomendaci[oó]n|propuesta|accion\s+correctiva)[:\s]",
        re.IGNORECASE
    )

    for i in range(scan_start, scan_end):
        line = lines[i]
        norm_line = normalize_text(line)

        # Skip lines that belong to non-finding administrative sections
        if _is_non_finding_section(norm_line):
            continue

        is_finding_start = bool(finding_label_re.match(line))

        # Also treat as finding start if there is no explicit hallazgos section,
        # the line is a short heading (< 120 chars) and contains "hallazgo" or "observación"
        if not is_finding_start and hallazgos_start is None:
            if ("hallazgo" in norm_line or "observacion" in norm_line) and len(line) < 120:
                is_finding_start = True

        if is_finding_start:
            if current_finding:
                findings_raw.append(current_finding)
            current_finding = {
                "title": line[:100],
                "situation": "",
                "severity": "Alto" if "alto" in norm_line else ("Bajo" if "bajo" in norm_line else "Medio"),
                "responsible_area": explicit_area,
                "proposal": ""
            }
        elif current_finding is not None:
            if proposal_label_re.match(line):
                # Everything after the label keyword is the proposal text
                proposal_text = re.sub(proposal_label_re, "", line).strip()
                current_finding["proposal"] = proposal_text
            elif current_finding["proposal"]:
                # Continue appending to the proposal if it spans multiple lines
                current_finding["proposal"] += " " + line
            else:
                # Append to situation text
                current_finding["situation"] += " " + line

    if current_finding:
        findings_raw.append(current_finding)

    # Clean up whitespace
    for f in findings_raw:
        f["title"] = clean_text(f["title"])
        f["situation"] = clean_text(f["situation"]) or f["title"]
        f["proposal"] = clean_text(f["proposal"])

    # If still no findings found, try a last-resort approach: look anywhere in doc
    # for "Hallazgo X" / "Observación X" patterns, without section constraints
    if not findings_raw:
        current_finding = None
        for line in lines:
            norm_line = normalize_text(line)
            if _is_non_finding_section(norm_line):
                continue
            is_finding_start = bool(finding_label_re.match(line))
            if is_finding_start:
                if current_finding:
                    findings_raw.append(current_finding)
                current_finding = {
                    "title": line[:100],
                    "situation": "",
                    "severity": "Medio",
                    "responsible_area": explicit_area,
                    "proposal": ""
                }
            elif current_finding is not None:
                if proposal_label_re.match(line):
                    current_finding["proposal"] = re.sub(proposal_label_re, "", line).strip()
                elif current_finding["proposal"]:
                    current_finding["proposal"] += " " + line
                else:
                    current_finding["situation"] += " " + line
        if current_finding:
            findings_raw.append(current_finding)

        for f in findings_raw:
            f["title"] = clean_text(f["title"])
            f["situation"] = clean_text(f["situation"]) or f["title"]
            f["proposal"] = clean_text(f["proposal"])

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
        raw_proposal = f.get("proposal", "") or f.get("recommendation", "") or f.get("propuesta", "")
        proposal = clean_text(raw_proposal)
        if not proposal:
            proposal = f"Implementar medidas de control y mejora para: {title[:60]}."
        severity = f.get("severity", "Medio")
        area = f.get("responsible_area") or explicit_area

        print(f"[DEBUG Parser] H-{idx}: title={title[:50]}, proposal={proposal[:80]}")

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
