"""Explicit migration command; never run DDL automatically on service startup."""

import hashlib
from pathlib import Path

from tronenergy_storage.db import transaction


def migrate():
    root = Path(__file__).resolve().parents[4] / "migrations"
    with transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(875210)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(name text PRIMARY KEY, checksum text NOT NULL)"
        )
        for path in sorted(root.glob("[0-9]*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            old = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE name=%s", (path.name,)
            ).fetchone()
            if old:
                if old["checksum"] != checksum:
                    raise RuntimeError("applied migration checksum mismatch")
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations VALUES (%s,%s)", (path.name, checksum))


if __name__ == "__main__":
    migrate()
