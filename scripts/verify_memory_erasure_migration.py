"""Check legacy revoked-value scrubbing on a fresh isolated PostgreSQL database."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    admin_url = os.environ.get("MEMORY_ERASURE_PG_ADMIN_URL", "")
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise RuntimeError("MEMORY_ERASURE_PG_ADMIN_URL must connect to a separate PostgreSQL /postgres database")
    name = "ra_memory_erase_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db_url = urlunsplit(("postgresql", parts.netloc, "/" + name, "", ""))
    env = os.environ.copy()
    env.update({"DATABASE_URL": db_url.replace("postgresql://", "postgresql+psycopg://", 1), "LONG_TERM_MEMORY_MODE": "off"})
    subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "8a512e96af34"], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    with psycopg.connect(db_url) as connection:
        connection.execute("INSERT INTO customers (id, display_name) VALUES (%s, %s)", ("synthetic-erase-check", "Synthetic erasure check"))
        connection.execute("INSERT INTO memory_entries (id, customer_id, key, value, revoked) VALUES (%s, %s, %s, %s, %s)", ("revoked-entry", "synthetic-erase-check", "language", "old synthetic preference", True))
        connection.execute("INSERT INTO memory_entries (id, customer_id, key, value, revoked) VALUES (%s, %s, %s, %s, %s)", ("active-entry", "synthetic-erase-check", "channel", "web", False))
    subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    with psycopg.connect(db_url) as connection:
        rows = connection.execute("SELECT id, key, value, revoked FROM memory_entries ORDER BY id").fetchall()
        assert rows == [("active-entry", "channel", "web", False), ("revoked-entry", "language", "", True)]
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "7c461acdb21e"
    print(json.dumps({"database": name, "migration": version, "checks": ["revoked_value_erased", "active_value_preserved", "row_identity_preserved"], "passed": 3}))


if __name__ == "__main__":
    main()
