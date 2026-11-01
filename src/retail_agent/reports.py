"""Saved reports: a local SQLite file, one row per report.

Each row records who asked (the owner), in which conversation, when, the question, the SQL that ran and the report
text. The owner and conversation come from the code, never from the model, so a report can't be saved for someone
else. Reading them back (`find`) always filters on the owner in the SQL itself, and deleting (ticket 13) will reuse
the same selection, so it removes exactly what a list shows.

The file and its table are created on the first save, so a run without reports leaves no file behind.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SavedReport:
    """One saved report, as a list shows it."""

    id: int
    created_at: str
    conversation_id: str
    title: str


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

    def find(
        self, owner: str, mentioning: str | None = None, conversation_id: str | None = None
    ) -> list[SavedReport]:
        """`owner`'s reports, newest first. `mentioning` is text in the title, question or report, ignoring case."""
        if not self._path.exists():
            return []  # nothing saved yet; don't create the file just to read it
        query = "SELECT id, created_at, conversation_id, title FROM reports WHERE owner = ?"
        parameters: list[str] = [owner]
        if mentioning:
            # A plain "contains" with Python's casefold: no wildcards to escape, and case is ignored beyond ASCII
            # (SQLite's own LIKE and LOWER only fold A-Z).
            query += " AND instr(casefold(title || ' ' || question || ' ' || report), ?) > 0"
            parameters.append(mentioning.casefold())
        if conversation_id:
            query += " AND conversation_id = ?"
            parameters.append(conversation_id)
        with closing(sqlite3.connect(self._path)) as connection:
            connection.create_function("casefold", 1, str.casefold, deterministic=True)
            connection.execute(_CREATE_TABLE)  # a file without the table (e.g. an empty one) has no reports
            rows = connection.execute(query + " ORDER BY id DESC", parameters).fetchall()
        return [SavedReport(*row) for row in rows]
