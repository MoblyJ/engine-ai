"""Schema migrations for engine_core stores (upgrade.md §7).

One helper, used by every engine_core SQLite store: user_version tracks the schema
version; each migration is (target_version, sql_script). New databases start at the
latest version; old ones upgrade forward in order. Never share a database file over a
network filesystem — these are local stores.
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection, migrations: list[tuple[int, str]]) -> int:
    """Apply forward migrations in order; returns the resulting schema version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in sorted(migrations):
        if version > current:
            with conn:
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {version}")
            current = version
    return current


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]
