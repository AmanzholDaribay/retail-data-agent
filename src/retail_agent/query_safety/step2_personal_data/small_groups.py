"""Step 2, part 3: small groups. Demographic columns reach the model only inside a total over enough customers.

Why: age, gender, city, state, country and traffic source say little about a crowd, but a "total" over one
customer is that customer: "average age of buyers in Tromsø: 27" with one buyer in Tromsø is one person's age.

How: those columns are tagged `aggregate_only: true` in schema.yaml. Every SELECT in the query (the main one,
subqueries, WITH steps) that reads a tagged column straight from a table is checked on its own:

    SELECT id, city FROM users                     ❌ a list, one row per customer: rejected
    SELECT city, AVG(age) FROM users GROUP BY city ✅ a total: gets HAVING COUNT(DISTINCT users.id) >= 5
    SELECT user_id, SUM(sale_price) ...            ✅ no tagged column: unchanged

The HAVING makes BigQuery drop every group under MIN_GROUP_SIZE customers before anything comes back. SQL applies
HAVING before LIMIT, so "top 3 countries" still gets 3 safe rows. It counts the customer id of the table the tagged
column comes from (`users.id`, `orders.user_id`), by the alias the query uses. ARRAY_AGG and STRING_AGG are
rejected in those totals: they give one row per group, but it holds every customer's value.

A WITH step that only passes `city` along for a later step to total is rejected too: the total has to be in the
SELECT that reads the table, because that's where the customers can still be counted.

Like `columns.py`, it uses sqlglot's `qualify` to find the table behind every column, but on a copy: `qualify`
also renames unnamed outputs (`_col_1`), and the model should get back the column names it wrote. The copy has
the same SELECTs in the same order, so the HAVING is added to the matching SELECT of the original.

Known limit: subtracting two allowed totals ("all countries" minus "all but Iceland") can still single out one
customer. Stopping that needs added noise (differential privacy); it's noted in the design doc.
"""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from retail_agent.query_safety.step1_guard import QueryRejected
from retail_agent.query_safety.step2_personal_data.customer_pseudonyms import ID_COLUMNS
from retail_agent.schema import Schema

class SmallGroups:
    """Lets tagged columns through only inside totals, and adds a HAVING that hides groups under `min_group_size`."""

    def __init__(self, dialect: str, schema: Schema, min_group_size: int) -> None:
        self._dialect = dialect
        self.min_group_size = min_group_size
        project, dataset = schema.dataset.split(".")
        # The nested shape sqlglot expects: {project: {dataset: {table: {column: type}}}}.
        self._columns = {
            project: {dataset: {table.name: {column.name: column.type for column in table.columns} for table in schema.tables}}
        }
        self._tagged = {
            (table.name, column.name) for table in schema.tables for column in table.columns if column.aggregate_only
        }

    def apply(self, sql: str) -> str:
        """The query with a HAVING on every total of a tagged column. The same string when it reads none."""
        statement = sqlglot.parse_one(sql, read=self._dialect)
        qualified = qualify(statement.copy(), schema=self._columns, dialect=self._dialect, expand_stars=False)
        original_selects = list(statement.find_all(exp.Select))
        qualified_selects = list(qualified.find_all(exp.Select))
        changed = False
        for scope in traverse_scope(qualified):
            tagged = self._tagged_columns_read(scope)
            if not tagged:
                continue
            first_column, table_alias, table_name = tagged[0]
            if not _is_total(scope.expression):
                raise QueryRejected(
                    f"`{table_name}.{first_column}` can be used only inside a total (COUNT, SUM, AVG ... with GROUP BY), "
                    "in the same SELECT that reads the table, not listed one row per customer. "
                    f"Groups with fewer than {self.min_group_size} customers are never shown."
                )
            if _keeps_every_row(scope.expression):
                raise QueryRejected(
                    f"`{table_name}.{first_column}` can't be collected with ARRAY_AGG or STRING_AGG: that lists every "
                    "customer's value. Use COUNT, SUM, AVG, MIN or MAX instead."
                )
            # By identity: two identical SELECTs (e.g. a UNION of the same total) are equal but each needs its HAVING.
            position = next(i for i, select in enumerate(qualified_selects) if select is scope.expression)
            select = original_selects[position]
            self._hide_small_groups(select, table_alias, table_name)
            changed = True
        return statement.sql(dialect=self._dialect) if changed else sql

    def _tagged_columns_read(self, scope: Scope) -> list[tuple[str, str, str]]:
        """(column, table alias, table name) for each tagged column this SELECT reads straight from a table."""
        found = []
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table) and (source.name, column.name) in self._tagged:
                found.append((column.name, column.table, source.name))
        return found

    def _hide_small_groups(self, select: exp.Select, table_alias: str, table_name: str) -> None:
        """Add HAVING COUNT(DISTINCT alias.id) >= min, ANDed with any HAVING the model wrote."""
        condition = sqlglot.parse_one(
            f"COUNT(DISTINCT {table_alias}.{ID_COLUMNS[table_name]}) >= {self.min_group_size}", read=self._dialect
        )
        existing = select.args.get("having")
        select.set("having", exp.Having(this=exp.and_(existing.this, condition) if existing else condition))


def _keeps_every_row(select: exp.Expr) -> bool:
    """ARRAY_AGG, STRING_AGG or ARRAY_CONCAT_AGG in this SELECT: one row per group, but holding every customer's value."""
    return any(
        aggregate.find_ancestor(exp.Select) is select
        for aggregate in select.find_all(exp.ArrayAgg, exp.GroupConcat, exp.ArrayConcatAgg)
    )


def _is_total(select: exp.Expr) -> bool:
    """A GROUP BY, or an aggregate function (outside a window) among the outputs: one row per group, not per customer."""
    if not isinstance(select, exp.Select):
        return False
    if select.args.get("group"):
        return True
    # `COUNT(*) OVER (...)` keeps one row per customer, and an aggregate in a subquery belongs to that subquery.
    return any(
        aggregate.find_ancestor(exp.Window, exp.Select) is select
        for output in select.expressions
        for aggregate in output.find_all(exp.AggFunc)
    )
