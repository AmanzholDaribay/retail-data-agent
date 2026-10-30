from retail_agent.db.base import Database, QueryResult


class QueryGateway:
    """The only path from the agent to the database.

    Query validation, brand scoping and PII masking plug in here in later steps.
    For now it passes queries straight through with a row cap.
    """

    def __init__(self, database: Database, max_rows: int) -> None:
        self._database = database
        self._max_rows = max_rows

    def run(self, sql: str) -> QueryResult:
        return self._database.execute(sql, self._max_rows)
