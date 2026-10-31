import logging
import time

from retail_agent.brand_scope import BrandScope
from retail_agent.db.base import Database, QueryResult
from retail_agent.guard import QueryGuard, QueryRejected

logger = logging.getLogger(__name__)


class QueryGateway:
    """The only path from the agent to the database.

    Every query passes the guard, is limited to the user's brands, and has its size checked before it reaches
    the database. Results are capped at `max_rows`. PII masking plugs in here later in step 3.
    """

    def __init__(self, database: Database, guard: QueryGuard, max_rows: int, max_bytes: int, scope: BrandScope) -> None:
        self._database = database
        self._guard = guard
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._scope = scope

    def run(self, sql: str) -> QueryResult:
        logger.info("SQL: %s", sql)
        started = time.monotonic()
        try:
            self._guard.check(sql)
            scoped_sql = self._scope.apply(sql)
            if scoped_sql != sql:
                logger.info("Scoped SQL: %s", scoped_sql)
            self._check_bytes(scoped_sql)
            result = self._database.execute(scoped_sql, self._max_rows)
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
