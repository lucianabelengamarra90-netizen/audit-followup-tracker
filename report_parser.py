import os
import re
import csv
import io
import uuid
import hashlib
import zipfile
import unicodedata
import xml.etree.ElementTree as ET
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


# ============================================================
# CONFIGURACIÓN
# ============================================================

MAX_SOURCE_CHARS = int(
    os.getenv("OPENAI_AUDIT_MAX_CHARS", "100000")
)

DEFAULT_MODEL = os.getenv(
    "OPENAI_AUDIT_MODEL",
    "gpt-4o"
)

REVIEW_MODEL = os.getenv(
    "OPENAI_AUDIT_REVIEW_MODEL",
    DEFAULT_MODEL
)


# ============================================================
# MODELOS ESTRUCTURADOS
# ============================================================

class AuditProposal(BaseModel):
    proposal_text: str = ""
    source_supported: bool = True


class AuditDocumentFinding(BaseModel):
    original_title: str = ""
    title: str = ""
    situation: str = ""
    evidence: str = ""
    affected_process_or_control: str = ""
    cause: Optional[str] = None
    risk: str = ""
    impact: Optional[str] = None
    severity: str = "Medio"
    responsible_area: Optional[str] = None
    proposals: List[AuditProposal] = Field(default_factory=list)
    source_excerpt: str = ""
    confidence: float = 0.0


class AuditDocumentExtraction(BaseModel):
    report_title: Optional[str] = None
    process: Optional[str] = None
    area: Optional[str] = None
    auditor: Optional[str] = None
    declared_finding_count: int = 0
    findings: List[AuditDocumentFinding] = Field(default_factory=list)


class AuditDocumentReview(BaseModel):
    approved: bool = False
    score: float = 0.0
    issues: List[str] = Field(default_factory=list)
    corrected_result: Optional[AuditDocumentExtraction] = None


# ============================================================
# UTILIDADES GENERALES
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    text = str(value).strip()

    if not text:
        return ""

    text = unicodedata.normalize("NFD", text)

    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) != "Mn"
    )

    text = re.sub(
        r"\s+",
        " ",
        text.lower()
    )

    return text.strip()


def clean_text(value):
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def _clip_text(text, max_chars=1000):
    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    cut = text[:max_chars].rfind(" ")

    if cut < int(max_chars * 0.65):
        cut = max_chars

    return text[:cut].rstrip() + "…"


def _safe_severity(value):
    norm = normalize_text(value)

    if norm in (
        "alto",
        "alta",
        "critico",
        "critica"
    ):
        return "Alto"

    if norm in (
        "bajo",
        "baja",
        "leve",
        "menor"
    ):
        return "Bajo"

    return "Medio"


def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print(
            "[IA] OPENAI_API_KEY no configurada. "
            "No se generarán hallazgos por bloques arbitrarios."
        )
        return None

    return OpenAI(
        api_key=api_key
    )


# ============================================================
# METADATOS EXPLÍCITOS DEL INFORME
# ============================================================

def extract_explicit_area(raw_text):
    if not raw_text:
        return ""

    patterns = [
        r"(?:área auditada|area auditada)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:proceso auditado)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:sector auditado)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:gerencia auditada)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:departamento auditado)\s*[:\-]\s*([^\n\r\|]+)",
        r"(?:unidad auditada)\s*[:\-]\s*([^\n\r\|]+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            raw_text,
            re.IGNORECASE
        )

        if match:
            result = clean_text(
                match.group(1)
            )

            if 2 < len(result) < 100:
                return result

    return ""


def extract_explicit_auditor(raw_text):
    if not raw_text:
        return ""

    pattern = (
        r"(?:auditor responsable|auditor líder|auditor lider|"
        r"auditor encargado|elaborado por|realizado por)"
        r"\s*[:\-]\s*([^\n\r\|]+)"
    )

    match = re.search(
        pattern,
        raw_text,
        re.IGNORECASE
    )

    if match:
        result = clean_text(
            match.group(1)
        )

        if 2 < len(result) < 100:
            return result

    return ""


def extract_report_title(
    raw_text,
    filename
):
    lines = [
        clean_text(line)
        for line in raw_text.splitlines()
        if clean_text(line)
    ]

    for line in lines[:30]:
        norm = normalize_text(line)

        if (
            "informe" in norm
            and "auditoria" in norm
            and len(line) < 180
        ):
            line = re.sub(
                r"^\[(?:HEADING|BOLD|HEADER)\]\s*",
                "",
                line,
                flags=re.IGNORECASE
            )

            return clean_text(line)

    return (
        f"Informe de Auditoría - {filename}"
    )


# ============================================================
# EXTRACCIÓN DOCX RESPETANDO ESTRUCTURA
# ============================================================

def _iter_docx_blocks(parent):
    if not isinstance(
        parent,
        _Document
    ):
        return

    parent_element = (
        parent.element.body
    )

    for child in parent_element.iterchildren():

        if isinstance(
            child,
            CT_P
        ):
            yield Paragraph(
                child,
                parent
            )

        elif isinstance(
            child,
            CT_Tbl
        ):
            yield Table(
                child,
                parent
            )


def _paragraph_is_mostly_bold(
    paragraph
):
    visible_runs = [
        run
        for run in paragraph.runs
        if clean_text(run.text)
    ]

    if not visible_runs:
        return False

    total_chars = sum(
        len(clean_text(run.text))
        for run in visible_runs
    )

    if total_chars <= 0:
        return False

    bold_chars = sum(
        len(clean_text(run.text))
        for run in visible_runs
        if run.bold is True
    )

    return (
        bold_chars / total_chars
    ) >= 0.60


