import logging
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import ORDER_ITEMS, PRODUCTS, assemble_app, tool_output_seen_by_model
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from retail_agent.agent import LAST_CALL_NOTE
from retail_agent.app import SqlRun
from retail_agent.db.base import QueryError, QueryResult


def test_answers_a_question_with_the_sql_it_ran():
    sql = f"SELECT p.brand, SUM(oi.sale_price) AS revenue FROM {ORDER_ITEMS} oi JOIN {PRODUCTS} p ON p.id = oi.product_id GROUP BY p.brand"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Brand A leads with $100.")])
    database = FakeDatabase([QueryResult(columns=["brand", "revenue"], rows=[("Brand A", 100.0)])])

    result = assemble_app(model, database).ask("Which brand has the most revenue?")

    assert result.answer == "Brand A leads with $100."
    assert [run.sql for run in result.sql] == [sql]
    assert len(database.executed) == 1


def test_a_result_cut_off_at_max_rows_tells_the_model_how_many_rows_there_were():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT id FROM {ORDER_ITEMS}"), AIMessage("Partial answer.")])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(n,) for n in range(1, 121)])])

    assemble_app(model, database).ask("List every number")

    lines = tool_output_seen_by_model(model).splitlines()
    assert lines[0] == "n"
    assert lines[1:51] == [str(n) for n in range(1, 51)]
    assert lines[51:] == ["Note: only the first 50 rows are shown. The query returned 120 rows."]


def test_an_empty_result_tells_the_model_there_were_no_rows():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT brand FROM {PRODUCTS} WHERE 1 = 0"), AIMessage("Nothing.")])
    database = FakeDatabase([QueryResult(columns=["brand", "revenue"], rows=[])])

    assemble_app(model, database).ask("Revenue of a brand that doesn't exist?")

    assert tool_output_seen_by_model(model) == "The query returned no rows. Columns: brand, revenue"


def test_an_empty_result_can_be_corrected_with_another_query():
    wrong = f"SELECT COUNT(*) AS n FROM {PRODUCTS} WHERE brand = 'calvin klein'"
    fixed = f"SELECT COUNT(*) AS n FROM {PRODUCTS} WHERE LOWER(brand) = 'calvin klein'"
    model = ScriptedChatModel(script=[run_sql_call(wrong), run_sql_call(fixed), AIMessage("Calvin Klein has 12 products.")])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[]), QueryResult(columns=["n"], rows=[(12,)])])

    result = assemble_app(model, database).ask("How many Calvin Klein products are there?")

    assert [run.sql for run in result.sql] == [wrong, fixed]
    assert result.answer == "Calvin Klein has 12 products."


def test_a_question_about_the_data_itself_is_answered_without_sql():
    model = ScriptedChatModel(script=[AIMessage("There are orders, order items, products and users.")])
    database = FakeDatabase([])

    result = assemble_app(model, database).ask("What data do you have?")

    assert [run.sql for run in result.sql] == []
    assert database.executed == []


def test_dates_decimals_and_nulls_reach_the_model_as_plain_text():
    row = (
        date(2026, 9, 30),
        datetime(2026, 9, 30, 14, 5, tzinfo=timezone.utc),
        datetime(2026, 9, 30, 14, 5),
        Decimal("1E+3"),
        Decimal("1234.5000"),
        12.5,
        None,
    )
    model = ScriptedChatModel(script=[run_sql_call("SELECT 1"), AIMessage("Done.")])
    database = FakeDatabase([QueryResult(columns=["d", "ts", "dt", "big", "price", "f", "missing"], rows=[row])])

    assemble_app(model, database).ask("Show one row")

    assert tool_output_seen_by_model(model).splitlines()[1] == (
        "2026-09-30 | 2026-09-30 14:05:00 UTC | 2026-09-30 14:05:00 | 1000 | 1234.5000 | 12.5 | NULL"
    )


