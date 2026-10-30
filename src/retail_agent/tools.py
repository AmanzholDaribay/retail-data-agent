from langchain_core.tools import BaseTool, tool

from retail_agent.db.base import QueryResult
from retail_agent.gateway import QueryGateway


def make_run_sql(gateway: QueryGateway) -> BaseTool:
    @tool
    def run_sql(sql: str) -> str:
        """Run one SELECT statement against the retail database and return the rows as a table."""
        return format_table(gateway.run(sql))

    return run_sql


def format_table(result: QueryResult) -> str:
    lines = [" | ".join(result.columns)]
    lines += [" | ".join(str(value) for value in row) for row in result.rows]
    return "\n".join(lines)
