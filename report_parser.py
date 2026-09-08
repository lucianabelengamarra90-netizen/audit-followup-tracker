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

    return text_content[:50000]


def heuristic_parse_audit_report(raw_text, filename):
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    norm_text = normalize_text(raw_text)

    # Detección de Título de Auditoría
    title = f"Informe de Auditoría - {filename}"
    for line in lines[:10]:
        if any(w in line.lower() for w in ["auditoría", "informe", "revisión", "evaluación"]):
            title = clean_text(line[:120])
            break

    # Detección de Proceso
    process = "Gestión Operativa y Financiera"
    if "crédito" in norm_text or "prestamo" in norm_text or "cobranza" in norm_text:
        process = "Gestión de Préstamos y Cobranzas"
    elif "compra" in norm_text or "proveedor" in norm_text:
        process = "Proceso de Compras y Abastecimiento"
    elif "tesoreria" in norm_text or "pago" in norm_text:
        process = "Gestión de Tesorería y Pagos"
    elif "nomina" in norm_text or "recursos humanos" in norm_text:
        process = "Nómina y Liquidación de Haberes"

    area = "Operaciones"
    if "área" in norm_text or "area" in norm_text:
        match_area = re.search(r"área:\s*([^\n|]+)", raw_text, re.IGNORECASE)
        if match_area:
            area = clean_text(match_area.group(1)[:60])

    period = "Período Relevado 2025-2026"
    auditor = "Equipo de Auditoría Interna"

    # Ingesta heurística de hallazgos
    findings = []
    current_obs = None
    obs_counter = 1

    for line in lines:
        norm_line = normalize_text(line)

        # Detectar líneas con excepciones / hallazgos
        is_finding_line = any(kw in norm_line for kw in [
            "hallazgo", "observación", "observacion", "diferencia", "inconsistencia",
            "no coincide", "falta de", "debilidad", "incumplimiento", "desvío", "desvio"
        ])

        if is_finding_line and not any(fp in norm_line for fp in ["hallazgos generales", "resumen de hallazgos", "cuadro de hallazgos", "sin observaciones"]):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            narrative = " | ".join(parts[:3]) if len(parts) > 1 else line

            severity = "Media"
            if any(k in norm_line for k in ["alta", "crítico", "critico", "grave"]):
                severity = "Alta"
            elif any(k in norm_line for k in ["baja", "menor", "leve"]):
                severity = "Baja"

            finding_type = "Oportunidad de Mejora"
            if "diferencia" in norm_line or "inconsistencia" in norm_line:
                finding_type = "Diferencia de Datos"
            elif "falta" in norm_line or "sin respaldo" in norm_line:
                finding_type = "Falta de Documentación"
            elif "debilidad" in norm_line or "control" in norm_line:
                finding_type = "Debilidad de Control Interno"

            findings.append({
                "code": f"OBS-{obs_counter:02d}",
                "type": finding_type,
                "process_step": "Evaluación del Proceso",
                "title": f"Observación {obs_counter}: {clean_text(narrative[:80])}",
                "situation": clean_text(narrative),
                "risk": "Riesgo de descalce operativo, inconsistencia de registros o debilidad de control interno.",
                "proposal": "Regularizar la situación documentada, formalizar los registros y ajustar los procedimientos.",
                "severity": severity,
                "responsible_area": area,
                "action_owner": "Responsable del Área Auditada",
                "target_date": "2026-10-30",
                "status": "Pendiente"
            })
            obs_counter += 1

    if not findings:
        findings.append({
            "code": "OBS-01",
            "type": "Oportunidad de Mejora",
            "process_step": "Revisión General",
            "title": f"Revisión de Cumplimiento - {title[:50]}",
            "situation": f"Se completó la ingesta del informe {filename}. Reorganizar las observaciones específicas.",
            "risk": "Vulnerabilidad en el control interno del proceso.",
            "proposal": "Implementar controles periódicos y monitoreo continuo.",
            "severity": "Media",
            "responsible_area": area,
            "action_owner": "Responsable del Proceso",
            "target_date": "2026-11-15",
            "status": "Pendiente"
        })

    return {
        "report": {
            "title": title,
            "process": process,
            "area": area,
            "period": period,
            "auditor": auditor,
            "summary": f"Informe de Auditoría cargado desde el archivo {filename} conteniendo {len(findings)} hallazgos y oportunidades de mejora."
        },
        "findings": findings
    }


def parse_audit_report(file_path, filename):
    raw_text = extract_raw_text_from_file(file_path)
    if not raw_text.strip():
        return heuristic_parse_audit_report(f"Informe {filename}", filename)

    openai_client = get_openai_client()
    if not openai_client:
        return heuristic_parse_audit_report(raw_text, filename)

    instructions = """
Actuá como Auditor Senior especializado en Auditoría Interna y Gestión de Riesgos.
Analizarás el texto completo de un Informe de Auditoría Interna subido en formato PDF, Word o Excel.
Tu objetivo es extraer con máxima precisión la información del informe y ESTRUCTURARLA en JSON con la siguiente forma exacta:

{
  "report": {
    "title": "Nombre/Título completo del Informe de Auditoría",
    "process": "Proceso de negocio principal evaluado (ej: Gestión de Préstamos, Compras, Tesorería)",
    "area": "Área o departamento auditado",
    "period": "Período auditado",
    "auditor": "Auditor o equipo a cargo",
    "summary": "Resumen ejecutivo del informe"
  },
  "findings": [
    {
      "code": "OBS-01",
      "type": "Categoría (ej: 'Debilidad de Control', 'Falta de Documentación', 'Inconsistencia de Datos', 'Oportunidad de Mejora')",
      "process_step": "Etapa o subproceso específico del proceso donde ocurrió",
      "title": "Título corto y ejecutivo de la mejora o hallazgo",
      "situation": "Descripción objetiva de la Situación Observada",
      "risk": "Riesgo potencial o impacto financiero/operativo",
      "proposal": "Recomendación o propuesta del plan de acción",
      "severity": "Alta | Media | Baja",
      "responsible_area": "Área responsable del plan",
      "action_owner": "Persona o rol a cargo",
      "target_date": "YYYY-MM-DD (si no hay fecha, estimar 60 días futuros en formato YYYY-MM-DD)",
      "status": "Pendiente"
    }
  ]
}

RESTRICCIONES STRICTAS:
- NO INVENTES importes, fechas ni hechos que no estén en el texto.
- Descartar párrafos introductorios generales o títulos de tabla vacíos que no contengan observaciones reales.
- Si hay severidades/criticidades, asigná estrictamente 'Alta', 'Media' o 'Baja'.
- Devolvé ÚNICAMENTE el JSON sin formato adicional markdown ni saludos.
"""

    prompt = f"NOMBRE DEL ARCHIVO: {filename}\nCONTENIDO DEL INFORME DE AUDITORÍA:\n{raw_text[:35000]}"

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        out_text = clean_text(response.choices[0].message.content)
        json_match = re.search(r"\{.*\}", out_text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            if "report" in parsed and "findings" in parsed:
                return parsed
    except Exception as exc:
        print(f"Error OpenAI parse_audit_report: {exc}")

    return heuristic_parse_audit_report(raw_text, filename)
