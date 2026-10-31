from collections.abc import Sequence

import sqlglot
from sqlglot import exp

from retail_agent.db.base import QueryError


class QueryRejected(QueryError):
    """The gateway refused a query before it reached the database. The message is meant for the model."""


class QueryGuard:
    """Checks a query before it runs: one SELECT statement that reads only the allowed tables."""

    def __init__(self, dialect: str, allowed_tables: Sequence[str]) -> None:
        """`allowed_tables` are fully qualified names, e.g. `bigquery-public-data.thelook_ecommerce.orders`."""
        self._dialect = dialect
        self._allowed_tables = list(allowed_tables)

    def check(self, sql: str) -> None:
        """Raise QueryRejected unless the query is allowed."""
        try:
            statements = [statement for statement in sqlglot.parse(sql, read=self._dialect) if statement is not None]
        except sqlglot.ParseError as error:
            # Fail closed: a query we can't parse can't be checked.
            raise QueryRejected(f"The query could not be parsed, so it was not run. Simplify it. ({error})") from error
        if len(statements) != 1:
            raise QueryRejected("Send one statement per query.")
        statement = statements[0]
        if not isinstance(statement, exp.Query):
            raise QueryRejected("Only SELECT queries are allowed.")
        defined_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                # Known functions such as ML.PREDICT parse without a name, so fall back to their SQL keyword.
                function = table.this.name or table.this.sql(self._dialect).split("(")[0]
                raise QueryRejected(f"Read only from tables. Table functions such as {function} are not allowed.")
            name = ".".join(part for part in (table.catalog, table.db, table.name) if part)
            # A one-part name the query defines with WITH is not a table. Only one-part names: a WITH name
            # spelled like a table path only covers its own query, so the same path elsewhere is the real table.
            if not table.catalog and not table.db and name in defined_names:
                continue
            if name not in self._allowed_tables:
                allowed = ", ".join(f"`{allowed_name}`" for allowed_name in self._allowed_tables)
                raise QueryRejected(f"Table `{name}` is not allowed. Use only these tables: {allowed}.")
