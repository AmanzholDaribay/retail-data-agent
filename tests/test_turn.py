from pathlib import Path

from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from langchain_core.messages import AIMessage, SystemMessage

from retail_agent.app import App, assemble
from retail_agent.config import Settings
from retail_agent.db.base import QueryResult
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


def test_model_sees_the_schema_and_dialect_in_its_system_message():
    model = ScriptedChatModel(script=[AIMessage("Hello.")])

    assemble_app(model, FakeDatabase([])).ask("Hi")

    system_message = model.received[0][0]
    assert isinstance(system_message, SystemMessage)
    assert "fakesql SQL" in system_message.text
    for table in ["orders", "order_items", "products", "users"]:
        assert f"`bigquery-public-data.thelook_ecommerce.{table}`" in system_message.text
