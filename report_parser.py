import os
import re
import uuid
import unicodedata
from openpyxl import load_workbook
from docx import Document
from pypdf import PdfReader


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


# ──────────────────────────────────────────────────────────────
#  PARSER HEURÍSTICO INTELIGENTE (sin dependencia de IA)
# ──────────────────────────────────────────────────────────────

# Secciones que NO son hallazgos
NON_FINDING_KEYWORDS = {
    "alcance", "metodologia", "metodología", "objetivo", "objetivos",
    "introduccion", "introducción", "antecedentes", "contexto",
    "conclusion", "conclusión", "conclusiones", "resumen ejecutivo",
    "anexo", "anexos", "referencias", "glosario", "índice", "indice",
    "marco normativo", "marco de referencia", "cronograma", "equipo auditor",
    "distribucion", "distribución", "carátula", "caratula", "portada",
    "periodo", "período", "plan de accion", "plan de acción",
    "tabla de contenido", "contenido", "contenidos",
}

# Palabras clave que indican el inicio de un hallazgo
FINDING_KEYWORDS = [
    "hallazgo", "observacion", "observación", "desviacion", "desviación",
    "debilidad", "deficiencia", "incumplimiento", "punto de atención",
    "punto de atencion", "irregularidad", "riesgo detectado",
]

# Palabras clave que indican una propuesta / recomendación
PROPOSAL_KEYWORDS = [
    "propuesta de mejora", "propuesta", "recomendacion", "recomendación",
    "accion correctiva", "acción correctiva", "mejora sugerida",
    "sugerencia", "plan de accion", "plan de acción",
    "medida correctiva", "accion recomendada", "acción recomendada",
]


def _is_non_finding_header(norm_line):
    """Devuelve True si la línea es un header de sección administrativa (no un hallazgo)."""
    for kw in NON_FINDING_KEYWORDS:
        # "7. Alcance", "2.1 Metodología", "Alcance:", "ALCANCE", etc.
        pattern = r"(?:^|\d+[\.\s]+)" + re.escape(kw) + r"s?\s*[:.]?\s*$"
        if re.search(pattern, norm_line):
            return True
        if norm_line.strip().rstrip(":. ") == kw:
            return True
    return False


def _looks_like_finding_start(line, norm_line):
    """Detecta si la línea es el inicio de un nuevo hallazgo."""
    # Patrón explícito: "Hallazgo 1", "Observación N°3", "Hallazgo 1:", etc.
    explicit_re = re.compile(
        r"^(?:hallazgo|observaci[oó]n|desviaci[oó]n|punto|ítem|item)\s*(?:n[°º]?\s*)?\d+",
        re.IGNORECASE
    )
    if explicit_re.match(line):
        return True

    # Líneas cortas (<120 chars) que contienen palabras clave de hallazgo
    if len(line) < 120:
        for kw in FINDING_KEYWORDS:
            if kw in norm_line:
                return True

    return False


def _looks_like_proposal_start(line, norm_line):
    """Detecta si la línea es el inicio de una propuesta de mejora."""
    for kw in PROPOSAL_KEYWORDS:
        if norm_line.startswith(kw):
            return True
        # "Propuesta de mejora:", "Recomendación:", etc.
        pattern = re.escape(kw) + r"\s*[:\-]"
        if re.match(pattern, norm_line):
            return True
    return False


def _extract_after_keyword(line, norm_line):
    """Extrae el texto que viene después de la keyword de propuesta/hallazgo."""
    for kw in PROPOSAL_KEYWORDS:
        pattern = re.compile(re.escape(kw) + r"\s*[:\-]\s*", re.IGNORECASE)
        m = pattern.match(line)
        if m:
            return line[m.end():].strip()
    return line


def _detect_severity(text):
    """Detecta la severidad a partir del texto."""
    norm = normalize_text(text)
    if "alto" in norm or "critico" in norm or "crítico" in norm or "grave" in norm:
        return "Alto"
    if "bajo" in norm or "leve" in norm or "menor" in norm:
        return "Bajo"
    return "Medio"


