import logging
import time

from retail_agent.db.base import Database, QueryResult

logger = logging.getLogger(__name__)


class QueryGateway:
    """The only path from the agent to the database.

    Query validation, brand scoping and PII masking plug in here in later steps.
    For now it passes queries straight through with a row cap.
    """

    def __init__(self, database: Database, max_rows: int) -> None:
        self._database = database
        self._max_rows = max_rows

    def run(self, sql: str) -> QueryResult:
        logger.info("SQL: %s", sql)
        started = time.monotonic()
        try:
            result = self._database.execute(sql, self._max_rows)
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
