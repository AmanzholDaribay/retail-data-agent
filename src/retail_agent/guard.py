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
        """Raise QueryRejected unless the query passes every rule, in this order."""
        statement = self._parse_one_statement(sql)
        _require_select(statement)
        for table in statement.find_all(exp.Table):
            self._reject_table_function(table)
            if is_with_name(table, statement):
                continue
            self._require_allowed_table(table)

    def _parse_one_statement(self, sql: str) -> exp.Expr:
        """The single statement in `sql`. Fails closed: a query we can't parse can't be checked."""
        try:
            statements = [statement for statement in sqlglot.parse(sql, read=self._dialect) if statement is not None]
        except sqlglot.ParseError as error:
            raise QueryRejected(f"The query could not be parsed, so it was not run. Simplify it. ({error})") from error
        if len(statements) != 1:
            raise QueryRejected("Send one statement per query.")
        return statements[0]

    def _reject_table_function(self, table: exp.Table) -> None:
        if not isinstance(table.this, exp.Identifier):
            # Known functions such as ML.PREDICT parse without a name, so fall back to their SQL keyword.
            function = table.this.name or table.this.sql(self._dialect).split("(")[0]
            raise QueryRejected(f"Read only from tables. Table functions such as {function} are not allowed.")

    def _require_allowed_table(self, table: exp.Table) -> None:
        name = qualified_name(table)
        if name not in self._allowed_tables:
            allowed = ", ".join(f"`{allowed_name}`" for allowed_name in self._allowed_tables)
            raise QueryRejected(f"Table `{name}` is not allowed. Use only these tables: {allowed}.")


def _require_select(statement: exp.Expr) -> None:
    """A query (SELECT, or a UNION of SELECTs); no DML, DDL or scripting."""
    if not isinstance(statement, exp.Query):
        raise QueryRejected("Only SELECT queries are allowed.")


def is_with_name(table: exp.Table, statement: exp.Expr) -> bool:
    """Whether `table` is a name the query defines with WITH, not a real table.

    Only one-part names count: a WITH name spelled like a table path only covers its own query,
    so the same path elsewhere in the statement reads the real table.
    """
    if table.catalog or table.db:
        return False
    return table.name in {cte.alias_or_name for cte in statement.find_all(exp.CTE)}


def qualified_name(table: exp.Table) -> str:
    return ".".join(part for part in (table.catalog, table.db, table.name) if part)
