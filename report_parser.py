import os
import re
import csv
import io
import uuid
import hashlib
import unicodedata
from typing import List, Optional

from openpyxl import load_workbook
from docx import Document
from docx.document import Document as _Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from pypdf import PdfReader
from openai import OpenAI
from pydantic import BaseModel, Field


AUDIT_MODEL = os.getenv("OPENAI_AUDIT_MODEL", "gpt-4o")
MAX_SOURCE_CHARS = int(os.getenv("OPENAI_AUDIT_MAX_CHARS", "100000"))


# ============================================================
# MODELOS IA
# ============================================================

class AIEnrichedFinding(BaseModel):
    finding_number: int
    title: str = ""
    situation: str = ""
    evidence: str = ""
    affected_process_or_control: str = ""
    cause: Optional[str] = None
    risk: str = ""
    impact: Optional[str] = None
    severity: str = "Medio"
    responsible_area: Optional[str] = None
    confidence: float = 0.0


class AIProposalMapping(BaseModel):
    proposal_number: int
    finding_numbers: List[int] = Field(default_factory=list)
    confidence: float = 0.0


class AIReportAnalysis(BaseModel):
    findings: List[AIEnrichedFinding] = Field(default_factory=list)
    proposal_mappings: List[AIProposalMapping] = Field(default_factory=list)


# ============================================================
# UTILIDADES
# ============================================================

def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_text(value):
    text = clean_text(value)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text.lower()).strip()


def clip_text(value, limit):
    text = clean_text(value)
    if len(text) <= limit:
        return text
    cut = text[:limit].rfind(" ")
    if cut < int(limit * 0.6):
        cut = limit
    return text[:cut].rstrip() + "…"


def strip_marker(value):
    text = clean_text(value)
    return re.sub(
        r"^\[(?:HEADING|BOLD|TABLE_ROW|PAGE|SHEET)\](?:\s+\d+)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def safe_severity(value):
    norm = normalize_text(value)
    if norm in {"alto", "alta", "critico", "critica"}:
        return "Alto"
    if norm in {"bajo", "baja", "leve", "menor"}:
        return "Bajo"
    return "Medio"


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[IA] OPENAI_API_KEY no configurada.")
        return None
    return OpenAI(api_key=api_key)


# ============================================================
# EXTRACCIÓN DE ARCHIVOS
# ============================================================

def iter_docx_blocks(document):
    if not isinstance(document, _Document):
        return

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def paragraph_is_bold(paragraph):
    runs = [r for r in paragraph.runs if clean_text(r.text)]
    if not runs:
        return False

    total = sum(len(clean_text(r.text)) for r in runs)
    bold = sum(len(clean_text(r.text)) for r in runs if r.bold is True)
    return total > 0 and (bold / total) >= 0.6


def extract_docx(file_path):
    doc = Document(file_path)
    lines = []

    for block in iter_docx_blocks(doc):
        if isinstance(block, Paragraph):
            text = clean_text(block.text)
            if not text:
                continue

            style = normalize_text(block.style.name if block.style else "")

            if style.startswith(("heading", "titulo", "title")):
                lines.append(f"[HEADING] {text}")
            elif len(text) <= 250 and paragraph_is_bold(block):
                lines.append(f"[BOLD] {text}")
            else:
                lines.append(text)

        elif isinstance(block, Table):
            for row in block.rows:
                cells = [clean_text(c.text) for c in row.cells if clean_text(c.text)]
                if cells:
                    lines.append("[TABLE_ROW] " + " || ".join(cells))

    return "\n".join(lines)


def extract_pdf(file_path):
    reader = PdfReader(file_path)
    pages = []

    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[PAGE] {number}\n{text}")

    if len(reader.pages) and not pages:
        raise ValueError(
            "El PDF no contiene texto seleccionable o es una imagen escaneada."
        )

    return "\n".join(pages)


def extract_xlsx(file_path):
    workbook = load_workbook(file_path, data_only=True)
    lines = []

    for sheet_name in workbook.sheetnames[:10]:
        lines.append(f"[SHEET] {sheet_name}")
        ws = workbook[sheet_name]

        for row_number, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if row_number > 2500:
                break

            cells = [clean_text(v) for v in row if clean_text(v)]
            if cells:
                lines.append("[TABLE_ROW] " + " || ".join(cells))

    return "\n".join(lines)


def extract_csv(file_path):
    with open(file_path, "rb") as handle:
        raw = handle.read()

    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="ignore")

    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";" if ";" in sample else ","

    rows = []
    for row in csv.reader(io.StringIO(content), delimiter=delimiter):
        cells = [clean_text(c) for c in row if clean_text(c)]
        if cells:
            rows.append("[TABLE_ROW] " + " || ".join(cells))

    return "\n".join(rows)


