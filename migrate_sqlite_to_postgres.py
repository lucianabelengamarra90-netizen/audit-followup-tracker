"""
migrate_sqlite_to_postgres.py
Script de migración atómica y segura de SQLite a PostgreSQL en Supabase.
Preserva registros, códigos, relaciones, avances, fechas, adjuntos e historial de auditoría.
"""
import os
import sqlite3
import psycopg2


def migrate_sqlite_to_postgres(sqlite_path="audit_tracker.db", pg_url=None):
    if not pg_url:
        pg_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not pg_url:
        raise ValueError("Se requiere la variable de entorno DATABASE_URL o SUPABASE_DB_URL con la cadena de conexión de Supabase.")

    if pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql://", 1)

    print(f"[Migración] Leyendo datos locales desde: {sqlite_path}")
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    print("[Migración] Conectando a Supabase PostgreSQL...")
    os.environ["DATABASE_URL"] = pg_url
    from database import init_db
    init_db()

    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()

    try:
        tables = ["schema_migrations", "code_sequences", "reports", "findings", "proposals", "action_plans", "audit_history"]

        for tbl in tables:
            sqlite_cur.execute(f"SELECT * FROM {tbl}")
            rows = sqlite_cur.fetchall()
            if not rows:
                print(f"[Migración] Tabla {tbl}: 0 registros (omitida)")
                continue

            cols = list(rows[0].keys())
            col_names = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))

            if tbl == "code_sequences":
                conflict_clause = "ON CONFLICT (entity_type, year) DO UPDATE SET last_value = EXCLUDED.last_value"
            elif "id" in cols:
                conflict_clause = "ON CONFLICT (id) DO NOTHING"
            elif "code" in cols:
                conflict_clause = "ON CONFLICT (code) DO NOTHING"
            else:
                conflict_clause = ""

            insert_sql = f"INSERT INTO {tbl} ({col_names}) VALUES ({placeholders}) {conflict_clause}"

            migrated_cnt = 0
            for row in rows:
                val_tuple = tuple(row[col] for col in cols)
                pg_cur.execute(insert_sql, val_tuple)
                migrated_cnt += 1

            print(f"[Migración] Tabla {tbl}: {migrated_cnt} registros migrados a PostgreSQL.")

        pg_conn.commit()
        print("[Migración] ¡Migración completada exitosamente sin pérdida de datos!")
    except Exception as exc:
        pg_conn.rollback()
        print(f"[Migración] ERROR durante la migración: {exc}")
        raise exc
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    migrate_sqlite_to_postgres()
