"""Agent state in SQLite, in the private data folder (ADR 0004).

Migrations are named, not numbered, so features built in parallel can each add their own without clashing.
"""
import os
import sqlite3
import threading
from pathlib import Path

from .config import private_dir

MIGRATIONS: list[tuple[str, str]] = [
    ("core_actions", """
        CREATE TABLE pending_actions (
            id TEXT PRIMARY KEY,
            uid TEXT NOT NULL,
            tool TEXT NOT NULL,
            input TEXT NOT NULL,
            summary TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            image TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            result TEXT
        );
        CREATE INDEX pending_actions_uid_status ON pending_actions (uid, status);
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            uid TEXT NOT NULL,
            action_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            input TEXT NOT NULL,
            confirmed_by TEXT NOT NULL,
            outcome TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        );
    """),
    ("core_connections", """
        CREATE TABLE connections (
            id TEXT PRIMARY KEY,
            uid TEXT NOT NULL,
            connector TEXT NOT NULL,
            account TEXT NOT NULL DEFAULT '',
            access TEXT NOT NULL DEFAULT 'api',
            created_at TEXT NOT NULL,
            UNIQUE (uid, connector, account)
        );
    """),
]


class Database:
    def __init__(self, data_dir: Path):
        private_dir(data_dir)
        self.path = data_dir / "alter.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if os.name != "nt":
            self.path.chmod(0o600)
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
            done = {row["name"] for row in self._conn.execute("SELECT name FROM migrations")}
            for name, sql in MIGRATIONS:
                if name in done:
                    continue
                self._conn.execute("BEGIN")
                try:
                    for statement in filter(str.strip, sql.split(";")):
                        self._conn.execute(statement)
                    self._conn.execute("INSERT INTO migrations (name) VALUES (?)", (name,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()
