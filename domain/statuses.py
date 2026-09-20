from datetime import datetime
from typing import Optional

PERSISTED_STATUSES = ["En proceso", "En suspensión", "Finalizado"]

FINISHED_VARIANTS = {
    "finalizado", "finalizada", "completado", "completada", 
    "cerrado", "cerrada", "implementado", "implementada", "archivada"
}

SUSPENDED_VARIANTS = {
    "en suspensión", "en suspension", "stand-by", "suspendida", "suspendido"
}


def normalize_status(raw_status: Optional[str]) -> str:
    """
    Normaliza cualquier texto de estado a uno de los 3 estados persistidos oficiales:
    'En proceso', 'En suspensión' o 'Finalizado'.
    """
    if not raw_status:
        return "En proceso"
    
    clean = str(raw_status).strip().lower()
    
    if clean in FINISHED_VARIANTS:
        return "Finalizado"
    if clean in SUSPENDED_VARIANTS:
        return "En suspensión"
    
    return "En proceso"


def is_final_status(status: Optional[str]) -> bool:
    """Determina si un estado normalizado es final."""
    return normalize_status(status) == "Finalizado"


def compute_effective_status(status_raw: Optional[str], target_date_str: Optional[str], today: Optional[datetime] = None) -> str:
    """
    Calcula el estado efectivo (dinámico) en tiempo de ejecución.
    REGLAS:
    1. Finalizado -> Finalizado
    2. En suspensión -> En suspensión
    3. En proceso + fecha compromiso < hoy -> Vencido
    4. En proceso + fecha compromiso >= hoy -> En proceso
    5. Sin fecha compromiso -> En proceso (nunca se marca como Vencido)
    """
    norm = normalize_status(status_raw)
    
    if norm == "Finalizado":
        return "Finalizado"
    if norm == "En suspensión":
        return "En suspensión"
        
    if target_date_str:
        try:
            today_date = (today or datetime.now()).date()
            t_str = str(target_date_str).strip()[:10]
            d = None
            if "-" in t_str and len(t_str) == 10:
                d = datetime.strptime(t_str, "%Y-%m-%d").date()
            elif "/" in t_str:
                parts = t_str.split("/")
                if len(parts) == 3 and len(parts[2]) == 4:
                    d = datetime.strptime(f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}", "%Y-%m-%d").date()

            if d and d < today_date:
                return "Vencido"
        except Exception:
            pass
            
    return "En proceso"


def get_status_badge_class(effective_status: str) -> str:
    """Retorna la clase CSS correspondiente al estado efectivo."""
    clean = (effective_status or "en-proceso").lower().replace(" ", "-").replace("ó", "o").replace("sión", "sion")
    return f"pill-{clean}"