def _format_docx_paragraph(
    paragraph
):
    text = clean_text(
        paragraph.text
    )

    if not text:
        return ""

    style_name = normalize_text(
        paragraph.style.name
        if paragraph.style
        else ""
    )

    looks_heading = (
        style_name.startswith("heading")
        or style_name.startswith("titulo")
        or style_name.startswith("title")
    )

    if looks_heading:
        return (
            f"[HEADING] {text}"
        )

    if (
        len(text) <= 220
        and _paragraph_is_mostly_bold(
            paragraph
        )
    ):
        return (
            f"[BOLD] {text}"
        )

    return text


def extract_docx_xml_deep(
    file_path
):
    text_parts = []

    try:
        with zipfile.ZipFile(
            file_path,
            "r"
        ) as archive:

            for name in archive.namelist():

                if not (
                    name.startswith("word/")
                    and name.endswith(".xml")
                ):
                    continue

                try:
                    xml_bytes = archive.read(
                        name
                    )

                    root = ET.fromstring(
                        xml_bytes
                    )

                    current = []

                    for elem in root.iter():

                        if (
                            elem.tag.endswith("}t")
                            and elem.text
                        ):
                            current.append(
                                elem.text
                            )

                        elif (
                            elem.tag.endswith("}p")
                            or elem.tag.endswith("}tr")
                        ):

                            if current:
                                text_parts.append(
                                    clean_text(
                                        " ".join(
                                            current
                                        )
                                    )
                                )

                                current = []

                    if current:
                        text_parts.append(
                            clean_text(
                                " ".join(
                                    current
                                )
                            )
                        )

                except Exception:
                    continue

    except Exception as exc:
        print(
            "[Parser] Error en extracción XML profunda:",
            exc
        )

    return "\n".join(
        part
        for part in text_parts
        if part
    )


def _extract_docx_text(
    file_path
):
    try:
        doc = Document(
            file_path
        )

        parts = []

        # Encabezados del Word.
        for section in doc.sections:
            try:
                header_lines = [
                    clean_text(p.text)
                    for p
                    in section.header.paragraphs
                    if clean_text(p.text)
                ]

                if header_lines:
                    parts.append(
                        "[HEADER] "
                        + " | ".join(
                            header_lines
                        )
                    )

            except Exception:
                pass

        # Cuerpo en el orden real del documento.
        for block in _iter_docx_blocks(
            doc
        ):

            if isinstance(
                block,
                Paragraph
            ):
                formatted = (
                    _format_docx_paragraph(
                        block
                    )
                )

                if formatted:
                    parts.append(
                        formatted
                    )

            elif isinstance(
                block,
                Table
            ):

                for row in block.rows:

                    cells = [
                        clean_text(
                            cell.text
                        )
                        for cell in row.cells
                        if clean_text(
                            cell.text
                        )
                    ]

                    if cells:
                        parts.append(
                            "[TABLE_ROW] "
                            + " || ".join(
                                cells
                            )
                        )

        text_content = "\n".join(
            parts
        ).strip()

        if text_content:
            return text_content

    except Exception as exc:
        print(
            "[Parser] python-docx no pudo leer el archivo:",
            exc
        )

    return extract_docx_xml_deep(
        file_path
    )


# ============================================================
# EXTRACCIÓN DE TEXTO POR TIPO DE ARCHIVO
# ============================================================

