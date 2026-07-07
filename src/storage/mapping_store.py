"""SQLite storage for real value → pseudonymized value mappings.

Maintains pseudonym consistency within a session: the same source value
always produces the same pseudonym. The database is stored in .db-anonymized/
at the project root and purged automatically based on the configured mode
(ephemeral or session).
"""

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    fake_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_hash ON mappings(session_id, value_hash);
"""

_MIGRATE_RENAME = """
ALTER TABLE mappings RENAME COLUMN real_value TO value_hash;
"""


class MappingStore:
    """SQLite store for pseudonymization mappings (real value → fake value).

    Real values are never stored in plain text - only their SHA-256 hash is persisted.
    """

    def __init__(self, db_path: str = ".db-anonymized/mappings.db"):
        """Initialize the SQLite store and create/migrate the schema if needed.

        Args:
            db_path: Path to the SQLite file (supports ~ for home directory).
        """
        resolved = os.path.expanduser(db_path)
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(resolved, check_same_thread=False)
        self._migrate()
        self._conn.executescript(_SCHEMA)

    def _migrate(self):
        """Migrate schema if the old 'real_value' column exists (drops and recreates the table).

        Returns:
            None
        """
        cursor = self._conn.execute("PRAGMA table_info(mappings)")
        cols = {row[1] for row in cursor.fetchall()}
        if "real_value" in cols:
            self._conn.execute("DROP TABLE mappings")
            self._conn.commit()

    @staticmethod
    def _hash(session_id: str, real_value: str) -> str:
        """Compute the SHA-256 hash of a real value for secure storage.

        Args:
            session_id: Session identifier.
            real_value: Real value to hash.

        Returns:
            SHA-256 hex digest of '{session_id}:{real_value}'.
        """
        return hashlib.sha256(f"{session_id}:{real_value}".encode()).hexdigest()

    def get(self, session_id: str, real_value: str) -> str | None:
        """Look up the pseudonymized value corresponding to a real value.

        Args:
            session_id: Session identifier.
            real_value: Real value to look up.

        Returns:
            The fake value if cached, None otherwise.
        """
        h = self._hash(session_id, real_value)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT fake_value FROM mappings WHERE session_id = ? AND value_hash = ?",
                (session_id, h),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def put(self, session_id: str, entity_type: str, real_value: str, fake_value: str):
        """Store a new real value → fake value mapping.

        The insert is ignored if the mapping already exists (INSERT OR IGNORE).

        Args:
            session_id: Session identifier.
            entity_type: PII type (e.g. 'EMAIL', 'PERSON').
            real_value: Real value (will be hashed for storage).
            fake_value: Generated fake value.

        Returns:
            None
        """
        h = self._hash(session_id, real_value)
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO mappings (session_id, entity_type, value_hash, fake_value) "
                "VALUES (?, ?, ?, ?)",
                (session_id, entity_type, h, fake_value),
            )
            self._conn.commit()

    def clear_session(self, session_id: str):
        """Delete all mappings for a session.

        Args:
            session_id: Session identifier to clean up.

        Returns:
            None
        """
        with self._lock:
            self._conn.execute("DELETE FROM mappings WHERE session_id = ?", (session_id,))
            self._conn.commit()

    def purge(self):
        """Delete all mappings from all sessions."""
        with self._lock:
            self._conn.execute("DELETE FROM mappings")
            self._conn.commit()

    def close(self):
        """Close the SQLite connection.

        Returns:
            None
        """
        self._conn.close()