def _clean_finding_title(raw_title):
    """
    Limpia el título del hallazgo:
    - Quita prefijos como "Hallazgo 1:", "Observación N°3:", "Hallazgo 1 -", etc.
    - Limita a ~80 caracteres
    - Capitaliza la primera letra
    """
    title = clean_text(raw_title)
    # Quitar prefijo "Hallazgo 1:", "Observación N°3 -", "Punto 2.", etc.
    title = re.sub(
        r"^(?:hallazgo|observaci[oó]n|desviaci[oó]n|punto|ítem|item)\s*(?:n[°º]?\s*)?\d*\s*[:\-\.]\s*",
        "", title, flags=re.IGNORECASE
    ).strip()
    if not title:
        return raw_title[:80]
    # Limitar longitud
    if len(title) > 80:
        # Cortar en el último espacio antes del límite
        cut = title[:80].rfind(" ")
        title = title[:cut] if cut > 20 else title[:80]
    # Capitalizar primera letra
    if title:
        title = title[0].upper() + title[1:]
    return title


def _summarize_text(text, max_chars=500, mode="finding"):
    """
    Genera un resumen inteligente del texto:
    - Divide en oraciones
    - Elimina frases administrativas/relleno
    - Puntúa cada oración por relevancia según keywords de auditoría
    - Selecciona las mejores oraciones que quepan en max_chars
    - mode='finding' prioriza vocabulario de hallazgos
    - mode='proposal' prioriza verbos de acción y mejora
    """
    text = clean_text(text)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    # Dividir en oraciones (por punto seguido de espacio o mayúscula)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

    if not sentences:
        return text[:max_chars]

    # Frases administrativas a eliminar
    filler_patterns = [
        r"se\s+(?:vio|conversó|habló|consultó|reunió|acordó)\s+con",
        r"según\s+(?:reunión|lo\s+informado|lo\s+conversado)",
        r"de\s+acuerdo\s+con\s+lo\s+informado",
        r"se\s+(?:adjunta|acompaña|incluye)\s+(?:como\s+)?anexo",
        r"ver\s+anexo",
        r"durante\s+(?:la\s+)?(?:reunión|visita|entrevista)",
        r"el\s+(?:área|sector)\s+(?:informó|comentó|indicó|manifestó)\s+que",
    ]

    # Keywords de hallazgos (para scoring)
    finding_keywords = [
        "no se cumple", "incumplimiento", "ausencia", "falta de", "carencia",
        "deficiencia", "debilidad", "riesgo", "vulnerabilidad", "desviación",
        "irregularidad", "no cuenta con", "no dispone", "no existe",
        "no se evidencia", "no se observa", "no se registra", "sin registro",
        "inadecuado", "insuficiente", "incompleto", "desactualizado",
        "no cumple", "omisión", "error", "diferencia", "discrepancia",
        "exposición", "impacto", "control interno", "normativa",
        "procedimiento", "política", "segregación", "conciliación",
        "inventario", "faltante", "sobrante", "material", "stock",
    ]

    # Keywords de propuestas (para scoring)
    proposal_keywords = [
        "implementar", "establecer", "diseñar", "fortalecer", "desarrollar",
        "generar", "elaborar", "definir", "documentar", "formalizar",
        "automatizar", "capacitar", "instruir", "controlar", "verificar",
        "asegurar", "garantizar", "mejorar", "optimizar", "revisar",
        "actualizar", "segregar", "conciliar", "regularizar", "normalizar",
        "mitigar", "prevenir", "corregir", "subsanar", "remediar",
        "se recomienda", "se sugiere", "se propone", "es necesario",
        "resulta necesario", "se deberá", "se deberían", "plan de acción",
    ]

    keywords = proposal_keywords if mode == "proposal" else finding_keywords

    scored_sentences = []
    for sent in sentences:
        norm_sent = normalize_text(sent)

        # Descartar frases administrativas/relleno
        is_filler = False
        for fp in filler_patterns:
            if re.search(fp, norm_sent):
                is_filler = True
                break
        if is_filler:
            continue

        # Calcular score
        score = 0
        for kw in keywords:
            if kw in norm_sent:
                score += 2

        # Bonus por longitud razonable (ni muy corta ni muy larga)
        if 30 < len(sent) < 200:
            score += 1

        # Penalizar oraciones que son solo referencias o números
        if re.match(r'^[\d\s,.\-/]+$', sent):
            score -= 5

        scored_sentences.append((score, sent))

    # Ordenar por score (mayor primero)
    scored_sentences.sort(key=lambda x: x[0], reverse=True)

    # Seleccionar las mejores oraciones que quepan
    selected = []
    total_len = 0
    for score, sent in scored_sentences:
        if total_len + len(sent) + 2 > max_chars:
            if not selected:  # Al menos una oración
                selected.append(sent[:max_chars])
            break
        selected.append(sent)
        total_len += len(sent) + 2  # +2 por ". "

    if not selected:
        return text[:max_chars]

    result = " ".join(selected)
    # Asegurar que termina con punto
    if result and not result.endswith((".","!","?")):
        result += "."
    return result


