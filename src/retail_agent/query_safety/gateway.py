"""The main file: the one path from the agent to the database. Read `QueryGateway.run` top to bottom.

    SQL ──▶ Step 1 guard ──▶ Step 2 personal data ──▶ Step 3 brand scope ──▶ Step 4 size ──▶ BigQuery
               │                  │                        │                     │
               └──────────────────┴──── any step can stop it with QueryRejected ─┘

A stopped query never reaches the database. Its message goes back to the model, so it can fix the SQL and try again.
"""

import dataclasses
import logging
import time
from typing import Any

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from retail_agent.db.base import Database, QueryResult
from retail_agent.query_safety.step1_guard import QueryGuard, QueryRejected
from retail_agent.query_safety.step2_personal_data.columns import PersonalDataCheck
from retail_agent.query_safety.step2_personal_data.customer_pseudonyms import CustomerPseudonyms
from retail_agent.query_safety.step2_personal_data.small_groups import SmallGroups
from retail_agent.query_safety.step3_brand_scope.brand_scope import BrandScope

logger = logging.getLogger(__name__)


class QueryGateway:
    """The only path from the agent to the database.

    Every query passes the guard, reads only schema columns (so no personal data), uses demographic columns only
    in totals over enough customers, sees customer ids only as pseudonyms, is limited to the user's brands, and has its size checked before it reaches the database.
    Results are capped at `max_rows`.
    """

    def __init__(
        self,
        database: Database,
        guard: QueryGuard,
        personal_data: PersonalDataCheck,
        small_groups: SmallGroups,
        pseudonyms: CustomerPseudonyms,
        max_rows: int,
        max_bytes: int,
        scope: BrandScope,
    ) -> None:
        self._database = database
        self._guard = guard
        self._personal_data = personal_data
        self._small_groups = small_groups
        self._pseudonyms = pseudonyms
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._scope = scope

    # A step of its own in a LangSmith trace (when tracing is on), under the run_sql tool call.
    @traceable(name="query_gateway")
    def run(self, sql: str) -> QueryResult:
        logger.info("SQL: %s", sql)
        started = time.monotonic()
        try:
            # Step 1: the guard. One SELECT that reads only the allowed tables.  -> step1_guard.py
            self._guard.check(sql)
            # Step 2: personal data. Only columns listed in schema.yaml, no *.   -> step2_personal_data/columns.py
            self._personal_data.check(sql)
            #   ...demographic columns only in totals of min_group_size+ customers.  -> step2_personal_data/small_groups.py
            grouped_sql = self._small_groups.apply(sql)
            #   ...and customer ids become pseudonyms, for every user.  -> step2_personal_data/customer_pseudonyms.py
            pseudonymized_sql = self._pseudonyms.apply(grouped_sql)
            # Step 3: brand scope. Rewrite the SQL so the user sees only their brands.  -> step3_brand_scope/brand_scope.py
            scoped_sql = self._scope.apply(pseudonymized_sql)
            if scoped_sql != sql:
                logger.info("Rewritten SQL: %s", scoped_sql)
            _add_to_trace(rewritten_sql=scoped_sql)  # the SQL BigQuery gets, which the tool call alone doesn't show
            # Step 4: size. Stop the query before it scans more than max_bytes.  -> _check_bytes below
            self._check_bytes(scoped_sql)
            # Every step passed: run the rewritten query, keeping at most max_rows rows.
            # When step 2 added the small-group HAVING, the result says so: missing rows are hidden, not absent.
            hidden_below = self._small_groups.min_group_size if grouped_sql != sql else None
            result = dataclasses.replace(
                self._database.execute(scoped_sql, self._max_rows), sql=scoped_sql, small_groups_hidden_below=hidden_below
            )
        except QueryRejected as error:
            logger.warning("SQL rejected: %s", error)
            raise
        except Exception as error:
            logger.warning("SQL failed after %.1fs: %s", time.monotonic() - started, error)
            raise
        _add_to_trace(rows=len(result.rows), bytes_processed=result.bytes_processed)
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


def _add_to_trace(**metadata: Any) -> None:
    """Add metadata to this gateway step in the LangSmith trace. Does nothing when there is no trace."""
    run_tree = get_current_run_tree()
    if run_tree is not None:
        run_tree.add_metadata(metadata)


def _format_bytes(size: int) -> str:
    """A byte count in the largest unit that keeps it at 1 or more, e.g. 31.5 MB."""
    for unit, scale in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} bytes"