def extract_raw_text_from_file(file_path):
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

    if ext == "docx":
        text = extract_docx(file_path)
    elif ext == "pdf":
        text = extract_pdf(file_path)
    elif ext == "xlsx":
        text = extract_xlsx(file_path)
    elif ext == "csv":
        text = extract_csv(file_path)
    elif ext == "txt":
        with open(file_path, "rb") as handle:
            raw = handle.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="ignore")
    elif ext == "doc":
        raise ValueError(
            "Los archivos .doc antiguos no se interpretan de forma confiable. "
            "Guardalo como .docx y volvé a subirlo."
        )
    else:
        raise ValueError(f"Formato no soportado: {ext}")

    text = (text or "").strip()
    if not text:
        raise ValueError("El archivo no contiene texto interpretable o está vacío.")

    return text[:MAX_SOURCE_CHARS]


# ============================================================
# METADATOS DEL INFORME
# ============================================================

def extract_report_title(raw_text, filename):
    for raw_line in raw_text.splitlines()[:50]:
        line = strip_marker(raw_line)
        norm = normalize_text(line)

        if "informe" in norm and "auditoria" in norm and len(line) < 180:
            return line

    return f"Informe de Auditoría - {filename}"


def extract_explicit_area(raw_text):
    patterns = [
        r"(?:área auditada|area auditada)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:proceso auditado)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:gerencia auditada)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:unidad auditada)\s*[:\-]\s*([^\n\r\|]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            value = clean_text(match.group(1))
            if 2 < len(value) < 100:
                return value

    return ""


def extract_explicit_auditor(raw_text):
    pattern = (
        r"(?:auditor responsable|auditor líder|auditor lider|"
        r"elaborado por|realizado por)\s*[:\-]\s*([^\n\r\|]+)"
    )
    match = re.search(pattern, raw_text, flags=re.IGNORECASE)

    if match:
        value = clean_text(match.group(1))
        if 2 < len(value) < 100:
            return value

    return ""


# ============================================================
# SECCIONES PRINCIPALES
# ============================================================

def section_kind(raw_line):
    text = strip_marker(raw_line)

    # Evita agarrar entradas del índice con puntos de relleno.
    if not text or "...." in text:
        return ""

    norm = normalize_text(text)
    norm = re.sub(r"^\d+(?:\.\d+)*[\.\)]?\s*", "", norm).strip(" .:-")

    if norm in {"hallazgo", "hallazgos", "observaciones de auditoria"}:
        return "findings"

    if norm in {
        "propuesta de mejora",
        "propuestas de mejora",
        "recomendacion",
        "recomendaciones",
        "recomendaciones de auditoria",
    }:
        return "proposals"

    if norm in {"conclusion", "conclusiones"}:
        return "conclusion"

    return ""


def find_sections(lines):
    findings_idx = None
    proposals_idx = None
    conclusion_idx = None

    for idx, line in enumerate(lines):
        kind = section_kind(line)

        if kind == "findings" and findings_idx is None:
            findings_idx = idx
        elif (
            kind == "proposals"
            and findings_idx is not None
            and proposals_idx is None
            and idx > findings_idx
        ):
            proposals_idx = idx
        elif (
            kind == "conclusion"
            and proposals_idx is not None
            and conclusion_idx is None
            and idx > proposals_idx
        ):
            conclusion_idx = idx
            break

    return findings_idx, proposals_idx, conclusion_idx


# ============================================================
# HALLAZGOS
# ============================================================

FINDING_RE = re.compile(
    r"^(?:\[(?:HEADING|BOLD)\]\s*)?"
    r"hallazgo\s+(\d+)\s*[:\-\.\)]?\s*(.*)$",
    flags=re.IGNORECASE,
)

RISK_RE = re.compile(
    r"(?:▌\s*)?riesgo\s+(alto|medio|bajo)",
    flags=re.IGNORECASE,
)


def is_noise(line):
    norm = normalize_text(strip_marker(line))

    if not norm:
        return True

    if "confidencial" in norm and "uso interno" in norm:
        return True

    if "vital" in norm and "auditoria interna" in norm and len(norm) < 120:
        return True

    return False


def parse_findings(lines, findings_idx, proposals_idx):
    start = findings_idx + 1 if findings_idx is not None else 0
    end = proposals_idx if proposals_idx is not None else len(lines)

    findings = []
    current = None
    pending_risk = "Medio"

    for raw_line in lines[start:end]:
        if is_noise(raw_line):
            continue

        line = strip_marker(raw_line)

        risk_match = RISK_RE.search(line)
        if risk_match and len(line) < 60:
            pending_risk = risk_match.group(1).capitalize()
            continue

        match = FINDING_RE.match(raw_line) or FINDING_RE.match(line)

        if match:
            if current:
                findings.append(current)

            number = int(match.group(1))
            title = clean_text(match.group(2)) or f"Hallazgo {number}"

            current = {
                "number": number,
                "title": title,
                "risk_heading": pending_risk,
                "lines": [],
            }
            pending_risk = "Medio"
            continue

        if current:
            current["lines"].append(raw_line)

    if current:
        findings.append(current)

    # Si por un header repetido aparece el mismo número dos veces,
    # conserva el bloque más largo.
    unique = {}
    for finding in findings:
        n = finding["number"]
        if n not in unique or len(finding["lines"]) > len(unique[n]["lines"]):
            unique[n] = finding

    return [unique[n] for n in sorted(unique)]


LABELS = [
    ("analysis", re.compile(
        r"^(?:an[aá]lisis realizado(?:\s+con[^:]*)?|an[aá]lisis)\s*:\s*(.*)$",
        re.IGNORECASE,
    )),
    ("summary", re.compile(
        r"^resumen del hallazgo\s*:\s*(.*)$",
        re.IGNORECASE,
    )),
    ("evidence", re.compile(
        r"^evidencia\s*:\s*(.*)$",
        re.IGNORECASE,
    )),
    ("conclusion", re.compile(
        r"^conclusi[oó]n\s*:?\s*(.*)$",
        re.IGNORECASE,
    )),
    ("impact", re.compile(
        r"^impacto(?:\s+econ[oó]mico)?\s*:\s*(.*)$",
        re.IGNORECASE,
    )),
    ("context", re.compile(
        r"^aclaraci[oó]n(?:\s+de|\s+del)?\s+alcance\s*:\s*(.*)$",
        re.IGNORECASE,
    )),
]


def split_finding(finding):
    sections = {
        "context": [],
        "summary": [],
        "analysis": [],
        "evidence": [],
        "conclusion": [],
        "impact": [],
        "other": [],
        "tables": [],
    }

    current_section = "other"

    for raw_line in finding["lines"]:
        if raw_line.startswith("[TABLE_ROW]"):
            table = clean_text(raw_line.replace("[TABLE_ROW]", "", 1))
            if table:
                sections["tables"].append(table)
            continue

        line = strip_marker(raw_line)
        if not line or is_noise(line):
            continue

        matched = False

        for section_name, pattern in LABELS:
            match = pattern.match(line)
            if match:
                current_section = section_name
                rest = clean_text(match.group(1))
                if rest:
                    sections[section_name].append(rest)
                matched = True
                break

        if not matched:
            sections[current_section].append(line)

    def joined(name, limit):
        return clip_text(" ".join(sections[name]), limit)

    summary = joined("summary", 1200)
    analysis = joined("analysis", 1600)
    conclusion = joined("conclusion", 1200)
    context = joined("context", 900)
    other = joined("other", 1200)
    evidence = joined("evidence", 1500)
    impact = joined("impact", 700)

    situation_parts = [x for x in [summary, analysis, conclusion] if x]
    if not situation_parts:
        situation_parts = [x for x in [context, other, conclusion] if x]

    return {
        "number": finding["number"],
        "title": finding["title"],
        "risk_heading": finding["risk_heading"],
        "situation": clip_text(" ".join(situation_parts), 1800) or finding["title"],
        "context": context,
        "summary": summary,
        "analysis": analysis,
        "evidence": evidence,
        "conclusion": conclusion,
        "impact": impact,
        "other": other,
        "table_evidence": sections["tables"][:30],
    }


# ============================================================
# PROPUESTAS DE MEJORA
# ============================================================

PROPOSAL_RE = re.compile(
    r"^(?:\[(?:HEADING|BOLD)\]\s*)?(\d+)[\.\)]\s+(.+)$"
)

HALLAZGO_REF_RE = re.compile(
    r"\bhallazgos?\s+((?:\d+\s*(?:,|y|e)?\s*)+)",
    re.IGNORECASE,
)


def extract_hallazgo_refs(text):
    numbers = []

    for match in HALLAZGO_REF_RE.finditer(text):
        numbers.extend(int(n) for n in re.findall(r"\d+", match.group(1)))

    return sorted(set(numbers))


def remove_hallazgo_refs(text):
    return clean_text(
        re.sub(
            r"\bhallazgos?\s+\d+(?:\s*(?:,|y|e)\s*\d+)*\.?",
            "",
            text,
            flags=re.IGNORECASE,
        )
    )


def parse_proposals(lines, proposals_idx, conclusion_idx):
    if proposals_idx is None:
        return []

    start = proposals_idx + 1
    end = conclusion_idx if conclusion_idx is not None else len(lines)

    blocks = []
    current = None

    for raw_line in lines[start:end]:
        if is_noise(raw_line):
            continue

        line = strip_marker(raw_line)
        match = PROPOSAL_RE.match(raw_line) or PROPOSAL_RE.match(line)

        if match:
            if current:
                blocks.append(current)

            current = {
                "number": int(match.group(1)),
                "lines": [clean_text(match.group(2))],
            }
            continue

        if current:
            current["lines"].append(line)

    if current:
        blocks.append(current)

    proposals = []

    for block in blocks:
        full_text = clean_text(" ".join(block["lines"]))
        refs = extract_hallazgo_refs(full_text)
        proposal_text = remove_hallazgo_refs(full_text)

        if proposal_text:
            proposals.append({
                "number": block["number"],
                "proposal_text": proposal_text,
                "explicit_finding_numbers": refs,
                "finding_numbers": list(refs),
            })

    return proposals


# ============================================================
# IA
# ============================================================

AI_SYSTEM_PROMPT = """
Actuás como Auditor Interno Senior.

La estructura del informe YA fue separada por código.
No podés crear, eliminar, dividir, fusionar ni renumerar hallazgos.
No podés inventar propuestas de mejora.

Recibirás hallazgos ya delimitados y propuestas ya extraídas.

PARA CADA HALLAZGO:
- mantené exactamente el mismo finding_number;
- redactá un título breve, profesional y fiel;
- redactá situation como una síntesis del verdadero hallazgo;
- NO copies tablas ni encabezados de tablas;
- usá los datos tabulares solo cuando aporten evidencia relevante;
- distinguí situación, evidencia, causa, riesgo e impacto;
- cause=null si la causa no está respaldada;
- impact=null si no está respaldado;
- no inventes hechos, cifras, fechas, sistemas, responsables ni normativa;
- severity solo Alto, Medio o Bajo;
- responsible_area solo si surge del contenido;
- confidence entre 0 y 1.

La situación debe INTERPRETAR el hallazgo, no copiar todas las líneas.

PROPUESTAS:
Las referencias explícitas "Hallazgo X" / "Hallazgos X y Y" ya están resueltas.
No las modifiques.

Solo para propuestas cuyo explicit_finding_numbers sea []:
- indicá a qué finding_numbers corresponden semánticamente;
- puede ser uno o varios;
- no cambies el texto;
- devolvé [] si no hay sustento suficiente.
"""


def analyze_with_ai(findings, proposals):
    client = get_openai_client()
    if not client:
        return None

    findings_payload = [
        {
            "finding_number": f["number"],
            "original_title": f["title"],
            "risk_heading": f["risk_heading"],
            "context": f["context"],
            "summary": f["summary"],
            "analysis": f["analysis"],
            "evidence": f["evidence"],
            "conclusion": f["conclusion"],
            "impact": f["impact"],
            "other_narrative": f["other"],
            "table_evidence": f["table_evidence"][:20],
        }
        for f in findings
    ]

    proposals_payload = [
        {
            "proposal_number": p["number"],
            "proposal_text": p["proposal_text"],
            "explicit_finding_numbers": p["explicit_finding_numbers"],
        }
        for p in proposals
    ]

    prompt = f"""
HALLAZGOS DEL INFORME:
{findings_payload}

PROPUESTAS DEL INFORME:
{proposals_payload}

No cambies la cantidad de hallazgos ni sus números.
No generes propuestas nuevas.
"""

    try:
        response = client.beta.chat.completions.parse(
            model=AUDIT_MODEL,
            messages=[
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format=AIReportAnalysis,
        )
        return response.choices[0].message.parsed

    except Exception as exc:
        print(f"[IA] Error: {exc}")
        return None


def apply_ai(findings, proposals, ai_result):
    by_number = {f["number"]: f for f in findings}
    valid_numbers = set(by_number)

    if ai_result:
        for result in ai_result.findings:
            if result.finding_number not in by_number:
                continue

            f = by_number[result.finding_number]

            if clean_text(result.title):
                f["title"] = clip_text(result.title, 180)

            if clean_text(result.situation):
                f["situation"] = clip_text(result.situation, 1800)

            f["evidence_ai"] = clip_text(result.evidence, 1400)
            f["affected_process_or_control"] = clip_text(
                result.affected_process_or_control,
                600,
            )
            f["cause"] = clip_text(result.cause, 600) if result.cause else ""
            f["risk"] = clip_text(result.risk, 900)
            f["impact_ai"] = clip_text(result.impact, 700) if result.impact else ""
            f["severity"] = safe_severity(result.severity or f["risk_heading"])
            f["responsible_area_ai"] = clean_text(result.responsible_area)
            f["ai_confidence"] = max(0.0, min(1.0, float(result.confidence or 0)))
            f["ai_validated"] = True

        mappings = {m.proposal_number: m for m in ai_result.proposal_mappings}

        for proposal in proposals:
            if proposal["explicit_finding_numbers"]:
                continue

            mapping = mappings.get(proposal["number"])

            if not mapping or float(mapping.confidence or 0) < 0.55:
                continue

            proposal["finding_numbers"] = sorted({
                n for n in mapping.finding_numbers if n in valid_numbers
            })

    for f in findings:
        f.setdefault("severity", safe_severity(f["risk_heading"]))
        f.setdefault("risk", "")
        f.setdefault("evidence_ai", f["evidence"])
        f.setdefault("impact_ai", f["impact"])
        f.setdefault("cause", "")
        f.setdefault("affected_process_or_control", "")
        f.setdefault("responsible_area_ai", "")
        f.setdefault("ai_confidence", 0.0)
        f.setdefault("ai_validated", False)

    return findings, proposals


# ============================================================
# SALIDA PARA AUDITTRACK
# ============================================================

def parse_audit_report(file_path, filename):
    raw_text = extract_raw_text_from_file(file_path)

    lines = [
        clean_text(line)
        for line in raw_text.splitlines()
        if clean_text(line)
    ]

    findings_idx, proposals_idx, conclusion_idx = find_sections(lines)

    raw_findings = parse_findings(
        lines,
        findings_idx,
        proposals_idx,
    )

    if not raw_findings:
        raise ValueError(
            "No se encontraron hallazgos explícitos del tipo "
            "'Hallazgo 1:', 'Hallazgo 2:', etc. "
            "AuditTrack no generó hallazgos a partir de párrafos sueltos."
        )

    findings = [split_finding(f) for f in raw_findings]

    proposals = parse_proposals(
        lines,
        proposals_idx,
        conclusion_idx,
    )

    ai_result = analyze_with_ai(findings, proposals)
    findings, proposals = apply_ai(findings, proposals, ai_result)

    proposals_by_finding = {f["number"]: [] for f in findings}

    for proposal in proposals:
        for finding_number in proposal["finding_numbers"]:
            if finding_number in proposals_by_finding:
                proposals_by_finding[finding_number].append(proposal)

    report_title = extract_report_title(raw_text, filename)
    report_area = extract_explicit_area(raw_text) or "Pendiente de definir"
    auditor = extract_explicit_auditor(raw_text) or "Auditoría Interna"

    relational_findings = []
    proposal_counter = 1

    for display_index, finding in enumerate(findings, start=1):
        finding_id = str(uuid.uuid4())

        source_key = hashlib.md5(
            f"{filename}_{finding['number']}_{finding['title']}".encode("utf-8")
        ).hexdigest()[:12]

        area = clean_text(finding.get("responsible_area_ai")) or report_area
        linked_proposals = []

        for proposal in proposals_by_finding.get(finding["number"], []):
            proposal_text = clean_text(proposal["proposal_text"])

            if not proposal_text:
                continue

            linked_proposals.append({
                "id": str(uuid.uuid4()),
                "finding_id": finding_id,
                "code": f"PM-2026-{proposal_counter:03d}",
                "title": proposal_text,
                "proposal_text": proposal_text,
                "severity": finding["severity"],
                "responsible_area": area,
                "action_owner": "Pendiente de definir",
                "target_date": "",
                "status": "Pendiente",
                "action_plans": [],
            })

            proposal_counter += 1

        evidence = (
            clean_text(finding.get("evidence_ai"))
            or clean_text(finding.get("evidence"))
        )

        impact = (
            clean_text(finding.get("impact_ai"))
            or clean_text(finding.get("impact"))
        )

        relational_findings.append({
            "id": finding_id,
            "sourceItemId": f"src_{source_key}",
            "sourceItemIds": [f"src_{source_key}"],
            "sourceKey": source_key,
            "sourceFile": filename,
            "sourceLocation": f"Hallazgo {finding['number']}",
            "evidence": evidence,
            "included": True,
            "selectedAsFinding": True,
            "converted": False,
            "code": f"H-2026-{display_index:03d}",
            "title": clean_text(finding["title"]),
            "situation": clean_text(finding["situation"]),
            "risk": clean_text(finding.get("risk")),
            "severity": finding["severity"],
            "responsible_area": area,
            "action_owner": "Pendiente de definir",
            "status": "Pendiente",
            "observations": "",
            "proposals": linked_proposals,
            "cause": clean_text(finding.get("cause")),
            "affected_process_or_control": clean_text(
                finding.get("affected_process_or_control")
            ),
            "impact": impact,
            "ai_confidence": float(finding.get("ai_confidence", 0)),
            "ai_validated": bool(finding.get("ai_validated", False)),
            "ai_status": (
                "Estructura documental + IA"
                if finding.get("ai_validated")
                else "Estructura documental"
            ),
        })

    unmatched = [
        p["number"]
        for p in proposals
        if not p["finding_numbers"]
    ]

    print(
        f"[Parser] {filename}: "
        f"{len(relational_findings)} hallazgos, "
        f"{len(proposals)} propuestas, "
        f"{len(unmatched)} propuestas sin relación."
    )

    return {
        "report": {
            "title": report_title,
            "process": report_area,
            "area": report_area,
            "period": "2026",
            "auditor": auditor,
            "summary": (
                f"Informe {filename} procesado con "
                f"{len(relational_findings)} hallazgos y "
                f"{len(proposals)} propuestas de mejora."
            ),
            "source_filename": filename,
        },
        "findings": relational_findings,
    }
