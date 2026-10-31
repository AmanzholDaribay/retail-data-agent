import logging
from typing import Any

from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import ORDER_ITEMS, assemble_app, tool_output_seen_by_model
from langchain_core.messages import AIMessage

from retail_agent.db.base import QueryResult


def model_output_for(sql: str, database: FakeDatabase | None = None, **settings: Any) -> str:
    """What run_sql sends back to the model for this SQL."""
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Done.")])
    assemble_app(model, database or FakeDatabase([]), **settings).ask("Anything")
    return tool_output_seen_by_model(model)


def test_a_statement_that_changes_data_is_rejected_before_it_reaches_the_database():
    database = FakeDatabase([])

    output = model_output_for(f"DELETE FROM {ORDER_ITEMS} WHERE TRUE", database)

    assert output == "Error: Only SELECT queries are allowed."
    assert database.executed == []


def test_a_statement_that_changes_a_table_is_rejected():
    database = FakeDatabase([])

    output = model_output_for(f"DROP TABLE {ORDER_ITEMS}", database)

    assert output == "Error: Only SELECT queries are allowed."
    assert database.executed == []


def test_sql_the_guard_cannot_parse_goes_back_to_the_model():
    database = FakeDatabase([])

    output = model_output_for("SELECT FROM WHERE", database)

    assert output.startswith("Error: The query could not be parsed")
    assert database.executed == []


def test_more_than_one_statement_is_rejected():
    database = FakeDatabase([])

    output = model_output_for(f"SELECT 1 FROM {ORDER_ITEMS}; DROP TABLE {ORDER_ITEMS}", database)

    assert output == "Error: Send one statement per query."
    assert database.executed == []


def test_a_table_outside_the_schema_is_rejected():
    database = FakeDatabase([])

    output = model_output_for("SELECT ip_address FROM `bigquery-public-data.thelook_ecommerce.events`", database)

    assert output == (
        "Error: Table `bigquery-public-data.thelook_ecommerce.events` is not allowed. Use only these tables: "
        "`bigquery-public-data.thelook_ecommerce.orders`, `bigquery-public-data.thelook_ecommerce.order_items`, "
        "`bigquery-public-data.thelook_ecommerce.products`, `bigquery-public-data.thelook_ecommerce.users`."
    )
    assert database.executed == []


def test_a_name_defined_with_with_is_not_mistaken_for_a_table():
    sql = (
        f"WITH monthly AS (SELECT DATE_TRUNC(DATE(created_at), MONTH) AS month, sale_price FROM {ORDER_ITEMS}) "
        "SELECT month, SUM(sale_price) FROM monthly GROUP BY month"
    )
    database = FakeDatabase([QueryResult(columns=["month", "revenue"], rows=[("2026-09-01", 100.0)])])

    model_output_for(sql, database)

    assert database.executed == [sql]


def test_a_table_function_is_rejected():
    database = FakeDatabase([])

    output = model_output_for("SELECT * FROM EXTERNAL_QUERY('my-connection', 'SELECT * FROM customers')", database)

    assert output == "Error: Read only from tables. Table functions such as EXTERNAL_QUERY are not allowed."
    assert database.executed == []


def test_a_query_that_would_scan_more_than_the_byte_limit_is_rejected_before_it_runs():
    database = FakeDatabase([], estimated_bytes=2_500_000_000)

    output = model_output_for(f"SELECT * FROM {ORDER_ITEMS}", database)

    assert output == (
        "Error: This query would scan 2.5 GB, over the 1.0 GB limit per query. "
        "Select fewer columns or filter on a narrower date range."
    )
    assert database.executed == []


def test_a_rejected_query_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="retail_agent"):
        model_output_for(f"DELETE FROM {ORDER_ITEMS} WHERE TRUE")

    assert "SQL rejected: Only SELECT queries are allowed." in caplog.text


def test_the_byte_limit_message_uses_a_unit_that_fits_the_sizes():
    database = FakeDatabase([], estimated_bytes=31_500_000)

    output = model_output_for(f"SELECT * FROM {ORDER_ITEMS}", database, max_bytes_billed=5_000_000)

    assert output.startswith("Error: This query would scan 31.5 MB, over the 5.0 MB limit")


def test_a_with_name_that_copies_a_table_path_cannot_hide_that_table():
    # BigQuery accepts this: the WITH name only covers the inner query, so the JOIN reads the real events table.
    events = "`bigquery-public-data.thelook_ecommerce.events`"
    database = FakeDatabase([])

    output = model_output_for(f"SELECT * FROM (WITH {events} AS (SELECT 1 AS a) SELECT a FROM {events}) JOIN {events} ON TRUE", database)

    assert output.startswith("Error: Table `bigquery-public-data.thelook_ecommerce.events` is not allowed.")
    assert database.executed == []


def test_a_table_function_without_a_plain_name_is_named_in_the_message():
    output = model_output_for("SELECT * FROM ML.PREDICT(MODEL `my-project.models.churn`, (SELECT 1 AS x))")

    assert output == "Error: Read only from tables. Table functions such as PREDICT are not allowed."
