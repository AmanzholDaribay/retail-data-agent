import logging
from pathlib import Path

import pytest
from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from retail_agent.app import App, assemble
from retail_agent.config import Settings
from retail_agent.db.base import QueryError, QueryResult
from retail_agent.schema import load_schema

ROOT = Path(__file__).parents[1]


def assemble_app(model: ScriptedChatModel, database: FakeDatabase) -> App:
    """The real schema file and prompt template, with a fake model and database."""
    return assemble(
        model=model,
        database=database,
        schema=load_schema(ROOT / "schema.yaml"),
        prompt_template=(ROOT / "prompts" / "system.md").read_text(),
        settings=Settings(gcp_project="test-project"),
    )


def test_answers_a_question_with_the_sql_it_ran():
    sql = "SELECT brand, SUM(sale_price) AS revenue FROM order_items GROUP BY brand"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Brand A leads with $100.")])
    database = FakeDatabase([QueryResult(columns=["brand", "revenue"], rows=[("Brand A", 100.0)])])

    result = assemble_app(model, database).ask("Which brand has the most revenue?")

    assert result.answer == "Brand A leads with $100."
    assert result.sql == [sql]
    assert database.executed == [sql]


def test_a_failed_query_goes_back_to_the_model_which_fixes_it():
    bad_sql = "SELECT revenue FROM order_items"
    good_sql = "SELECT SUM(sale_price) AS revenue FROM order_items"
    model = ScriptedChatModel(
        script=[run_sql_call(bad_sql), run_sql_call(good_sql), AIMessage("Revenue is $100.")]
    )
    database = FakeDatabase(
        [
            QueryError("Unrecognized name: revenue at [1:8]"),
            QueryResult(columns=["revenue"], rows=[(100.0,)]),
        ]
    )

    result = assemble_app(model, database).ask("What is our revenue?")

    error_seen_by_model = model.received[1][-1]
    assert isinstance(error_seen_by_model, ToolMessage)
    assert error_seen_by_model.text == "Error: Unrecognized name: revenue at [1:8]"
    assert result.answer == "Revenue is $100."
    assert result.sql == [bad_sql, good_sql]
    assert database.executed == [bad_sql, good_sql]


def test_a_model_that_never_stops_ends_at_the_call_budget():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT {n}") for n in range(1, 7)])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(n,)]) for n in range(1, 7)])

    result = assemble_app(model, database).ask("Count forever")

    assert len(model.received) == 5
    assert result.sql == [f"SELECT {n}" for n in range(1, 6)]
    assert result.answer == (
        "I couldn't finish this question within 5 model calls. Try asking it in a simpler or more specific way."
    )


def test_an_answer_on_the_last_allowed_call_is_returned():
    script = [run_sql_call(f"SELECT {n}") for n in range(1, 5)] + [AIMessage("Done after four queries.")]
    model = ScriptedChatModel(script=script)
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(n,)]) for n in range(1, 5)])

    result = assemble_app(model, database).ask("Count to four")

    assert len(model.received) == 5
    assert result.answer == "Done after four queries."


def test_model_sees_the_schema_and_dialect_in_its_system_message():
    model = ScriptedChatModel(script=[AIMessage("Hello.")])

    assemble_app(model, FakeDatabase([])).ask("Hi")

    system_message = model.received[0][0]
    assert isinstance(system_message, SystemMessage)
    assert "fakesql SQL" in system_message.text
    for table in ["orders", "order_items", "products", "users"]:
        assert f"`bigquery-public-data.thelook_ecommerce.{table}`" in system_message.text


def test_logs_the_question_each_llm_call_and_the_sql(caplog):
    sql = "SELECT 1"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("One.")])
    database = FakeDatabase([QueryResult(columns=["x"], rows=[(1,)], bytes_processed=10)])

    with caplog.at_level(logging.INFO, logger="retail_agent"):
        assemble_app(model, database).ask("What is one?")

    log = caplog.text
    assert "Question: What is one?" in log
    assert log.count("LLM call started") == 2
    assert log.count("LLM call finished") == 2
    assert f"SQL: {sql}" in log
    assert "1 rows, 10 bytes processed" in log


def test_a_failing_model_fails_the_turn_and_is_logged(caplog):
    model = ScriptedChatModel(script=[RuntimeError("503 UNAVAILABLE: model is busy")])

    with caplog.at_level(logging.INFO, logger="retail_agent"), pytest.raises(RuntimeError, match="503"):
        assemble_app(model, FakeDatabase([])).ask("Anything")

    assert "LLM call failed" in caplog.text
    assert "503 UNAVAILABLE" in caplog.text