def parse_document(raw_text, filename):
    """
    Parser inteligente que extrae hallazgos y propuestas de mejora del texto.
    
    Estrategia:
    1. Recorre el documento línea por línea
    2. Cuando detecta el inicio de un hallazgo, acumula el texto
    3. Cuando detecta una propuesta de mejora, la asocia al hallazgo actual
    4. Al finalizar, limpia y acota el texto de cada hallazgo
    """
    explicit_area = extract_explicit_area(raw_text)
    explicit_auditor = extract_explicit_auditor(raw_text)
    report_title = f"Informe de Auditoría - {filename}"

    lines = [clean_text(l) for l in raw_text.splitlines() if clean_text(l)]

    # Detectar título del informe
    for l in lines[:15]:
        nl = l.lower()
        if "informe" in nl and ("auditoría" in nl or "auditoria" in nl or "inventario" in nl):
            report_title = clean_text(l)
            break

    findings = []
    current_finding = None
    reading_proposal = False  # Flag: estamos leyendo líneas de propuesta

    for line in lines:
        norm = normalize_text(line)

        # Ignorar secciones administrativas
        if _is_non_finding_header(norm) and len(line) < 120:
            if current_finding:
                findings.append(current_finding)
                current_finding = None
                reading_proposal = False
            continue

        # ¿Es inicio de un nuevo hallazgo?
        if _looks_like_finding_start(line, norm):
            if current_finding:
                findings.append(current_finding)

            current_finding = {
                "raw_title": line,      # Título bruto para limpiar después
                "situation_lines": [],  # Acumular líneas de situación
                "proposal_lines": [],   # Acumular líneas de propuesta
                "severity": _detect_severity(line),
                "responsible_area": explicit_area,
            }
            reading_proposal = False
            continue

        # ¿Es inicio de una propuesta de mejora?
        if current_finding and _looks_like_proposal_start(line, norm):
            proposal_text = _extract_after_keyword(line, norm)
            current_finding["proposal_lines"].append(proposal_text)
            reading_proposal = True
            continue

        # Acumular texto en el hallazgo actual
        if current_finding:
            if reading_proposal:
                if len(line) < 80 and line.endswith(":"):
                    reading_proposal = False
                    current_finding["situation_lines"].append(line)
                else:
                    current_finding["proposal_lines"].append(line)
            else:
                current_finding["situation_lines"].append(line)

    # Guardar el último hallazgo
    if current_finding:
        findings.append(current_finding)

    # ── EXCEL ESPECIAL: buscar hallazgos en filas tabulares ──
    if not findings and "|" in raw_text:
        findings = _parse_tabular_findings(lines, explicit_area)
    else:
        # ── Post-procesamiento: limpiar y acotar textos ──
        cleaned_findings = []
        for f in findings:
            raw_title = f.get("raw_title", "")
            situation_full = " ".join(f.get("situation_lines", []))
            proposal_full = " ".join(f.get("proposal_lines", []))

            title = _clean_finding_title(raw_title)
            situation = _summarize_text(situation_full, max_chars=500, mode="finding")
            if not situation:
                situation = title
            proposal = _summarize_text(proposal_full, max_chars=400, mode="proposal")
            if not proposal:
                proposal = f"Implementar medidas de control y mejora para: {title[:60]}."

            cleaned_findings.append({
                "title": title,
                "situation": situation,
                "proposal": proposal,
                "severity": f.get("severity", "Medio"),
                "responsible_area": f.get("responsible_area", explicit_area),
            })
        findings = cleaned_findings

    print(f"[Parser] Documento '{filename}': {len(findings)} hallazgos encontrados")
    for i, f in enumerate(findings, 1):
        print(f"  H-{i}: {f['title'][:60]} | Propuesta: {f['proposal'][:60]}")

    return {
        "report_title": report_title,
        "area": explicit_area,
        "auditor": explicit_auditor,
        "findings": findings
    }


