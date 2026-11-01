"""Step 1 of 4: the guard. It only reads the SQL and never changes it.

Like a librarian at the reading-room door, it checks 5 things in order and stops at the first one that fails:

  1. Exactly one statement.                          ❌ SELECT 1; DROP TABLE orders
  2. A SELECT (or a UNION of SELECTs), nothing else. ❌ DELETE FROM orders
  3. No query parameters or system variables.        ❌ SELECT @pseudonym_salt
  4. Real tables, not table functions.               ❌ FROM ML.PREDICT(...)
  5. Only the allowed tables.                        ❌ FROM other_project.secrets.salaries

Rule 3 matters because our own SQL sends a secret to BigQuery as a parameter (the salt behind customer
pseudonyms, step 2). If the model could write `SELECT @pseudonym_salt`, BigQuery would hand the secret back.

SQL that sqlglot can't parse is stopped too: a query we can't read is a query we can't check.

WITH names are skipped. In `WITH big_orders AS (SELECT ... FROM orders) SELECT ... FROM big_orders`,
`big_orders` is a temporary table the query makes for itself, not a real one. The real table inside it
(`orders`) is still checked. Only one-part names count, so a WITH name spelled like a full table path
can't hide that real table elsewhere in the query.
"""

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
        _reject_parameters(statement)
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


def _reject_parameters(statement: exp.Expr) -> None:
    """`@name`, `?` and `@@name` read values sent with the query, not data. The model has no use for them."""
    if statement.find(exp.Parameter, exp.Placeholder):
        raise QueryRejected(
            "Query parameters and system variables (@name, ?, @@name) are not allowed. Write values into the SQL."
        )


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
