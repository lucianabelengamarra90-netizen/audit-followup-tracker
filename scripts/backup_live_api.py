"""
Respaldo recuperable de la instancia en producción a través de la API autenticada.

Guarda la jerarquía completa (informes → hallazgos → propuestas → planes) más el
detalle por informe y el /api/health, de modo que pueda restaurarse con
`scripts/restore_from_api_backup.py` sobre cualquier base (SQLite o PostgreSQL)
conservando IDs, códigos y relaciones.

Uso:
    python scripts/backup_live_api.py https://audit-followup-tracker.onrender.com backups/
"""
import http.cookiejar
import json
import os
import sys
import urllib.request
from datetime import datetime


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "https://audit-followup-tracker.onrender.com").rstrip("/")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "backups"
    user = os.environ.get("AUDIT_USER", "admin")
    pwd = os.environ.get("AUDIT_PASSWORD", "audit2026admin")

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(path):
        with opener.open(f"{base}{path}", timeout=90) as r:
            return json.loads(r.read().decode("utf-8"))

    login_req = urllib.request.Request(
        f"{base}/login",
        data=json.dumps({"username": user, "password": pwd}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(login_req, timeout=90) as r:
        if r.status != 200:
            raise SystemExit("Login fallido")

    data = {"source": base, "timestamp": datetime.now().isoformat(), "endpoints": {}}
    for path in ("/api/health", "/reports", "/findings", "/proposals", "/action-plans"):
        data["endpoints"][path] = get(path)

    details = {}
    for rep in data["endpoints"]["/reports"].get("reports", []):
        details[rep["id"]] = get(f"/reports/{rep['id']}")
    data["report_details"] = details

    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"live_prod_backup_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    counts = data["endpoints"]["/api/health"].get("counts", {})
    print(f"[Backup] Guardado en {path}")
    print(f"[Backup] health.commit={data['endpoints']['/api/health'].get('commit')} counts={counts}")
    print(f"[Backup] reports={len(data['endpoints']['/reports'].get('reports', []))} "
          f"findings={len(data['endpoints']['/findings'].get('findings', []))} "
          f"proposals={len(data['endpoints']['/proposals'].get('proposals', []))} "
          f"plans={len(data['endpoints']['/action-plans'].get('action_plans', []))}")


if __name__ == "__main__":
    main()
