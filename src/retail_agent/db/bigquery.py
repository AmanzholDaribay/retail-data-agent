from google.api_core.exceptions import GoogleAPICallError
from google.cloud import bigquery

from retail_agent.db.base import QueryError, QueryResult

# Only obvious infrastructure and setup errors are kept from the model, since rewriting the query can't fix them.
# Everything else goes back to the model: SQL mistakes are open-ended, and the LLM call budget caps the cost of a
# wrong guess. Matched on BigQuery's reason, not the exception class: the client raises reasons it doesn't know,
# such as bytesBilledLimitExceeded, as a 500.
_INFRASTRUCTURE_REASONS = {
    # Temporary: worth retrying later (step 4).
    "backendError",
    "internalError",
    "rateLimitExceeded",
    "tableUnavailable",
    "stopped",
    # Setup: access, billing and quota.
    "accessDenied",
    "billingNotEnabled",
    "quotaExceeded",
    "blocked",
    "policyViolation",
}
# Without a reason, only the HTTP status is known: auth, rate limits and server errors (5xx) are infrastructure.
_INFRASTRUCTURE_STATUSES = {401, 403, 429}


def as_query_error(error: GoogleAPICallError) -> QueryError | None:
    """The QueryError to send back to the model, or None for an infrastructure error the model can't fix."""
    detail = error.errors[0] if error.errors else {}
    if _is_infrastructure(error, detail.get("reason")):
        return None
    # BigQuery's own message only: error.message repeats it and can start with the request URL.
    return QueryError(detail.get("message") or error.message)


def _is_infrastructure(error: GoogleAPICallError, reason: str | None) -> bool:
    if reason is not None:
        return reason in _INFRASTRUCTURE_REASONS
    return error.code is not None and (error.code >= 500 or error.code in _INFRASTRUCTURE_STATUSES)


class BigQueryDatabase:
    """Runs queries on BigQuery, billed to one project and capped by bytes billed."""

    dialect = "bigquery"

    def __init__(self, project: str, max_bytes_billed: int) -> None:
        self._client = bigquery.Client(project=project)
        self._job_config = bigquery.QueryJobConfig(maximum_bytes_billed=max_bytes_billed)

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        try:
            job = self._client.query(sql, job_config=self._job_config)
            result = job.result(max_results=max_rows)
        except GoogleAPICallError as error:
            query_error = as_query_error(error)
            if query_error is None:
                raise
            raise query_error from error
        rows = [tuple(row.values()) for row in result]
        return QueryResult(
            columns=[field.name for field in result.schema],
            rows=rows,
            truncated=(result.total_rows or 0) > len(rows),
            bytes_processed=job.total_bytes_processed,
        )
