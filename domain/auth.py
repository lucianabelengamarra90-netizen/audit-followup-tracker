import functools
from flask import session, jsonify, request, redirect, url_for

import os

ROLE_READER = "Consulta"
ROLE_EDITOR = "Editor"
ROLE_VALIDATOR = "Validador"

ALL_ROLES = [ROLE_READER, ROLE_EDITOR, ROLE_VALIDATOR]

DEMO_USERS = {
    "lector": {"password": os.environ.get("READER_PASSWORD", "audit2026reader"), "name": "Usuario Consulta", "role": ROLE_READER},
    "editor": {"password": os.environ.get("EDITOR_PASSWORD", "audit2026editor"), "name": "Auditor Editor", "role": ROLE_EDITOR},
    "admin": {"password": os.environ.get("ADMIN_PASSWORD", "audit2026admin"), "name": "Luciana Gamarra (Admin)", "role": ROLE_VALIDATOR},
    "luciana": {"password": os.environ.get("ADMIN_PASSWORD", "audit2026admin"), "name": "Luciana Gamarra", "role": ROLE_VALIDATOR},
}

def get_current_user():
    return session.get("user")

def login_user(username, password):
    user_info = DEMO_USERS.get(username.lower())
    if user_info and user_info["password"] == password:
        user_session = {
            "username": username.lower(),
            "name": user_info["name"],
            "role": user_info["role"]
        }
        session["user"] = user_session
        return True, user_session
    return False, None

def logout_user():
    session.clear()

def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            if (
                request.path.startswith("/api/")
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                or request.is_json
                or request.method in ("POST", "PUT", "DELETE", "PATCH")
                or request.path in ["/upload-report", "/parse-preview", "/save-validated-report", "/import-excel", "/export-excel", "/findings", "/proposals", "/action-plans", "/reports", "/kpi-indicators", "/dashboard-stats"]
            ):
                return jsonify({"success": False, "error": "Acceso no autorizado. Inicie sesión."}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def require_role(*allowed_roles):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user or user.get("role") not in allowed_roles:
                if request.path.startswith("/api/") or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({
                        "success": False,
                        "error": f"Permisos insuficientes. Se requiere uno de los roles: {', '.join(allowed_roles)}"
                    }), 403
                return jsonify({"error": "Permisos insuficientes"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator
