"""Step 3 of 4: brand scope. The only step that changes the SQL.

Each real table in the query is swapped for a filtered copy that holds only the user's brands, so the model's
SQL can't see other brands' rows, whatever it wrote. Like handing someone a filing cabinet that holds only
their own folders: they count with the same words, but they can only count their own folders.

`BrandScope.apply(sql)` does it in 5 steps:

  1. A user who sees every brand (the CEO): return the SQL unchanged.
  2. Parse the SQL into a tree.
  3. Swap every real table for its filtered copy:  `...thelook_ecommerce.products`  ->  _brand_scoped_products
     (WITH names the model made are skipped; a table we can't filter stops the query.)
  4. Add the 4 filtered copies at the front of the query's WITH clause.
  5. Turn the tree back into SQL text.

For a Levi's manager, the model's query

    SELECT p.brand, SUM(oi.sale_price) FROM `...order_items` AS oi JOIN `...products` AS p ON oi.product_id = p.id ...

runs as

    WITH _brand_scoped_products AS (... WHERE brand IN ('Levi\\'s')), _brand_scoped_order_items AS (...), ...
    SELECT p.brand, SUM(oi.sale_price) FROM _brand_scoped_order_items AS oi JOIN _brand_scoped_products AS p ...

How the copies are filtered: only products has a brand, so the others follow IDs back to it, like a chain:

    products     WHERE brand IN (the user's brands)
       │ id
    order_items  WHERE product_id IN (my products)
       │ order_id                  │ user_id
    orders       (orders holding    users  (people who bought
                  my items)                 my items)

The copies use `*` although step 2 forbids it: step 2 checks the model's SQL, and these copies are our own.
The model's outer query can still name only schema.yaml columns.
"""

from collections.abc import Set

import sqlglot
from sqlglot import exp

from retail_agent.query_safety.step1_guard import QueryRejected, is_with_name, qualified_name
from retail_agent.query_safety.step3_order_item_count import orders_columns_with_scoped_item_count

# Our filtered tables get this prefix, so they can't be confused with WITH names the model writes.
# If the model uses one of these names anyway, BigQuery rejects the duplicate name: the query fails, it doesn't leak.
SCOPED_PREFIX = "_brand_scoped_"

# Which rows of each table a user may see. Only products has a brand; the other tables reach it through IDs.
# Each filter reads only scoped tables listed before it. Every query gets all four: BigQuery skips the ones it doesn't read.
_FILTERS = {
    "products": "brand IN ({brands})",
    "order_items": f"product_id IN (SELECT id FROM {SCOPED_PREFIX}products)",
    "orders": f"order_id IN (SELECT order_id FROM {SCOPED_PREFIX}order_items)",
    "users": f"id IN (SELECT user_id FROM {SCOPED_PREFIX}order_items)",
}
# Which columns each filtered table keeps: all of them, except that orders recounts num_of_item from the user's items.
_COLUMNS = {"orders": orders_columns_with_scoped_item_count(f"{SCOPED_PREFIX}order_items")}


class BrandScope:
    """Limits every query to the user's brands, whatever SQL the model wrote.

    Each table in the query is swapped for a filtered version of it, defined in a WITH clause added
    to the front of the query. The model's SQL is otherwise unchanged. A user with every brand (the CEO)
    gets the query as it was.
    """

    def __init__(self, dialect: str, dataset: str, brands: Set[str] | None) -> None:
        """`brands` is None for a user who sees every brand."""
        self._dialect = dialect
        self._dataset = dataset
        self._brands = brands

    def apply(self, sql: str) -> str:
        """The query, reading only the user's brands. Run it after the guard has checked `sql`."""
        if self._brands is None:
            return sql
        statement = sqlglot.parse_one(sql, read=self._dialect)
        for table in list(statement.find_all(exp.Table)):
            if is_with_name(table, statement):
                continue
            name = self._table_name_without_dataset(table)
            # Keep the old name as the alias, so `products.brand` in the model's SQL still resolves.
            alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(name))
            table.replace(exp.Table(this=exp.to_identifier(SCOPED_PREFIX + name), alias=alias))
        self._add_filtered_tables_to_with_clause(statement)
        return statement.sql(dialect=self._dialect)

    def _table_name_without_dataset(self, table: exp.Table) -> str:
        """`products` for `bigquery-public-data.thelook_ecommerce.products`. Fails closed on any other table."""
        name = qualified_name(table).removeprefix(f"{self._dataset}.")
        if name not in _FILTERS:
            raise QueryRejected(f"Table `{qualified_name(table)}` can't be limited to your brands, so the query was not run.")
        return name

    def _add_filtered_tables_to_with_clause(self, statement: exp.Expr) -> None:
        """Put the four filtered tables first in the query's WITH clause, so the model's own WITH queries can read them."""
        filtered_tables = [
            exp.CTE(
                this=self._build_filtered_table_query(name),
                alias=exp.TableAlias(this=exp.to_identifier(SCOPED_PREFIX + name)),
            )
            for name in _FILTERS
        ]
        existing = statement.args.get("with_")
        statement.set(
            "with_",
            exp.With(
                expressions=filtered_tables + (existing.expressions if existing else []),
                recursive=existing.args.get("recursive") if existing else None,
            ),
        )

    def _build_filtered_table_query(self, name: str) -> exp.Expr:
        """E.g. for products: SELECT * FROM `...products` AS products WHERE brand IN ('Levi\\'s')."""
        brands = ", ".join(exp.Literal.string(brand).sql(self._dialect) for brand in sorted(self._brands))
        columns = _COLUMNS.get(name, "*")
        condition = _FILTERS[name].format(brands=brands)
        return sqlglot.parse_one(
            f"SELECT {columns} FROM `{self._dataset}.{name}` AS {name} WHERE {condition}", read=self._dialect
        )

