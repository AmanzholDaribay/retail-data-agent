import logging
import time

from retail_agent.db.base import Database, QueryResult
from retail_agent.guard import QueryGuard, QueryRejected
from retail_agent.users import User

logger = logging.getLogger(__name__)


class QueryGateway:
    """The only path from the agent to the database.

    Every query passes the guard before it reaches the database, and results are capped at `max_rows`.
    Brand scoping and PII masking plug in here in later steps.
    """

    def __init__(self, database: Database, guard: QueryGuard, max_rows: int, max_bytes: int, user: User) -> None:
        self._database = database
        self._guard = guard
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        # Whose question this is. Brand scoping (step 3) limits every query to user.brands.
        self._user = user

    def run(self, sql: str) -> QueryResult:
        logger.info("SQL: %s", sql)
        started = time.monotonic()
        try:
            self._guard.check(sql)
            self._check_bytes(sql)
            result = self._database.execute(sql, self._max_rows)
        except QueryRejected as error:
            logger.warning("SQL rejected: %s", error)
            raise
        except Exception as error:
            logger.warning("SQL failed after %.1fs: %s", time.monotonic() - started, error)
            raise
        logger.info(
            "SQL finished in %.1fs: %d rows, %s bytes processed",
            time.monotonic() - started,
            len(result.rows),
            result.bytes_processed,
        )
        return result

    def _check_bytes(self, sql: str) -> None:
        """Reject a query that would scan more than `max_bytes`, before it costs anything."""
        estimated = self._database.estimate_bytes(sql)
        if estimated > self._max_bytes:
            raise QueryRejected(
                f"This query would scan {_format_bytes(estimated)}, over the {_format_bytes(self._max_bytes)} limit per query. "
                "Select fewer columns or filter on a narrower date range."
            )


def _format_bytes(size: int) -> str:
    """A byte count in the largest unit that keeps it at 1 or more, e.g. 31.5 MB."""
    for unit, scale in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} bytes"
