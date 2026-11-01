"""Saved reports: a local SQLite file, one row per report.

Each row records who asked (the owner), in which conversation, when, the question, the SQL that ran and the report
text. The owner and conversation come from the code, never from the model, so a report can't be saved for someone
else. Listing and deleting (tickets 12 and 13) will filter on the owner in the SQL itself.

The file and its table are created on the first save, so a run without reports leaves no file behind.
"""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    sql TEXT NOT NULL,
    title TEXT NOT NULL,
    report TEXT NOT NULL
)
"""


class ReportStore:
    """Saves reports to a SQLite file at `path`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, owner: str, conversation_id: str, question: str, sql: list[str], title: str, report: str) -> int:
        """Save one report and return its id."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # closing() closes the file even if the insert fails; the inner `with` commits it, or rolls back.
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(_CREATE_TABLE)
            cursor = connection.execute(
                "INSERT INTO reports (owner, conversation_id, created_at, question, sql, title, report) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (owner, conversation_id, datetime.now(UTC).isoformat(), question, json.dumps(sql), title, report),
            )
        assert cursor.lastrowid is not None  # always set after an INSERT
        return cursor.lastrowid