def extract_raw_text_from_file(
    file_path
):
    ext = (
        file_path.rsplit(
            ".",
            1
        )[-1].lower()
        if "." in file_path
        else ""
    )

    text_content = ""

    # --------------------------------------------------------
    # WORD DOCX
    # --------------------------------------------------------

    if ext == "docx":

        text_content = (
            _extract_docx_text(
                file_path
            )
        )

    # --------------------------------------------------------
    # WORD DOC ANTIGUO
    # --------------------------------------------------------

    elif ext == "doc":

        try:
            with open(
                file_path,
                "rb"
            ) as raw_file:

                raw_bytes = (
                    raw_file.read()
                )

            decoded = (
                raw_bytes.decode(
                    "latin-1",
                    errors="ignore"
                )
            )

            printable = re.findall(
                r"[A-Za-z0-9ÁÉÍÓÚáéíóúÑñ"
                r"\s\.,;:!\?\-\(\)\$/%]{4,}",
                decoded
            )

            text_content = "\n".join(
                clean_text(part)
                for part in printable
                if len(
                    clean_text(part)
                ) > 5
            )

        except Exception as exc:
            raise ValueError(
                "No se pudo procesar "
                "el archivo DOC: "
                + str(exc)
            )

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    elif ext == "pdf":

        try:
            reader = PdfReader(
                file_path
            )

            pages = []

            for index, page in enumerate(
                reader.pages,
                start=1
            ):

                text = (
                    page.extract_text()
                    or ""
                ).strip()

                if text:
                    pages.append(
                        f"[PAGE {index}]\n{text}"
                    )

            text_content = "\n".join(
                pages
            )

            if (
                len(reader.pages) > 0
                and not text_content.strip()
            ):
                raise ValueError(
                    "El PDF no contiene texto "
                    "seleccionable o es una imagen "
                    "escaneada (requiere OCR)."
                )

        except ValueError:
            raise

        except Exception as exc:
            raise ValueError(
                "No se pudo procesar el PDF: "
                + str(exc)
            )

    # --------------------------------------------------------
    # EXCEL
    # --------------------------------------------------------

    elif ext == "xlsx":

        try:
            workbook = load_workbook(
                file_path,
                data_only=True
            )

            lines = []

            for sheet_name in (
                workbook.sheetnames[:10]
            ):

                worksheet = workbook[
                    sheet_name
                ]

                lines.append(
                    f"[SHEET] {sheet_name}"
                )

                for row_index, row in enumerate(
                    worksheet.iter_rows(
                        values_only=True
                    ),
                    start=1
                ):

                    if row_index > 2500:
                        break

                    values = [
                        clean_text(value)
                        for value in row
                        if clean_text(value)
                    ]

                    if values:
                        lines.append(
                            "[TABLE_ROW] "
                            + " || ".join(
                                values
                            )
                        )

            text_content = "\n".join(
                lines
            )

        except Exception as exc:
            raise ValueError(
                "No se pudo procesar "
                "la planilla Excel: "
                + str(exc)
            )

    # --------------------------------------------------------
    # CSV
    # --------------------------------------------------------

    elif ext == "csv":

        try:
            with open(
                file_path,
                "rb"
            ) as raw_file:

                raw_bytes = (
                    raw_file.read()
                )

            try:
                content = (
                    raw_bytes.decode(
                        "utf-8-sig"
                    )
                )

            except UnicodeDecodeError:

                content = (
                    raw_bytes.decode(
                        "latin-1",
                        errors="ignore"
                    )
                )

            sample = content[:4096]

            try:
                dialect = (
                    csv.Sniffer().sniff(
                        sample,
                        delimiters=[
                            ",",
                            ";",
                            "\t",
                            "|"
                        ]
                    )
                )

                delimiter = (
                    dialect.delimiter
                )

            except Exception:

                delimiter = (
                    ";"
                    if ";" in sample
                    else ","
                )

            reader = csv.reader(
                io.StringIO(
                    content
                ),
                delimiter=delimiter
            )

            rows = []

            for row in reader:

                cells = [
                    clean_text(cell)
                    for cell in row
                    if clean_text(cell)
                ]

                if cells:
                    rows.append(
                        "[TABLE_ROW] "
                        + " || ".join(
                            cells
                        )
                    )

            text_content = "\n".join(
                rows
            )

        except Exception as exc:
            raise ValueError(
                "No se pudo procesar el CSV: "
                + str(exc)
            )

    # --------------------------------------------------------
    # TXT
    # --------------------------------------------------------

    elif ext == "txt":

        try:
            with open(
                file_path,
                "rb"
            ) as raw_file:

                raw_bytes = (
                    raw_file.read()
                )

            try:
                text_content = (
                    raw_bytes.decode(
                        "utf-8-sig"
                    )
                )

            except UnicodeDecodeError:

                text_content = (
                    raw_bytes.decode(
                        "latin-1",
                        errors="ignore"
                    )
                )

        except Exception as exc:
            raise ValueError(
                "No se pudo procesar el TXT: "
                + str(exc)
            )

    else:

        raise ValueError(
            f"Formato no soportado: {ext}"
        )

    text_content = (
        text_content or ""
    ).strip()

    if not text_content:
        raise ValueError(
            "El archivo no contiene texto "
            "interpretable o está vacío."
        )

    # No se parte el documento en supuestos hallazgos.
    # Se conserva el informe completo para que la IA
    # comprenda su estructura global.
    if (
        len(text_content)
        > MAX_SOURCE_CHARS
    ):
        text_content = (
            text_content[
                :MAX_SOURCE_CHARS
            ]
        )

    return text_content


# ============================================================
# BARRERA CONTRA TÍTULOS FALSOS
# ============================================================

SECTION_ONLY_TITLES = {
    "hallazgo",
    "hallazgos",
    "observacion",
    "observaciones",
    "resultado",
    "resultados",
    "analisis",
    "análisis",
    "conclusion",
    "conclusiones",
    "resumen",
    "resumen ejecutivo",
    "objetivo",
    "objetivos",
    "alcance",
    "metodologia",
    "metodología",
    "recomendaciones",
    "propuestas de mejora",
}


def _is_section_only_title(
    value
):
    norm = normalize_text(
        value
    )

    norm = re.sub(
        r"^\[(?:heading|bold)\]\s*",
        "",
        norm
    )

    # Ejemplo:
    # "7. Hallazgos" -> "hallazgos"
    norm = re.sub(
        r"^\d+(?:\.\d+)*"
        r"[\.\-\)]?\s*",
        "",
        norm
    )

    norm = norm.strip(
        " :-."
    )

    if (
        norm
        in SECTION_ONLY_TITLES
    ):
        return True

    if re.fullmatch(
        r"(?:seccion|sección|"
        r"capitulo|capítulo)\s+\d+",
        norm
    ):
        return True

    return False


