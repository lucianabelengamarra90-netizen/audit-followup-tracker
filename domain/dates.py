from datetime import datetime
from typing import Optional


def parse_date_to_iso(date_input: Optional[str]) -> Optional[str]:
    """
    Normaliza cualquier entrada de fecha al formato estricto ISO `YYYY-MM-DD`.
    Retorna None si la entrada es nula, vacía o inválida.
    """
    if not date_input:
        return None
        
    s = str(date_input).strip()
    if not s or s.lower() in ("sin fecha", "none", "null", "undefined"):
        return None

    # Si viene con timestamp ISO YYYY-MM-DDTHH:MM:SS
    if "T" in s:
        s = s.split("T")[0]

    s = s[:10]

    # Formato ISO YYYY-MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            d = datetime.strptime(s, "%Y-%m-%d")
            return d.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # Formato Latam DD/MM/YYYY
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            try:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                d = datetime(year, month, day)
                return d.strftime("%Y-%m-%d")
            except ValueError:
                return None

    return None


def format_display_date(iso_date_str: Optional[str]) -> str:
    """
    Convierte una fecha ISO `YYYY-MM-DD` a formato visual `DD/MM/YYYY`.
    Si no existe o es inválida, retorna 'Sin fecha'.
    """
    iso = parse_date_to_iso(iso_date_str)
    if not iso:
        return "Sin fecha"
    
    parts = iso.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}"


def is_date_past(iso_date_str: Optional[str], today: Optional[datetime] = None) -> bool:
    """Evalúa si una fecha en formato ISO es estrictamente menor al día de hoy."""
    iso = parse_date_to_iso(iso_date_str)
    if not iso:
        return False
        
    try:
        t_date = (today or datetime.now()).date()
        d_date = datetime.strptime(iso, "%Y-%m-%d").date()
        return d_date < t_date
    except Exception:
        return False