def test_a_failed_query_goes_back_to_the_model_which_fixes_it():
    bad_sql = f"SELECT SUM(sale_price) AS revenue FROM {ORDER_ITEMS} WHERE created_at > '2026-13-01'"
    good_sql = f"SELECT SUM(sale_price) AS revenue FROM {ORDER_ITEMS}"
    model = ScriptedChatModel(
        script=[run_sql_call(bad_sql), run_sql_call(good_sql), AIMessage("Revenue is $100.")]
    )
    database = FakeDatabase(
        [
            QueryError("Invalid timestamp: '2026-13-01'"),
            QueryResult(columns=["revenue"], rows=[(100.0,)]),
        ]
    )

    result = assemble_app(model, database).ask("What is our revenue?")

    error_seen_by_model = model.received[1][-1]
    assert isinstance(error_seen_by_model, ToolMessage)
    assert error_seen_by_model.text == "Error: Invalid timestamp: '2026-13-01'"
    assert result.answer == "Revenue is $100."
    assert [(run.sql, run.error) for run in result.sql] == [(bad_sql, "Invalid timestamp: '2026-13-01'"), (good_sql, None)]
    # The SQL BigQuery got is the gateway's rewrite: order_items is read with customer ids as pseudonyms.
    assert result.sql[1].sent_sql == database.executed[1]
    assert "@pseudonym_salt" in result.sql[1].sent_sql
    assert len(database.executed) == 2


def test_a_query_the_gateway_rejects_is_kept_with_the_reason():
    rejected = f"SELECT * FROM {PRODUCTS}"
    fixed = f"SELECT COUNT(id) AS n FROM {PRODUCTS}"
    model = ScriptedChatModel(script=[run_sql_call(rejected), run_sql_call(fixed), AIMessage("There are 12 products.")])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(12,)])])

    result = assemble_app(model, database).ask("How many products are there?")

    assert result.sql == [
        SqlRun(rejected, error="Name the columns you need instead of using *. COUNT(*) is fine."),
        SqlRun(fixed, error=None, sent_sql=fixed),
    ]
    assert len(database.executed) == 1  # the rejected query never reached the database


def test_the_last_allowed_call_answers_from_the_results_so_far():
    script = [run_sql_call(f"SELECT {n}") for n in range(1, 5)] + [AIMessage("Partial answer from four queries.")]
    model = ScriptedChatModel(script=script)
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(n,)]) for n in range(1, 5)])

    result = assemble_app(model, database).ask("Count forever")

    assert model.had_tools == [True, True, True, True, False]
    system_messages = [received[0].text for received in model.received]
    assert all(LAST_CALL_NOTE not in text for text in system_messages[:4])
    assert system_messages[4].endswith(LAST_CALL_NOTE)
    assert result.answer == "Partial answer from four queries."


def test_the_last_allowed_call_sees_earlier_queries_and_results_as_plain_text():
    script = [run_sql_call("SELECT 1"), AIMessage("One.")]
    model = ScriptedChatModel(script=script)
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(1,)])])

    assemble_app(model, database, max_llm_calls=2).ask("Count to one")

    last_call = model.received[1]
    assert not any(isinstance(message, ToolMessage) for message in last_call)
    assert not any(isinstance(message, AIMessage) and message.tool_calls for message in last_call)
    assert [message.text for message in last_call[2:]] == ["I ran:\nrun_sql:\nSELECT 1", "[run_sql result]\nn\n1"]


def test_a_model_that_calls_a_tool_on_its_last_call_ends_at_the_call_budget():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT {n}") for n in range(1, 7)])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(n,)]) for n in range(1, 7)])

    result = assemble_app(model, database).ask("Count forever")

    assert len(model.received) == 5
    assert [run.sql for run in result.sql] == [f"SELECT {n}" for n in range(1, 6)]
    assert result.answer == (
        "I couldn't finish this question within 5 model calls. Try asking it in a simpler or more specific way."
    )


def test_a_follow_up_question_sees_the_earlier_turn():
    sql = f"SELECT p.brand, SUM(oi.sale_price) AS revenue FROM {ORDER_ITEMS} oi JOIN {PRODUCTS} p ON p.id = oi.product_id GROUP BY p.brand"
    model = ScriptedChatModel(
        script=[run_sql_call(sql), AIMessage("Brand A leads with $100."), AIMessage("The month before, Brand B led.")]
    )
    database = FakeDatabase([QueryResult(columns=["brand", "revenue"], rows=[("Brand A", 100.0)])])
    app = assemble_app(model, database)

    app.ask("Top brand last month?")
    result = app.ask("And the month before?")

    follow_up_input = [message.text for message in model.received[2]]
    assert "Top brand last month?" in follow_up_input
    assert "Brand A leads with $100." in follow_up_input
    assert follow_up_input[-1] == "And the month before?"
    assert result.answer == "The month before, Brand B led."
    assert [run.sql for run in result.sql] == []  # only this turn's SQL, not the earlier turn's


