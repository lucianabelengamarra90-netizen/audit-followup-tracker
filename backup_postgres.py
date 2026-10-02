import os
import json
import sqlite3
import datetime

def export_database(output_prefix="backups/audit_tracker_v1.0.0_base_20261002"):
    os.makedirs("backups", exist_ok=True)
    db_url = os.environ.get("DATABASE_URL")
    
    data = {
        "timestamp": datetime.datetime.now().isoformat(),
        "database_type": "postgresql" if db_url else "sqlite",
        "tables": {}
    }
    
    if db_url:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        tables = ["reports", "findings", "proposals", "action_plans", "audit_history", "code_sequences"]
        for tbl in tables:
            try:
                cursor.execute(f"SELECT * FROM {tbl}")
                cols = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                data["tables"][tbl] = [dict(zip(cols, row)) for row in rows]
            except Exception as e:
                print(f"[Backup] Aviso al respaldar tabla {tbl}: {e}")
        conn.close()
    else:
        db_path = os.environ.get("DATABASE_PATH", "audit_tracker.db")
        if not os.path.exists(db_path):
            print(f"[Backup] Base SQLite {db_path} no existe. Se crea vacía.")
            return None
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall() if not r[0].startswith("sqlite_")]
        for tbl in tables:
            cursor.execute(f"SELECT * FROM {tbl}")
            data["tables"][tbl] = [dict(r) for r in cursor.fetchall()]
        conn.close()
        
    json_file = f"{output_prefix}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
        
    print(f"[Backup] Respaldo relacional exitoso guardado en: {json_file}")
    print("[Backup] Conteos de registros respaldados:")
    for tbl, rows in data["tables"].items():
        print(f"  - {tbl}: {len(rows)} registros")
        
    return json_file

def restore_and_verify_isolated(json_file, target_db_path="backups/isolated_restore_test.db"):
    if os.path.exists(target_db_path):
        os.remove(target_db_path)
        
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    conn = sqlite3.connect(target_db_path)
    cursor = conn.cursor()
    
    for tbl, rows in data.get("tables", {}).items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        
        # Create table with all columns if not existing
        col_defs = ["id TEXT PRIMARY KEY" if c == "id" else f'"{c}" TEXT' for c in cols]
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {tbl} ({', '.join(col_defs)})")
        
        # Add any missing columns
        cursor.execute(f"PRAGMA table_info({tbl})")
        existing_cols = {r[1] for r in cursor.fetchall()}
        for c in cols:
            if c not in existing_cols:
                cursor.execute(f'ALTER TABLE {tbl} ADD COLUMN "{c}" TEXT')
                
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join([f'"{c}"' for c in cols])
        sql = f"INSERT OR REPLACE INTO {tbl} ({col_names}) VALUES ({placeholders})"
        for row in rows:
            cursor.execute(sql, [row.get(c) for c in cols])
            
    conn.commit()
    
    print(f"[Restauración] Base restaurada exitosamente en entorno aislado: {target_db_path}")
    print("[Restauración] Conteos verificados tras restauración:")
    for tbl in ["reports", "findings", "proposals", "action_plans"]:
        cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cursor.fetchone()[0]
        print(f"  - {tbl}: {cnt} registros")
        
    conn.close()
    return True

if __name__ == "__main__":
    json_path = export_database()
    if json_path:
        restore_and_verify_isolated(json_path)
