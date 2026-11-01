from unittest.mock import MagicMock

from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import USERS, assemble_app
from langchain_core.messages import AIMessage
from langsmith import tracing_context

from retail_agent.config import Settings
from retail_agent.db.base import QueryResult
from retail_agent.users import User

LEVIS = User(name="levis_manager", brands=frozenset({"Levi's"}))


def test_every_llm_call_carries_the_user_model_and_conversation():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT COUNT(*) AS n FROM {USERS}"), AIMessage("40.")])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(40,)])])
    app = assemble_app(model, database, user=LEVIS)

    app.ask("How many customers?")

    for metadata in model.metadata_seen:
        assert metadata["user"] == "levis_manager"
        assert metadata["brands"] == "Levi's"
        assert metadata["model"] == Settings(gcp_project="test-project").llm_model
        assert metadata["thread_id"] == app._thread_id
    assert all("levis_manager" in tags for tags in model.tags_seen)


def test_the_gateway_step_shows_the_sql_bigquery_ran():
    client = MagicMock()
    sql = f"SELECT id FROM {USERS} LIMIT 1"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("cust_1.")])
    database = FakeDatabase([QueryResult(columns=["id"], rows=[("cust_1",)], bytes_processed=1234)])

    with tracing_context(enabled=True, client=client):
        assemble_app(model, database).ask("One customer?")

    [step] = [call.kwargs for call in client.update_run.call_args_list if call.kwargs.get("name") == "query_gateway"]
    metadata = step["extra"]["metadata"]
    assert "_pseudonymized_users" in metadata["rewritten_sql"]
    assert (metadata["rows"], metadata["bytes_processed"]) == (1, 1234)


def test_with_tracing_off_nothing_is_sent():
    client = MagicMock()
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT COUNT(*) AS n FROM {USERS}"), AIMessage("40.")])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(40,)])])

    with tracing_context(enabled=False, client=client):
        answer = assemble_app(model, database).ask("How many customers?").answer

    assert answer == "40."
    assert not client.create_run.called
