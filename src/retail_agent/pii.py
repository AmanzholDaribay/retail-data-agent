import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify

from retail_agent.guard import QueryRejected
from retail_agent.schema import Schema


class PersonalDataCheck:
    """Checks a query before it runs: it reads only the columns listed in `schema.yaml`.

    Personal columns (names, email, street address, postal code, latitude/longitude) are left out of
    `schema.yaml`, so they can't be read. This is an allow-list: a column nobody listed is blocked too.
    """

    def __init__(self, dialect: str, schema: Schema) -> None:
        self._dialect = dialect
        project, dataset = schema.dataset.split(".")
        # The nested shape sqlglot expects: {project: {dataset: {table: {column: type}}}}.
        self._columns = {
            project: {dataset: {table.name: {column.name: column.type for column in table.columns} for table in schema.tables}}
        }

    def check(self, sql: str) -> None:
        """Raise QueryRejected unless the query passes every rule, in this order."""
        statement = sqlglot.parse_one(sql, read=self._dialect)
        self._require_schema_columns(statement)  # also marks whole rows, so it goes first
        _reject_select_star(statement)
        _reject_whole_rows(statement)

    def _require_schema_columns(self, statement: exp.Expr) -> None:
        """Every column traces through aliases, joins, subqueries and WITH queries back to a schema column.

        Stars stay as written (`expand_stars=False`), because BigQuery runs the query as written.
        """
        try:
            qualify(statement, schema=self._columns, dialect=self._dialect, expand_stars=False)
        except OptimizeError as error:
            raise QueryRejected(
                "Read only the columns listed in the schema. "
                "When a column is in two joined tables, prefix it with the table alias. "
                f"({error})"
            ) from error


def _reject_select_star(statement: exp.Expr) -> None:
    """`*` would return every column, personal ones too. Only COUNT(*) is allowed."""
    for star in statement.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise QueryRejected("Name the columns you need instead of using *. COUNT(*) is fine.")


def _reject_whole_rows(statement: exp.Expr) -> None:
    """A table used as a value, e.g. `TO_JSON_STRING(u)`, carries every column of the row."""
    for row in statement.find_all(exp.TableColumn):
        raise QueryRejected(f"Select columns, not whole rows: `{row.name}` is a table, not a column.")
