from google.cloud import bigquery

from retail_agent.db.base import QueryResult


class BigQueryDatabase:
    """Runs queries on BigQuery, billed to one project and capped by bytes billed."""

    dialect = "bigquery"

    def __init__(self, project: str, max_bytes_billed: int) -> None:
        self._client = bigquery.Client(project=project)
        self._job_config = bigquery.QueryJobConfig(maximum_bytes_billed=max_bytes_billed)

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        job = self._client.query(sql, job_config=self._job_config)
        result = job.result(max_results=max_rows)
        rows = [tuple(row.values()) for row in result]
        return QueryResult(
            columns=[field.name for field in result.schema],
            rows=rows,
            truncated=(result.total_rows or 0) > len(rows),
            bytes_processed=job.total_bytes_processed,
        )
