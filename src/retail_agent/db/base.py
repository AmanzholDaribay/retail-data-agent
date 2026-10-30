from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool = False
    bytes_processed: int | None = None


class Database(Protocol):
    """A SQL engine the agent can query. One adapter per engine."""

    dialect: str

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        """Run one query and return at most `max_rows` rows."""
        ...
