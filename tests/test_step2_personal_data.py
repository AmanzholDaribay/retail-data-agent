from typing import Any

import pytest
from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import ORDER_ITEMS, ORDERS, PRODUCTS, USERS, assemble_app, tool_output_seen_by_model
from langchain_core.messages import AIMessage

from retail_agent.db.base import QueryResult


def model_output_for(sql: str, database: FakeDatabase, **settings: Any) -> str:
    """What run_sql sends back to the model for this SQL."""
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Done.")])
    assemble_app(model, database, **settings).ask("Anything")
    return tool_output_seen_by_model(model)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT age, city FROM {USERS}",
        f"SELECT u.country, COUNT(*) AS customers FROM {USERS} u JOIN {ORDERS} o ON u.id = o.user_id GROUP BY u.country",
        f"WITH monthly AS (SELECT DATE_TRUNC(DATE(created_at), MONTH) AS month, sale_price FROM {ORDER_ITEMS}) "
        "SELECT month, SUM(sale_price) FROM monthly GROUP BY month",
        f"SELECT COUNT(*) FROM {USERS}",
        f"SELECT u.gender, COUNT(*) FROM {USERS} u JOIN {ORDERS} o ON u.id = o.user_id GROUP BY u.gender",
        f"SELECT p.brand, SUM(oi.sale_price) AS revenue FROM {ORDER_ITEMS} oi JOIN {PRODUCTS} p ON oi.product_id = p.id "
        "WHERE oi.status NOT IN ('Cancelled', 'Returned') GROUP BY p.brand ORDER BY revenue DESC LIMIT 5",
    ],
)
def test_a_query_that_reads_only_schema_columns_runs(sql):
    database = FakeDatabase([QueryResult(columns=["x"], rows=[(1,)])])

    model_output_for(sql, database)

    assert len(database.executed) == 1  # it reached the database (rewritten: customer ids become pseudonyms)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT email FROM {USERS}",
        f"SELECT first_name, last_name FROM {USERS}",
        f"SELECT u.street_address FROM {USERS} u",
        f"SELECT ROUND(latitude, 2) FROM {USERS}",
        f"SELECT x FROM (SELECT postal_code AS x FROM {USERS})",
        f"WITH contacts AS (SELECT email AS e FROM {USERS}) SELECT e FROM contacts",
        f"SELECT o.order_id FROM {ORDERS} o JOIN {USERS} u ON u.id = o.user_id WHERE u.email LIKE '%@gmail.com'",
    ],
)
def test_a_column_outside_the_schema_is_rejected_before_it_reaches_the_database(sql):
    database = FakeDatabase([])

    output = model_output_for(sql, database)

    assert output.startswith("Error: Read only the columns listed in the schema.")
    assert database.executed == []


def test_a_column_in_two_joined_tables_must_name_its_table():
    database = FakeDatabase([])

    output = model_output_for(f"SELECT gender FROM {USERS} u JOIN {ORDERS} o ON u.id = o.user_id", database)

    assert output.startswith("Error: Read only the columns listed in the schema.")
    assert database.executed == []


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM {USERS}",
        f"SELECT u.* FROM {USERS} u",
        f"SELECT * EXCEPT (id) FROM {USERS}",
        f"SELECT AS STRUCT * FROM {USERS}",
    ],
)
def test_select_star_is_rejected(sql):
    database = FakeDatabase([])

    output = model_output_for(sql, database)

    assert output == "Error: Name the columns you need instead of using *. COUNT(*) is fine."
    assert database.executed == []


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT u FROM {USERS} u",
        f"SELECT TO_JSON_STRING(u) FROM {USERS} u",
        f"SELECT ARRAY_AGG(users) FROM {USERS}",
    ],
)
def test_a_whole_row_used_as_a_value_is_rejected(sql):
    database = FakeDatabase([])

    output = model_output_for(sql, database)

    assert output.startswith("Error: Select columns, not whole rows:")
    assert database.executed == []
