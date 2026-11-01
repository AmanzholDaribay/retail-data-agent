"""Step 2, part 2: customer pseudonyms. The model never sees a real customer id, only a nickname such as cust_72ac84091163.

Why: the dataset is public, so anyone holding a real `user_id` could look up that customer's name and email.

How: the same "swap the table" trick as brand scope. Each customer table the query reads is swapped for a copy
whose id column is already a nickname. BigQuery builds the copy itself while the query runs, from its own real ids:

    users.id             48213  ──▶  'cust_' + hash(salt + 48213)  ──▶  cust_72ac84091163
    orders.user_id       (same recipe, so joins between the tables still match)
    order_items.user_id
    products             no customer in it, left alone

Because the copy is built first, the model's SQL never touches a real id: `MAX(user_id)` or `user_id + 0` work on
nicknames too. Only the copies the query reads are added.

Follow-up questions work without any lookup table. The same salt and id always give the same nickname, so when the
model writes `WHERE user_id = 'cust_72ac84091163'`, BigQuery rebuilds the copy and compares nickname to nickname.

The salt is a secret (`PSEUDONYM_SALT` in .env). It reaches BigQuery as the query parameter @pseudonym_salt, never
inside the SQL text, so it isn't in the log. The guard (step 1) stops the model from reading parameters.

This runs for every user, the CEO too, and before brand scope (step 3). For a brand manager, step 3 then swaps the
real tables inside these copies for brand-filtered ones, so the nicknames are made from brand-filtered rows.
"""

import sqlglot
from sqlglot import exp

from retail_agent.query_safety.step1_guard import is_with_name, qualified_name

# Our copies get this prefix, so they can't be confused with WITH names the model writes.
PSEUDONYM_PREFIX = "_pseudonymized_"
# The query parameter that carries the salt. The database adapter sends its value with every query.
SALT_PARAMETER = "pseudonym_salt"

# The customer id column of each table that has one.
_ID_COLUMNS = {"users": "id", "orders": "user_id", "order_items": "user_id"}
# 12 hex characters keep tokens low; for about 100,000 customers, two sharing a nickname is about 1 in 50,000.
_PSEUDONYM = "CONCAT('cust_', SUBSTR(TO_HEX(SHA256(CONCAT(@" + SALT_PARAMETER + ", CAST({column} AS STRING)))), 1, 12))"


class CustomerPseudonyms:
    """Swaps every customer table the query reads for a copy whose customer id is a salted hash."""

    def __init__(self, dialect: str, dataset: str) -> None:
        self._dialect = dialect
        self._dataset = dataset

    def apply(self, sql: str) -> str:
        """The query, reading customer ids only as pseudonyms. The same string when it reads no customer table."""
        statement = sqlglot.parse_one(sql, read=self._dialect)
        names_read = []
        for table in list(statement.find_all(exp.Table)):
            if is_with_name(table, statement):
                continue
            name = qualified_name(table).removeprefix(f"{self._dataset}.")
            if name not in _ID_COLUMNS:
                continue
            # Keep the old name as the alias, so `users.id` in the model's SQL still resolves.
            alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(name))
            table.replace(exp.Table(this=exp.to_identifier(PSEUDONYM_PREFIX + name), alias=alias))
            if name not in names_read:
                names_read.append(name)
        if not names_read:
            return sql
        self._add_hashed_tables_to_with_clause(statement, names_read)
        return statement.sql(dialect=self._dialect)

    def _add_hashed_tables_to_with_clause(self, statement: exp.Expr, names: list[str]) -> None:
        """Put the hashed copies first in the query's WITH clause, so the model's own WITH queries can read them."""
        hashed_tables = [
            exp.CTE(
                this=self._build_hashed_table_query(name),
                alias=exp.TableAlias(this=exp.to_identifier(PSEUDONYM_PREFIX + name)),
            )
            for name in names
        ]
        existing = statement.args.get("with_")
        statement.set(
            "with_",
            exp.With(
                expressions=hashed_tables + (existing.expressions if existing else []),
                recursive=existing.args.get("recursive") if existing else None,
            ),
        )

    def _build_hashed_table_query(self, name: str) -> exp.Expr:
        """E.g. for users: SELECT * REPLACE (CONCAT('cust_', ...) AS id) FROM `...users` AS users."""
        column = _ID_COLUMNS[name]
        pseudonym = _PSEUDONYM.format(column=column)
        return sqlglot.parse_one(
            f"SELECT * REPLACE ({pseudonym} AS {column}) FROM `{self._dataset}.{name}` AS {name}", read=self._dialect
        )