def _parse_tabular_findings(lines, default_area):
    """
    Parser para documentos tabulares (Excel).
    Busca filas con separador | que contengan datos de hallazgos.
    """
    findings = []

    # Buscar filas que parezcan datos de hallazgo
    # Típicamente: Nro | Hallazgo | Propuesta | Área | Riesgo
    header_idx = None
    col_hallazgo = None
    col_propuesta = None
    col_area = None
    col_riesgo = None

    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        norm_cols = [normalize_text(c) for c in cols]

        # Detectar header
        if header_idx is None:
            for j, nc in enumerate(norm_cols):
                if any(kw in nc for kw in ["hallazgo", "observacion", "desviacion", "descripcion", "situacion"]):
                    col_hallazgo = j
                if any(kw in nc for kw in ["propuesta", "recomendacion", "mejora", "accion"]):
                    col_propuesta = j
                if any(kw in nc for kw in ["area", "proceso", "sector", "gerencia"]):
                    col_area = j
                if any(kw in nc for kw in ["riesgo", "severidad", "criticidad", "impacto"]):
                    col_riesgo = j
            if col_hallazgo is not None:
                header_idx = i
                continue

        # Leer filas de datos
        if header_idx is not None and col_hallazgo is not None:
            if len(cols) > col_hallazgo:
                hallazgo_text = cols[col_hallazgo]
                if len(hallazgo_text) < 5:
                    continue

                proposal_text = ""
                if col_propuesta is not None and len(cols) > col_propuesta:
                    proposal_text = cols[col_propuesta]

                area = default_area
                if col_area is not None and len(cols) > col_area:
                    area = cols[col_area] or default_area

                severity = "Medio"
                if col_riesgo is not None and len(cols) > col_riesgo:
                    severity = _detect_severity(cols[col_riesgo])

                findings.append({
                    "title": hallazgo_text[:120],
                    "situation": hallazgo_text,
                    "proposal": proposal_text,
                    "severity": severity,
                    "responsible_area": area,
                })

    return findings


# ──────────────────────────────────────────────────────────────
#  FUNCIÓN PRINCIPAL
# ──────────────────────────────────────────────────────────────

def parse_audit_report(file_path, filename):
    """Punto de entrada principal. Parsea el archivo y devuelve estructura relacional."""
    raw_text = extract_raw_text_from_file(file_path)

    if not raw_text.strip():
        print(f"[Parser] ERROR: No se pudo extraer texto de '{filename}'")
        return {
            "report": {"title": f"Error - {filename}", "process": "Control Interno",
                        "area": "Operaciones", "period": "2026",
                        "auditor": "Auditoría Interna", "summary": "No se pudo leer el archivo."},
            "findings": []
        }

    parsed = parse_document(raw_text, filename)
    extracted_findings = parsed.get("findings", [])
    report_title = parsed.get("report_title", f"Informe de Auditoría - {filename}")
    explicit_area = parsed.get("area", "Operaciones")
    explicit_auditor = parsed.get("auditor", "Auditoría Interna")

    # Formatear hallazgos en la estructura relacional de AuditTrack
    relational_findings = []
    for idx, f in enumerate(extracted_findings, start=1):
        h_code = f"H-2026-{idx:03d}"
        pm_code = f"PM-2026-{idx:03d}"
        pa_code = f"PA-2026-{idx:03d}"

        title = clean_text(f.get("title", f"Hallazgo {idx}"))
        situation = clean_text(f.get("situation", title))
        proposal = clean_text(f.get("proposal", ""))
        if not proposal:
            proposal = f"Implementar medidas de control y mejora para: {title[:60]}."
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
