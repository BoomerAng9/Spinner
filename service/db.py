"""
Neon Postgres connection layer for Spinner.

Secrets are read at runtime from the environment (DATABASE_URL) — never hardcoded.
The Neon connection string already carries sslmode=require & channel_binding=require
and points at the -pooler endpoint, so PgBouncer handles pooling for us.

CLI:
    python db.py healthcheck   # verify connectivity (prints non-secret status)
    python db.py migrate       # apply migrations/*.sql in order (idempotent)
"""
from __future__ import annotations
import os
import sys
import pathlib
import contextlib

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to the environment (see .env.example). "
            "Never hardcode the connection string."
        )
    return url


@contextlib.contextmanager
def get_conn():
    """Yield a psycopg connection to Neon. The -pooler endpoint pools for us."""
    if psycopg is None:
        raise RuntimeError("psycopg is not installed — `pip install 'psycopg[binary]'`")
    conn = psycopg.connect(_database_url(), connect_timeout=20, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def healthcheck() -> dict:
    """Return non-secret connectivity facts. Raises on failure."""
    with get_conn() as conn:
        row = conn.execute(
            "select current_database(), current_user, version()"
        ).fetchone()
        return {"ok": True, "database": row[0], "user": row[1], "server": row[2].split(",")[0]}


def migrate() -> list[str]:
    """Apply every migrations/NNN_*.sql once, in filename order. Idempotent."""
    applied: list[str] = []
    with get_conn() as conn:
        conn.execute(
            "create table if not exists schema_migrations ("
            " filename text primary key, applied_at timestamptz not null default now())"
        )
        done = {r[0] for r in conn.execute("select filename from schema_migrations").fetchall()}
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.execute(sql)
            conn.execute("insert into schema_migrations(filename) values (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "healthcheck"
    if cmd == "healthcheck":
        print(healthcheck())
    elif cmd == "migrate":
        done = migrate()
        print("applied:", done if done else "(nothing new — already up to date)")
    else:
        print(f"unknown command: {cmd}  (use: healthcheck | migrate)")
        sys.exit(1)