def _deduplicate_findings(
    findings
):
    result = []
    seen = set()

    for finding in findings:

        key = normalize_text(
            finding.title
            or finding.original_title
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(key)

        result.append(
            finding
        )

    return result


# ============================================================
# VALIDACIÓN LOCAL DE LA RESPUESTA DE IA
# ============================================================

def _sanitize_document_extraction(
    extraction,
    raw_text
):
    if not extraction:
        return None

    raw_norm = normalize_text(
        raw_text
    )

    cleaned = []

    for finding in (
        extraction.findings
        or []
    ):

        title = clean_text(
            finding.title
            or finding.original_title
        )

        original_title = clean_text(
            finding.original_title
            or finding.title
        )

        situation = clean_text(
            finding.situation
        )

        source_excerpt = clean_text(
            finding.source_excerpt
        )

        confidence = float(
            finding.confidence
            or 0
        )

        # Nunca permitir que "7. Hallazgos"
        # entre como un hallazgo.
        if (
            _is_section_only_title(
                title
            )
            or _is_section_only_title(
                original_title
            )
        ):
            continue

        if not title:
            continue

        if not situation:
            situation = title

        # Verifica si la breve evidencia de trazabilidad
        # realmente aparece en el documento.
        excerpt_supported = False

        if source_excerpt:

            excerpt_norm = normalize_text(
                source_excerpt
            )

            if (
                excerpt_norm
                and excerpt_norm
                in raw_norm
            ):
                excerpt_supported = True

        # Si la IA tiene confianza muy baja Y además
        # no puede apuntar a un fragmento real del Word,
        # no cargamos ese registro.
        if (
            confidence < 0.45
            and not excerpt_supported
        ):
            continue

        proposals = []

        for proposal in (
            finding.proposals
            or []
        ):

            proposal_text = clean_text(
                proposal.proposal_text
            )

            # Solo conservamos propuestas que la IA
            # identificó como respaldadas por la fuente.
            if (
                proposal_text
                and proposal.source_supported
            ):
                proposals.append(
                    AuditProposal(
                        proposal_text=(
                            proposal_text
                        ),
                        source_supported=True
                    )
                )

        finding.original_title = (
            original_title
        )

        finding.title = (
            _clip_text(
                title,
                180
            )
        )

        finding.situation = (
            _clip_text(
                situation,
                1600
            )
        )

        finding.evidence = (
            _clip_text(
                finding.evidence,
                1000
            )
        )

        finding.risk = (
            _clip_text(
                finding.risk,
                700
            )
        )

        finding.affected_process_or_control = (
            _clip_text(
                finding.affected_process_or_control,
                500
            )
        )

        finding.cause = (
            _clip_text(
                finding.cause,
                500
            )
            if finding.cause
            else None
        )

        finding.impact = (
            _clip_text(
                finding.impact,
                500
            )
            if finding.impact
            else None
        )

        finding.severity = (
            _safe_severity(
                finding.severity
            )
        )

        finding.responsible_area = (
            clean_text(
                finding.responsible_area
            )
            or None
        )

        finding.source_excerpt = (
            source_excerpt
        )

        finding.proposals = (
            proposals
        )

        cleaned.append(
            finding
        )

    cleaned = (
        _deduplicate_findings(
            cleaned
        )
    )

    extraction.findings = (
        cleaned
    )

    extraction.declared_finding_count = (
        len(cleaned)
    )

    return extraction


# ============================================================
# PROMPT PRINCIPAL: IA LEE TODO EL INFORME
# ============================================================

DOCUMENT_EXTRACTION_INSTRUCTIONS = """
Actuás como Auditor Interno Senior especializado en lectura documental.

El documento que recibís YA contiene hallazgos de Auditoría definidos por
el equipo auditor y, cuando corresponde, también contiene sus propuestas
de mejora o recomendaciones.

TU TAREA NO ES DESCUBRIR PROBLEMAS NUEVOS.

Tu tarea es reconstruir correctamente la estructura real del informe,
identificar cada hallazgo que el auditor efectivamente definió, reunir
todos los párrafos que pertenecen a ese hallazgo e identificar la o las
propuestas de mejora que pertenecen a ese mismo hallazgo.

OBJETIVO PRINCIPAL

Devolver exactamente los hallazgos reales del documento, no párrafos,
no títulos de sección y no ejemplos aislados.

REGLAS DE ESTRUCTURA

1. Un encabezado general como:
   "Hallazgos",
   "7. Hallazgos",
   "Resultados",
   "Análisis",
   "Observaciones",
   "Conclusiones",
   "Resumen",
   "Objetivos",
   "Alcance",
   "Metodología",
   "Propuestas de mejora"
   NO es un hallazgo.

2. No conviertas cada línea, párrafo, viñeta, ejemplo, evidencia,
comentario de reunión, respuesta del área, sucursal, producto o caso
analizado en un hallazgo independiente.

3. Si un hallazgo contiene varios ejemplos o casos que sustentan una
misma observación, mantenelos dentro del MISMO hallazgo.

4. No unas dos observaciones independientes si el documento las presenta
como hallazgos diferentes.

5. La cantidad final debe corresponder a la cantidad real de hallazgos
definidos por el auditor en el documento.

6. Prestá especial atención a señales de estructura:
   [HEADING], [BOLD], [TABLE_ROW], numeración, títulos, subtítulos,
   etiquetas como Hallazgo, Observación, Situación observada,
   Análisis, Evidencia, Riesgo, Recomendación y Propuesta de mejora.

HALLAZGO

7. original_title debe preservar el título o identificación que aparece
en el informe.

8. title debe ser un título profesional y conciso para el mismo hallazgo.
Podés limpiar numeraciones o frases de formato, pero no cambiar su sentido.

9. situation NO debe ser una copia indiscriminada de todo el bloque.
Debe interpretar y sintetizar la situación observada de forma profesional:
qué ocurrió, dónde o sobre qué proceso ocurrió y qué evidencia relevante
lo sustenta. Conservá cifras, fechas, documentos, sistemas y datos concretos
cuando existan.

10. No inventes hechos, cifras, porcentajes, cantidades, fechas, códigos,
documentos, transacciones, sistemas, responsables, normativa o causas.

11. evidence debe contener únicamente evidencia concreta respaldada por
el documento.

12. cause debe ser null si el documento no permite sostener una causa.

13. impact debe ser null si no existe un impacto concreto o razonablemente
descripto en el documento.

14. risk debe expresar la consecuencia relevante del hallazgo. No uses
frases genéricas como "riesgo de control interno asociado al hallazgo".

15. severity solamente puede ser Alto, Medio o Bajo. Clasificala de acuerdo
con la relevancia del riesgo y la evidencia disponible, sin exagerar.

16. responsible_area debe surgir del informe. Si no puede determinarse con
suficiente respaldo, devolver null.

PROPUESTAS DE MEJORA

17. Las propuestas ya existen en el informe. Debés DETECTARLAS y vincularlas
al hallazgo correcto.

18. No inventes una propuesta porque te parezca conveniente.

19. Si el documento tiene una propuesta/recomendación explícita, preservá
su sentido y contenido. Podés limpiar la redacción para que sea clara, pero
no reemplazarla por una recomendación diferente.

20. Si un hallazgo tiene dos propuestas explícitas, devolvé las dos.

21. Si el hallazgo no tiene propuesta explícita, proposals debe ser [].

22. Cada propuesta recuperada desde el documento debe tener
source_supported=true.

TRAZABILIDAD

23. source_excerpt debe ser una cita BREVE y LITERAL del documento que
permita comprobar que el hallazgo existe. No la parafrasees.

24. confidence debe estar entre 0 y 1 e indicar tu confianza en que el
registro corresponde a un hallazgo explícitamente definido en el informe.

25. No agregues planes de acción. Plan de acción y propuesta de mejora son
entidades diferentes.

El resultado debe poder ser revisado por otro auditor y debe conservar
trazabilidad con la fuente original.
"""


# ============================================================
# IA 1: EXTRACTOR DOCUMENTAL
# ============================================================

def extract_document_findings_with_ai(
    raw_text,
    filename
):
    client = (
        _get_openai_client()
    )

    if not client:
        return None

    fallback_title = (
        extract_report_title(
            raw_text,
            filename
        )
    )

    explicit_area = (
        extract_explicit_area(
            raw_text
        )
    )

    explicit_auditor = (
        extract_explicit_auditor(
            raw_text
        )
    )

    prompt = f"""
Analizá el informe completo.

ARCHIVO:
{filename}

TÍTULO DETECTADO POR REGLA SIMPLE:
{fallback_title}

ÁREA EXPLÍCITA DETECTADA, SI EXISTE:
{explicit_area or "No detectada"}

AUDITOR EXPLÍCITO DETECTADO, SI EXISTE:
{explicit_auditor or "No detectado"}

IMPORTANTE:
Los datos anteriores son solamente ayudas de navegación.
La fuente completa prevalece.

--- INICIO DEL INFORME ---

{raw_text}

--- FIN DEL INFORME ---

Reconstruí la estructura real del informe.

No conviertas títulos de sección ni párrafos sueltos en hallazgos.

Identificá solamente los hallazgos que ya están definidos por el auditor.

Identificá también las propuestas/recomendaciones que ya están escritas
y vinculalas con el hallazgo correspondiente.

Si el documento tiene 10 hallazgos reales, la salida debe contener
10 hallazgos, no 17 párrafos convertidos en registros.
"""

    try:
        response = (
            client
            .beta
            .chat
            .completions
            .parse(
                model=DEFAULT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            DOCUMENT_EXTRACTION_INSTRUCTIONS
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format=(
                    AuditDocumentExtraction
                ),
            )
        )

        result = (
            response
            .choices[0]
            .message
            .parsed
        )

        return (
            _sanitize_document_extraction(
                result,
                raw_text
            )
        )

    except Exception as exc:
        print(
            "[IA Documento] Error:",
            exc
        )

        return None


# ============================================================
# PROMPT DE SEGUNDO CONTROL
# ============================================================

DOCUMENT_REVIEW_INSTRUCTIONS = """
Actuás como Revisor Senior Independiente de Auditoría Interna.

Recibís:

1. el informe original completo;
2. una extracción estructurada realizada por otro modelo.

Tu función es verificar la ESTRUCTURA DOCUMENTAL, no inventar contenido.

CONTROL OBLIGATORIO

1. Verificá que cada registro sea un hallazgo real definido en el informe.

2. Eliminá falsos hallazgos que sean títulos de sección como
"7. Hallazgos".

3. Detectá si un solo hallazgo fue partido erróneamente en varios registros.

4. Detectá si dos hallazgos distintos fueron fusionados.

5. Verificá que no se hayan convertido ejemplos, casos, evidencia,
comentarios del área o párrafos de análisis en hallazgos independientes.

6. Verificá que cada propuesta de mejora corresponda al hallazgo correcto.

7. Verificá que no se hayan inventado propuestas.

8. Verificá que se hayan conservado las propuestas explícitas del documento.

9. Verificá que cifras, fechas, códigos, documentos, sistemas y hechos sean
fieles a la fuente.

10. Verificá que la síntesis de la situación represente el contenido completo
del hallazgo y no sea una simple copia de la primera línea.

11. Verificá que la cantidad final de hallazgos corresponda a la estructura
real del informe.

12. No crees hallazgos nuevos para "mejorar" la cobertura.

13. Si un mismo hallazgo incluye varios productos, sucursales, casos o
ejemplos como evidencia de una misma problemática, no los separes salvo
que el informe explícitamente los presente como hallazgos diferentes.

SI TODO ESTÁ CORRECTO:

approved=true
corrected_result=null

SI HAY ERRORES:

approved=false

Detallá los problemas en issues y devolvé en corrected_result la estructura
COMPLETA corregida.

Toda corrección debe estar respaldada por el informe original.
"""


# ============================================================
# IA 2: REVISOR DOCUMENTAL
# ============================================================

def review_document_extraction(
    raw_text,
    extraction
):
    client = (
        _get_openai_client()
    )

    if (
        not client
        or extraction is None
    ):
        return None

    prompt = f"""
--- INFORME ORIGINAL ---

{raw_text}

--- FIN INFORME ORIGINAL ---


--- EXTRACCIÓN PROPUESTA ---

{extraction.model_dump_json(indent=2)}

--- FIN EXTRACCIÓN PROPUESTA ---


Revisá especialmente:

- cantidad real de hallazgos;
- títulos de sección mal clasificados;
- un hallazgo partido en varios;
- varios hallazgos unidos incorrectamente;
- propuestas asociadas al hallazgo incorrecto;
- propuestas inventadas;
- propuestas del informe que hayan sido omitidas.
"""

    try:
        response = (
            client
            .beta
            .chat
            .completions
            .parse(
                model=REVIEW_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            DOCUMENT_REVIEW_INSTRUCTIONS
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format=(
                    AuditDocumentReview
                ),
            )
        )

        return (
            response
            .choices[0]
            .message
            .parsed
        )

    except Exception as exc:
        print(
            "[IA Revisión Documento] Error:",
            exc
        )

        return None


# ============================================================
# PIPELINE IA COMPLETO
# ============================================================

def run_document_ai_pipeline(
    raw_text,
    filename
):
    extraction = (
        extract_document_findings_with_ai(
            raw_text,
            filename
        )
    )

    if not extraction:
        return None

    review = (
        review_document_extraction(
            raw_text,
            extraction
        )
    )

    # Si la segunda IA falla por conexión/crédito,
    # conservamos la primera extracción estructurada.
    # NO usamos el viejo fallback que partía párrafos.
    if review is None:

        print(
            "[IA] La revisión secundaria no estuvo "
            "disponible. Se conserva la extracción "
            "documental primaria."
        )

        return extraction

    if review.approved:

        return extraction

    if review.corrected_result:

        corrected = (
            _sanitize_document_extraction(
                review.corrected_result,
                raw_text
            )
        )

        if corrected:
            return corrected

    print(
        "[IA] El revisor detectó problemas "
        "y no devolvió una estructura "
        "corregida utilizable."
    )

    return None


# ============================================================
# FALLBACK ESTRICTO
#
# IMPORTANTE:
# YA NO EXISTE EL FALLBACK QUE GENERABA UN HALLAZGO
# CADA 300 / 4000 CARACTERES.
# ============================================================

EXPLICIT_FINDING_RE = re.compile(
    r"^(?:hallazgo|"
    r"observaci[oó]n|"
    r"desviaci[oó]n)"
    r"\s*(?:n[°º]?\s*)?"
    r"(\d+)"
    r"\s*[:\-\.\)]?\s*"
    r"(.*)$",
    re.IGNORECASE
)


EXPLICIT_PROPOSAL_RE = re.compile(
    r"^(?:propuesta"
    r"(?:\s+de\s+mejora)?|"
    r"recomendaci[oó]n|"
    r"acci[oó]n\s+recomendada)"
    r"\s*[:\-]\s*"
    r"(.*)$",
    re.IGNORECASE
)


def strict_explicit_fallback(
    raw_text,
    default_area=""
):
    """
    Fallback deliberadamente conservador.

    Solo reconoce hallazgos etiquetados explícitamente
    como "Hallazgo N", "Observación N" o "Desviación N".

    Nunca convierte párrafos arbitrarios en hallazgos.
    """

    lines = [
        clean_text(
            re.sub(
                r"^\[(?:HEADING|"
                r"BOLD|HEADER)\]\s*",
                "",
                line,
                flags=re.IGNORECASE
            )
        )
        for line in raw_text.splitlines()
        if clean_text(line)
    ]

    findings = []

    current = None

    proposal_mode = False

    for line in lines:

        finding_match = (
            EXPLICIT_FINDING_RE.match(
                line
            )
        )

        if finding_match:

            if current:
                findings.append(
                    current
                )

            number = (
                finding_match.group(1)
            )

            title = clean_text(
                finding_match.group(2)
            )

            if not title:

                title = (
                    f"Hallazgo {number}"
                )

            current = {
                "title": title,
                "situation_lines": [],
                "proposal_lines": [],
                "responsible_area": (
                    default_area
                ),
            }

            proposal_mode = False

            continue

        if not current:
            continue

        proposal_match = (
            EXPLICIT_PROPOSAL_RE.match(
                line
            )
        )

        if proposal_match:

            proposal_mode = True

            initial_text = (
                clean_text(
                    proposal_match.group(1)
                )
            )

            if initial_text:

                current[
                    "proposal_lines"
                ].append(
                    initial_text
                )

            continue

        if proposal_mode:

            current[
                "proposal_lines"
            ].append(
                line
            )

        else:

            current[
                "situation_lines"
            ].append(
                line
            )

    if current:

        findings.append(
            current
        )

    result = []

    for index, item in enumerate(
        findings,
        start=1
    ):

        title = clean_text(
            item["title"]
        )

        if _is_section_only_title(
            title
        ):
            continue

        situation = (
            _clip_text(
                " ".join(
                    item[
                        "situation_lines"
                    ]
                ),
                1600
            )
        )

        if not situation:
            situation = title

        proposal = (
            _clip_text(
                " ".join(
                    item[
                        "proposal_lines"
                    ]
                ),
                1200
            )
        )

        result.append({
            "title": title,

            "situation": situation,

            "risk": "",

            "severity": "Medio",

            "responsible_area": (
                item[
                    "responsible_area"
                ]
                or ""
            ),

            "evidence": "",

            "cause": "",

            "affected_process_or_control": "",

            "impact": "",

            "proposals_ai": (
                [proposal]
                if proposal
                else []
            ),

            "ai_confidence": 0.50,

            "ai_validated": False,

            "ai_status": (
                "Fallback estructural explícito"
            ),

            "source_text": (
                situation
            ),

            "source_location": (
                f"Hallazgo explícito {index}"
            ),
        })

    return result


# ============================================================
# CONVERSIÓN DE IA A ESTRUCTURA DE AUDITTRACK
# ============================================================

def _document_to_relational_findings(
    document,
    filename,
    fallback_area=""
):
    relational_findings = []

    proposal_counter = 1

    for index, finding in enumerate(
        document.findings,
        start=1
    ):

        finding_id = str(
            uuid.uuid4()
        )

        title = clean_text(
            finding.title
            or finding.original_title
            or f"Hallazgo {index}"
        )

        situation = clean_text(
            finding.situation
            or title
        )

        source_key = (
            hashlib.md5(
                (
                    f"{filename}_"
                    f"{index}_"
                    f"{finding.original_title}_"
                    f"{finding.source_excerpt}"
                ).encode(
                    "utf-8"
                )
            )
            .hexdigest()[:12]
        )

        source_item_id = (
            f"src_{source_key}"
        )

        severity = (
            _safe_severity(
                finding.severity
            )
        )

        area = clean_text(
            finding.responsible_area
        )

        if not area:

            area = (
                fallback_area
                or "Pendiente de definir"
            )

        proposals = []

        for proposal in (
            finding.proposals
            or []
        ):

            proposal_text = (
                clean_text(
                    proposal.proposal_text
                )
            )

            if not proposal_text:
                continue

            pm_code = (
                f"PM-2026-"
                f"{proposal_counter:03d}"
            )

            proposal_counter += 1

            proposals.append({

                "id": str(
                    uuid.uuid4()
                ),

                "finding_id": (
                    finding_id
                ),

                "code": pm_code,

                "title": (
                    proposal_text
                ),

                "proposal_text": (
                    proposal_text
                ),

                "severity": (
                    severity
                ),

                "responsible_area": (
                    area
                ),

                "action_owner": (
                    "Pendiente de definir"
                ),

                "target_date": "",

                "status": (
                    "Pendiente"
                ),

                "action_plans": [],
            })

        relational_findings.append({

            "id": finding_id,

            "sourceItemId": (
                source_item_id
            ),

            "sourceItemIds": [
                source_item_id
            ],

            "sourceKey": (
                source_key
            ),

            "sourceFile": (
                filename
            ),

            "sourceLocation": (
                finding.source_excerpt
                or f"Hallazgo {index}"
            ),

            "source_text": (
                finding.source_excerpt
                or situation
            ),

            "evidence": (
                clean_text(
                    finding.evidence
                )
            ),

            "included": True,

            "selectedAsFinding": True,

            "converted": False,

            "code": (
                f"H-2026-{index:03d}"
            ),

            "title": (
                title
            ),

            "situation": (
                situation
            ),

            "risk": (
                clean_text(
                    finding.risk
                )
            ),

            "severity": (
                severity
            ),

            "responsible_area": (
                area
            ),

            "action_owner": (
                "Pendiente de definir"
            ),

            "status": (
                "Pendiente"
            ),

            "observations": "",

            "proposals": (
                proposals
            ),

            "cause": (
                clean_text(
                    finding.cause
                )
            ),

            "affected_process_or_control": (
                clean_text(
                    finding
                    .affected_process_or_control
                )
            ),

            "impact": (
                clean_text(
                    finding.impact
                )
            ),

            "ai_confidence": (
                float(
                    finding.confidence
                    or 0
                )
            ),

            "ai_validated": True,

            "ai_status": (
                "IA documental + revisión"
            ),
        })

    return relational_findings


# ============================================================
# CONVERSIÓN DEL FALLBACK A AUDITTRACK
# ============================================================

def _fallback_to_relational_findings(
    findings,
    filename,
    fallback_area=""
):
    relational_findings = []

    proposal_counter = 1

    for index, finding in enumerate(
        findings,
        start=1
    ):

        finding_id = str(
            uuid.uuid4()
        )

        title = clean_text(
            finding.get(
                "title",
                f"Hallazgo {index}"
            )
        )

        source_key = (
            hashlib.md5(
                (
                    f"{filename}_"
                    f"{index}_"
                    f"{title}"
                ).encode(
                    "utf-8"
                )
            )
            .hexdigest()[:12]
        )

        source_item_id = (
            f"src_{source_key}"
        )

        proposals = []

        for proposal_text in (
            finding.get(
                "proposals_ai",
                []
            )
            or []
        ):

            proposal_text = (
                clean_text(
                    proposal_text
                )
            )

            if not proposal_text:
                continue

            code = (
                f"PM-2026-"
                f"{proposal_counter:03d}"
            )

            proposal_counter += 1

            proposals.append({

                "id": str(
                    uuid.uuid4()
                ),

                "finding_id": (
                    finding_id
                ),

                "code": (
                    code
                ),

                "title": (
                    proposal_text
                ),

                "proposal_text": (
                    proposal_text
                ),

                "severity": (
                    _safe_severity(
                        finding.get(
                            "severity"
                        )
                    )
                ),

                "responsible_area": (
                    clean_text(
                        finding.get(
                            "responsible_area"
                        )
                    )
                    or fallback_area
                    or "Pendiente de definir"
                ),

                "action_owner": (
                    "Pendiente de definir"
                ),

                "target_date": "",

                "status": (
                    "Pendiente"
                ),

                "action_plans": [],
            })

        relational_findings.append({

            "id": (
                finding_id
            ),

            "sourceItemId": (
                source_item_id
            ),

            "sourceItemIds": [
                source_item_id
            ],

            "sourceKey": (
                source_key
            ),

            "sourceFile": (
                filename
            ),

            "sourceLocation": (
                finding.get(
                    "source_location"
                )
                or
                f"Hallazgo explícito {index}"
            ),

            "source_text": (
                finding.get(
                    "source_text"
                )
                or finding.get(
                    "situation"
                )
                or title
            ),

            "evidence": (
                clean_text(
                    finding.get(
                        "evidence"
                    )
                )
            ),

            "included": True,

            "selectedAsFinding": True,

            "converted": False,

            "code": (
                f"H-2026-{index:03d}"
            ),

            "title": (
                title
            ),

            "situation": (
                clean_text(
                    finding.get(
                        "situation"
                    )
                    or title
                )
            ),

            "risk": (
                clean_text(
                    finding.get(
                        "risk"
                    )
                )
            ),

            "severity": (
                _safe_severity(
                    finding.get(
                        "severity"
                    )
                )
            ),

            "responsible_area": (
                clean_text(
                    finding.get(
                        "responsible_area"
                    )
                )
                or fallback_area
                or "Pendiente de definir"
            ),

            "action_owner": (
                "Pendiente de definir"
            ),

            "status": (
                "Pendiente"
            ),

            "observations": "",

            "proposals": (
                proposals
            ),

            "cause": (
                clean_text(
                    finding.get(
                        "cause"
                    )
                )
            ),

            "affected_process_or_control": (
                clean_text(
                    finding.get(
                        "affected_process_or_control"
                    )
                )
            ),

            "impact": (
                clean_text(
                    finding.get(
                        "impact"
                    )
                )
            ),

            "ai_confidence": (
                float(
                    finding.get(
                        "ai_confidence",
                        0
                    )
                )
            ),

            "ai_validated": False,

            "ai_status": (
                finding.get(
                    "ai_status",
                    "Fallback estructural explícito"
                )
            ),
        })

    return relational_findings


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def parse_audit_report(
    file_path,
    filename
):
    """
    NUEVA LÓGICA

    1. Extrae el documento preservando títulos,
       negritas y tablas.

    2. La IA lee el informe COMPLETO.

    3. Identifica solamente los hallazgos
       que el auditor ya definió.

    4. Une todos los párrafos correspondientes
       al mismo hallazgo.

    5. Detecta las propuestas de mejora ya
       existentes en el informe.

    6. Vincula cada propuesta con su hallazgo.

    7. Una segunda IA revisa cantidad, estructura,
       falsos positivos y relaciones.

    8. Se elimina expresamente un título como
       "7. Hallazgos" si intentara entrar como
       hallazgo.

    9. Si la IA falla, NO se divide el informe
       por cantidad de caracteres.

    10. El fallback únicamente acepta títulos
        explícitos como "Hallazgo 1".
    """

    raw_text = (
        extract_raw_text_from_file(
            file_path
        )
    )

    fallback_title = (
        extract_report_title(
            raw_text,
            filename
        )
    )

    fallback_area = (
        extract_explicit_area(
            raw_text
        )
    )

    fallback_auditor = (
        extract_explicit_auditor(
            raw_text
        )
    )

    # --------------------------------------------------------
    # PRIMERA OPCIÓN: IA SOBRE EL DOCUMENTO COMPLETO
    # --------------------------------------------------------

    document = (
        run_document_ai_pipeline(
            raw_text,
            filename
        )
    )

    if (
        document
        and document.findings
    ):

        report_title = (
            clean_text(
                document.report_title
            )
            or fallback_title
        )

        report_area = (
            clean_text(
                document.area
                or document.process
            )
            or fallback_area
            or "Pendiente de definir"
        )

        auditor = (
            clean_text(
                document.auditor
            )
            or fallback_auditor
            or "Auditoría Interna"
        )

        relational_findings = (
            _document_to_relational_findings(
                document,
                filename,
                report_area
            )
        )

    # --------------------------------------------------------
    # FALLBACK:
    # SOLO SI EL INFORME TIENE "HALLAZGO 1", ETC.
    # --------------------------------------------------------

    else:

        strict_findings = (
            strict_explicit_fallback(
                raw_text,
                fallback_area
            )
        )

        if not strict_findings:

            raise ValueError(
                "No fue posible identificar los "
                "hallazgos del informe con suficiente "
                "confianza. AuditTrack no generó "
                "hallazgos por párrafos o bloques "
                "arbitrarios para evitar falsos "
                "positivos. Verificá que "
                "OPENAI_API_KEY esté configurada y "
                "que el servicio de IA esté disponible."
            )

        report_title = (
            fallback_title
        )

        report_area = (
            fallback_area
            or "Pendiente de definir"
        )

        auditor = (
            fallback_auditor
            or "Auditoría Interna"
        )

        relational_findings = (
            _fallback_to_relational_findings(
                strict_findings,
                filename,
                report_area
            )
        )

    # --------------------------------------------------------
    # ÚLTIMA BARRERA DE SEGURIDAD
    # --------------------------------------------------------

    relational_findings = [
        finding
        for finding
        in relational_findings
        if not _is_section_only_title(
            finding.get(
                "title",
                ""
            )
        )
    ]

    # Reenumerar después de eliminar
    # cualquier falso encabezado.
    for index, finding in enumerate(
        relational_findings,
        start=1
    ):

        finding["code"] = (
            f"H-2026-{index:03d}"
        )

    print(
        f"[Parser] '{filename}' procesado: "
        f"{len(relational_findings)} "
        "hallazgos reales."
    )

    return {

        "report": {

            "title": (
                report_title
            ),

            "process": (
                report_area
            ),

            "area": (
                report_area
            ),

            "period": (
                "2026"
            ),

            "auditor": (
                auditor
            ),

            "summary": (
                f"Informe {filename} procesado "
                f"con {len(relational_findings)} "
                "hallazgos estructurados."
            ),

            "source_filename": (
                filename
            ),
        },

        "findings": (
            relational_findings
        ),
    }