def test_a_new_app_starts_a_fresh_conversation():
    model = ScriptedChatModel(script=[AIMessage("First answer."), AIMessage("Second answer.")])

    assemble_app(model, FakeDatabase([])).ask("First question")
    assemble_app(model, FakeDatabase([])).ask("Second question")

    fresh_input = [message.text for message in model.received[1]]
    assert "First question" not in fresh_input
    assert fresh_input[-1] == "Second question"


def test_a_question_that_fails_mid_query_leaves_a_valid_conversation():
    # An outage ends the question without a tool result. Providers reject a tool call with no result,
    # so without one every later question in the run would fail too.
    model = ScriptedChatModel(script=[run_sql_call("SELECT 1"), AIMessage("Second answer.")])
    database = FakeDatabase([ConnectionError("BigQuery is down")])
    app = assemble_app(model, database)

    with pytest.raises(ConnectionError):
        app.ask("First question")
    result = app.ask("Second question")

    tool_call, tool_result, second_question = model.received[1][-3:]
    assert tool_call.tool_calls[0]["args"] == {"sql": "SELECT 1"}
    assert isinstance(tool_result, ToolMessage)
    assert tool_result.tool_call_id == tool_call.tool_calls[0]["id"]
    assert tool_result.text.startswith("Error:")
    assert "did not run" in tool_result.text
    assert second_question.text == "Second question"
    assert result.answer == "Second answer."
    assert database.executed == ["SELECT 1"]  # the failed query is not retried on its own


def test_the_call_budget_counts_each_question_separately():
    def four_queries_then(answer: str) -> list[AIMessage]:
        return [run_sql_call(f"SELECT {n}") for n in range(1, 5)] + [AIMessage(answer)]

    model = ScriptedChatModel(script=four_queries_then("First done.") + four_queries_then("Second done."))
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(n,)]) for n in range(1, 9)])
    app = assemble_app(model, database)

    app.ask("Count to four")
    result = app.ask("Count to four again")

    assert len(model.received) == 10
    assert result.answer == "Second done."


def test_model_sees_the_schema_and_dialect_in_its_system_message():
    model = ScriptedChatModel(script=[AIMessage("Hello.")])

    assemble_app(model, FakeDatabase([])).ask("Hi")

    system_message = model.received[0][0]
    assert isinstance(system_message, SystemMessage)
    assert "bigquery SQL" in system_message.text
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


def test_a_failing_model_falls_back_to_the_next_model_which_keeps_the_tools():
    sql = f"SELECT brand FROM {PRODUCTS}"
    quota_exceeded = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
    model = ScriptedChatModel(script=[quota_exceeded, quota_exceeded])
    fallback = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Brand A.")])
    database = FakeDatabase([QueryResult(columns=["brand"], rows=[("Brand A",)])])

    result = assemble_app(model, database, fallback_models=(fallback,)).ask("Which brands are there?")

    assert result.answer == "Brand A."
    assert [run.sql for run in result.sql] == [sql]
    assert fallback.had_tools == [True, True]
    # The primary is tried first on every call, so a busy model is used again once it recovers.
    assert len(model.received) == 2


def test_the_question_fails_when_every_model_fails(caplog):
    model = ScriptedChatModel(script=[RuntimeError("503 UNAVAILABLE: model is busy")])
    fallback = ScriptedChatModel(script=[RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")])

    with caplog.at_level(logging.INFO, logger="retail_agent"), pytest.raises(RuntimeError, match="429"):
        assemble_app(model, FakeDatabase([]), fallback_models=(fallback,)).ask("Anything")

    assert caplog.text.count("LLM call failed") == 2


def test_a_fallback_model_on_the_last_allowed_call_gets_no_tools():
    sql = f"SELECT brand FROM {PRODUCTS}"
    model = ScriptedChatModel(script=[run_sql_call(sql), RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")])
    fallback = ScriptedChatModel(script=[AIMessage("Brand A, from the results so far.")])
    database = FakeDatabase([QueryResult(columns=["brand"], rows=[("Brand A",)])])

    result = assemble_app(model, database, fallback_models=(fallback,), max_llm_calls=2).ask("Which brands?")

    assert result.answer == "Brand A, from the results so far."
    assert fallback.had_tools == [False]
    assert LAST_CALL_NOTE in fallback.received[0][0].text
