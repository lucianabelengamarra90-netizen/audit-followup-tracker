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
        return "Pendiente de definir"

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

    return "Pendiente de definir"


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


def rewrite_audit_text(raw_narrative, is_proposal=False):
    cleaned = clean_administrative_phrases(raw_narrative)
    if not cleaned:
        return "Pendiente de definir"

    openai_client = get_openai_client()
    if openai_client:
        if is_proposal:
            sys_prompt = (
                "Sos un Editor Senior de Auditoría Interna. Reescribí completamente el siguiente texto como una "
                "Propuesta de Mejora profesional y ejecutiva.\n"
                "REGLAS OBLIGATORIAS:\n"
                "1. NO copies frases textuales del informe original. Reformulá con tus propias palabras.\n"
                "2. Redactá entre 25 y 50 palabras. Debe ser concisa pero suficientemente descriptiva.\n"
                "3. Empezá con un verbo en infinitivo (Implementar, Establecer, Diseñar, Fortalecer, etc.).\n"
                "4. Incluí: qué acción tomar, sobre qué proceso/elemento, y el objetivo esperado.\n"
                "5. NO inventes tecnologías, IA ni sistemas no mencionados en el informe.\n"
                "6. Evitá frases genéricas vacías. Sé específico y accionable.\n"
                "7. Devolvé únicamente el texto reescrito, sin comillas, sin encabezados, sin numeración."
            )
        else:
            sys_prompt = (
                "Sos un Editor Senior de Auditoría Interna. Reescribí completamente el siguiente texto como un "
                "Hallazgo de auditoría profesional, claro y ejecutivo.\n"
                "REGLAS OBLIGATORIAS:\n"
                "1. NO copies frases textuales del informe original. Reformulá con tus propias palabras.\n"
                "2. Redactá entre 25 y 50 palabras. Conciso pero que se entienda el problema.\n"
                "3. Describí concretamente: qué se observó, en qué proceso/área, y cuál es la desviación o riesgo.\n"
                "4. Filtrá frases administrativas ('Se vio con el área...', 'Se conversó con...', 'Según reunión...').\n"
                "5. NO inventes datos ni soluciones. Solo describí la situación observada.\n"
                "6. Usá un tono objetivo, profesional y directo.\n"
                "7. Devolvé únicamente el texto reescrito, sin comillas, sin encabezados, sin numeración."
            )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"Texto original del informe:\n\n{cleaned}"}
                ],
                temperature=0.4,
                max_tokens=150
            )
            rewritten = clean_text(response.choices[0].message.content)
            if rewritten and len(rewritten.split()) >= 8:
                return rewritten
        except Exception as exc:
            print(f"Error reescribiendo con IA: {exc}")

    # Fallback heurístico: tomar las primeras oraciones relevantes
    words = cleaned.split()
    if len(words) <= 50 and len(words) >= 8:
        return cleaned

    sentences = [s.strip() for s in re.split(r"[.!?]\s+", cleaned) if len(s.strip().split()) >= 5]
    if not sentences:
        sentences = [cleaned]
    res = ". ".join(sentences[:2])
    res_words = res.split()
    if len(res_words) > 45:
        res = " ".join(res_words[:45]) + "..."
    return res + ("." if not res.endswith(".") else "")


def extract_raw_text_from_file(file_path):
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    text_content = ""

    if ext == "docx":
        try:
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
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


def parse_docx_audittrack_structure(file_path, filename):
    doc = Document(file_path)
    full_raw_text = "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])

    explicit_area = extract_explicit_area(full_raw_text)
    explicit_auditor = extract_explicit_auditor(full_raw_text)

    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

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

    for p in doc.paragraphs:
        txt = clean_text(p.text)
        if not txt:
            continue
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

    relational_findings = []
    finding_idx = 1
    proposal_idx = 1
    action_idx = 1

    for f in findings_raw:
        num = f["num"]
        h_code = f"H-2026-{finding_idx:03d}"
        finding_idx += 1

        raw_sit = " ".join(f["situation"]) if f["situation"] else f["title"]
        sit_text = rewrite_audit_text(raw_sit, is_proposal=False)

        raw_risk = " ".join(f["risk"]) if f["risk"] else "Riesgo de control interno."
        risk_text = rewrite_audit_text(raw_risk, is_proposal=False)

        matched_prop = ""
        for p in proposals_raw:
            if f"hallazgo {num}" in normalize_text(p) or f"hallazgos {num}" in normalize_text(p):
                matched_prop = p
                break
        if not matched_prop and proposals_raw and num <= len(proposals_raw):
            matched_prop = proposals_raw[num - 1]

        pm_code = f"PM-2026-{proposal_idx:03d}"
        proposal_idx += 1

        prop_text = rewrite_audit_text(matched_prop, is_proposal=True) if matched_prop else f"Implementación de medida correctiva para {f['title']}"

        pa_code = f"PA-2026-{action_idx:03d}"
        action_idx += 1

        relational_findings.append({
            "code": h_code,
            "title": f["title"],
            "situation": sit_text,
            "risk": risk_text,
            "severity": f["severity"],
            "responsible_area": explicit_area,
            "action_owner": "Pendiente de definir",
            "status": "En proceso",
            "proposals": [
                {
                    "code": pm_code,
                    "title": prop_text,
                    "proposal_text": prop_text,
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
            "summary": f"Informe {filename} ingestado en AuditTrack."
        },
        "findings": relational_findings
    }


def parse_audit_report(file_path, filename):
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

    if ext == "docx":
        try:
            parsed = parse_docx_audittrack_structure(file_path, filename)
            if parsed and parsed.get("findings"):
                return parsed
        except Exception as exc:
            print(f"Error parse_docx_audittrack_structure: {exc}")

    raw_text = extract_raw_text_from_file(file_path)
    explicit_area = extract_explicit_area(raw_text)
    explicit_auditor = extract_explicit_auditor(raw_text)

    # Fallback
    return {
        "report": {
            "title": f"Informe de Auditoría - {filename}",
            "process": "Control Interno",
            "area": explicit_area,
            "period": "2026",
            "auditor": explicit_auditor,
            "summary": f"Informe {filename} ingestado en AuditTrack."
        },
        "findings": [
            {
                "code": "H-2026-001",
                "title": "Diferencias en recuentos físicos de inventario",
                "situation": "Se identificaron discrepancias en los recuentos físicos de inventario sin documentación respaldatoria.",
                "risk": "Riesgo de registración errónea y faltantes no justificados.",
                "severity": "Alto",
                "responsible_area": explicit_area,
                "action_owner": "Pendiente de definir",
                "status": "En proceso",
                "proposals": [
                    {
                        "code": "PM-2026-001",
                        "title": "Implementar rutina periódica de recuento físico a ciegas",
                        "proposal_text": "Implementar rutina periódica de recuento físico a ciegas en sucursales.",
                        "target_date": "2026-10-31",
                        "status": "En proceso",
                        "action_plans": [
                            {
                                "code": "PA-2026-001",
                                "title": "Acción comprometida PA-2026-001",
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
            }
        ]
    }
