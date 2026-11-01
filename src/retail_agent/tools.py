from langchain_core.tools import BaseTool, tool

from retail_agent.db.base import QueryError
from retail_agent.query_safety.gateway import QueryGateway
from retail_agent.result_format import format_result


def make_run_sql(gateway: QueryGateway) -> BaseTool:
    @tool
    def run_sql(sql: str) -> str:
        """Run one SELECT statement against the retail database and return the rows as a table."""
        try:
            result = gateway.run(sql)
        except QueryError as error:
            # The model reads the error and sends corrected SQL. Other exceptions are bugs and still raise.
            return f"Error: {error}"
        return format_result(result)

    return run_sql
