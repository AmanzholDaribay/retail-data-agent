from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    total_rows: int | None = None  # rows the query produced, before the row cap; None when unknown
    bytes_processed: int | None = None
    sql: str | None = None  # the SQL that ran, after the gateway rewrote it; set by the gateway

    @property
    def truncated(self) -> bool:
        """Whether rows were cut off. Derived from total_rows, so the two can't disagree."""
        return self.total_rows is not None and self.total_rows > len(self.rows)


class QueryError(Exception):
    """The engine rejected a query, e.g. bad syntax or an unknown column. The message is meant for the model."""


class Database(Protocol):
    """A SQL engine the agent can query. One adapter per engine."""

    dialect: str

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        """Run one query and return at most `max_rows` rows. Raises QueryError if the engine rejects it."""
        ...

    def estimate_bytes(self, sql: str) -> int:
        """The bytes the query would scan, found without running it. Raises QueryError if the engine rejects it."""
        ...
