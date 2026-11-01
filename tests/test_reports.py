import json
import sqlite3

import pytest

from fakes import FakeDatabase, ScriptedChatModel
from harness import ORDER_ITEMS, assemble_app
from langchain_core.messages import AIMessage

from retail_agent.agent import LAST_CALL_NOTE
from retail_agent.db.base import QueryResult
from retail_agent.reports import ReportStore
from retail_agent.users import User

REVENUE_SQL = f"SELECT SUM(sale_price) AS revenue FROM {ORDER_ITEMS}"
ITEMS_SQL = f"SELECT COUNT(*) AS items FROM {ORDER_ITEMS}"
REPORT_TEXT = "Findings: revenue was 1,000. Insights: steady. Action items for Q2: grow."


def two_queries_at_once() -> AIMessage:
    """A model reply that sends two independent queries together, as parallel tool calls."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "run_sql", "args": {"sql": REVENUE_SQL}, "id": "call_revenue"},
            {"name": "run_sql", "args": {"sql": ITEMS_SQL}, "id": "call_items"},
        ],
    )


def save_report_call(title: str = "Q1 report") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "save_report", "args": {"title": title, "report": REPORT_TEXT}, "id": "call_save"}],
    )


def two_results() -> FakeDatabase:
    return FakeDatabase(
        [QueryResult(columns=["revenue"], rows=[(1000,)]), QueryResult(columns=["items"], rows=[(40,)])]
    )


def saved_rows(path) -> list[sqlite3.Row]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM reports ORDER BY id").fetchall()


def test_a_report_is_saved_with_its_owner_question_and_sql_in_two_llm_calls(tmp_path):
    path = tmp_path / "reports.sqlite3"
    model = ScriptedChatModel(script=[two_queries_at_once(), save_report_call()])
    manager = User(name="levis_manager", brands=frozenset({"Levi's"}))

    result = assemble_app(model, two_results(), user=manager, reports_path=path).ask("Create a Q1 report")

    [row] = saved_rows(path)
    assert row["owner"] == "levis_manager"
    assert row["question"] == "Create a Q1 report"
    assert json.loads(row["sql"]) == [REVENUE_SQL, ITEMS_SQL]
    assert (row["title"], row["report"]) == ("Q1 report", REPORT_TEXT)
    assert row["conversation_id"] and row["created_at"]
    assert len(model.received) == 2  # no extra call to say "saved"
    assert REPORT_TEXT in result.answer
    assert f"Saved as report {row['id']}" in result.answer


def test_a_rejected_query_is_left_out_of_the_saved_sql(tmp_path):
    path = tmp_path / "reports.sqlite3"
    rejected_and_good = AIMessage(
        content="",
        tool_calls=[
            {"name": "run_sql", "args": {"sql": "DELETE FROM orders WHERE TRUE"}, "id": "call_bad"},
            {"name": "run_sql", "args": {"sql": REVENUE_SQL}, "id": "call_good"},
        ],
    )
    model = ScriptedChatModel(script=[rejected_and_good, save_report_call()])
    database = FakeDatabase([QueryResult(columns=["revenue"], rows=[(1000,)])])

    assemble_app(model, database, reports_path=path).ask("Create a Q1 report")

    assert json.loads(saved_rows(path)[0]["sql"]) == [REVENUE_SQL]


def test_the_last_allowed_call_is_told_a_report_cant_be_saved_then():
    assert "can't be saved" in LAST_CALL_NOTE


def test_a_normal_question_saves_nothing(tmp_path):
    path = tmp_path / "reports.sqlite3"
    model = ScriptedChatModel(script=[AIMessage("There are 4 tables.")])

    assemble_app(model, FakeDatabase([]), reports_path=path).ask("What data is there?")

    assert not path.exists()


def test_each_cli_run_saves_under_its_own_conversation_id(tmp_path):
    path = tmp_path / "reports.sqlite3"
    for _ in range(2):
        model = ScriptedChatModel(script=[two_queries_at_once(), save_report_call()])
        assemble_app(model, two_results(), reports_path=path).ask("Create a Q1 report")

    first, second = saved_rows(path)
    assert first["conversation_id"] != second["conversation_id"]
    assert (first["id"], second["id"]) == (1, 2)


def test_the_store_creates_its_folder_and_table_when_missing(tmp_path):
    path = tmp_path / "data" / "reports.sqlite3"

    report_id = ReportStore(path).save(
        owner="ceo", conversation_id="abc", question="q", sql=["SELECT 1"], title="t", report="r"
    )

    assert report_id == 1
    assert saved_rows(path)[0]["owner"] == "ceo"


def list_reports_call(**args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "list_reports", "args": args, "id": "call_list"}])


def save(store: ReportStore, owner: str, title: str, report: str = "text", conversation_id: str = "old") -> int:
    return store.save(
        owner=owner, conversation_id=conversation_id, question="Create a report", sql=[], title=title, report=report
    )


def test_a_user_never_sees_another_users_reports(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "CEO's Brazil report", report="Brazil grew")
    save(store, "levis_manager", "Levi's Brazil report", report="Brazil grew")

    assert [report.title for report in store.find("ceo", mentioning="Brazil")] == ["CEO's Brazil report"]


def test_mentioning_searches_title_question_and_text_ignoring_case(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Client X review")
    store.save(owner="ceo", conversation_id="old", question="report on client x", sql=[], title="Q2", report="r")
    save(store, "ceo", "Q3", report="Notes about CLIENT X")
    save(store, "ceo", "Unrelated")

    assert [report.title for report in store.find("ceo", mentioning="client x")] == ["Q3", "Q2", "Client X review"]


def test_percent_and_underscore_in_the_search_are_taken_literally(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Margin up 5%")
    save(store, "ceo", "Margin up 50 points")

    assert [report.title for report in store.find("ceo", mentioning="5%")] == ["Margin up 5%"]
    assert store.find("ceo", mentioning="_") == []


def test_finding_without_a_report_file_creates_nothing(tmp_path):
    path = tmp_path / "reports.sqlite3"

    assert ReportStore(path).find("ceo") == []
    assert not path.exists()


def test_show_my_reports_lists_this_conversations_reports_in_one_call(tmp_path):
    path = tmp_path / "reports.sqlite3"
    save(ReportStore(path), "ceo", "From an earlier run")
    model = ScriptedChatModel(
        script=[two_queries_at_once(), save_report_call("Q1 report"), list_reports_call(this_conversation=True)]
    )
    app = assemble_app(model, two_results(), reports_path=path)
    app.ask("Create a Q1 report")

    result = app.ask("What reports did we make in this conversation?")

    assert len(model.received) == 3  # the list itself is the answer: 1 call for the second question
    assert "2 · " in result.answer and "Q1 report (this conversation)" in result.answer
    assert "From an earlier run" not in result.answer


def test_an_empty_search_says_so(tmp_path):
    model = ScriptedChatModel(script=[list_reports_call(mentioning="Brazil")])

    result = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3").ask("Reports on Brazil?")

    assert result.answer == "No saved reports mentioning 'Brazil'."


def test_a_reports_file_without_the_table_lists_nothing(tmp_path):
    path = tmp_path / "reports.sqlite3"
    path.touch()

    assert ReportStore(path).find("ceo") == []


def test_mentioning_ignores_case_beyond_ascii(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Ümraniye stores")

    assert [report.title for report in store.find("ceo", mentioning="ümraniye")] == ["Ümraniye stores"]


def test_the_header_names_both_filters(tmp_path):
    model = ScriptedChatModel(script=[list_reports_call(mentioning="Brazil", this_conversation=True)])

    result = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3").ask("Brazil reports here?")

    assert result.answer == "No saved reports mentioning 'Brazil' from this conversation."


def delete_reports_call(**args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "delete_reports", "args": args, "id": "call_delete"}])


def titles(store: ReportStore, owner: str = "ceo") -> list[str]:
    return [report.title for report in store.find(owner)]


def test_delete_by_text_shows_the_exact_list_and_deletes_only_after_yes(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Client X Q1 review")
    save(store, "ceo", "Unrelated")
    save(store, "ceo", "Q2", report="Notes on client x")
    model = ScriptedChatModel(script=[delete_reports_call(mentioning="Client X")])
    app = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3")

    paused = app.ask("Delete all reports mentioning Client X")

    assert paused.answer == ""
    assert "3 · " in paused.confirmation and "Q2" in paused.confirmation
    assert "1 · " in paused.confirmation and "Client X Q1 review" in paused.confirmation
    assert "Unrelated" not in paused.confirmation
    assert "Type yes to delete these 2 reports" in paused.confirmation
    assert titles(store) == ["Q2", "Unrelated", "Client X Q1 review"]  # nothing deleted before the yes

    done = app.confirm("yes")

    assert done.answer == "Deleted 2 reports."
    assert done.confirmation is None
    assert titles(store) == ["Unrelated"]
    assert len(model.received) == 1  # proposing, pausing and deleting cost one LLM call


def test_delete_the_reports_made_in_this_conversation(tmp_path):
    path = tmp_path / "reports.sqlite3"
    save(ReportStore(path), "ceo", "From an earlier run")
    model = ScriptedChatModel(
        script=[two_queries_at_once(), save_report_call("Q1 report"), delete_reports_call(this_conversation=True)]
    )
    app = assemble_app(model, two_results(), reports_path=path)
    app.ask("Create a Q1 report")

    paused = app.ask("Delete all the reports we made in this conversation")
    assert "Q1 report" in paused.confirmation and "From an earlier run" not in paused.confirmation
    app.confirm(" YES ")

    assert titles(ReportStore(path)) == ["From an earlier run"]


@pytest.mark.parametrize("answer", ["no", "y", "ok", ""])
def test_anything_but_yes_cancels(tmp_path, answer):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Client X Q1 review")
    model = ScriptedChatModel(script=[delete_reports_call(mentioning="Client X")])
    app = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3")
    app.ask("Delete all reports mentioning Client X")

    result = app.confirm(answer)

    assert result.answer == "Cancelled. Nothing was deleted."
    assert titles(store) == ["Client X Q1 review"]


def test_a_user_cant_delete_another_users_reports(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    theirs = save(store, "levis_manager", "Client X Levi's sales")
    model = ScriptedChatModel(script=[delete_reports_call(mentioning="Client X")])

    result = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3").ask("Delete reports mentioning Client X")

    assert result.confirmation is None
    assert result.answer == "No saved reports mentioning 'Client X'. Nothing was deleted."
    assert store.delete("ceo", [theirs]) == 0
    assert titles(store, "levis_manager") == ["Client X Levi's sales"]


def test_only_the_reports_that_were_shown_are_deleted(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Client X Q1 review")
    model = ScriptedChatModel(script=[delete_reports_call(mentioning="Client X")])
    app = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3")
    app.ask("Delete all reports mentioning Client X")
    save(store, "ceo", "Client X Q2 review")  # saved by another run while this one waits

    app.confirm("yes")

    assert titles(store) == ["Client X Q2 review"]


def test_the_conversation_goes_on_after_a_cancel(tmp_path):
    store = ReportStore(tmp_path / "reports.sqlite3")
    save(store, "ceo", "Client X Q1 review")
    model = ScriptedChatModel(script=[delete_reports_call(mentioning="Client X"), AIMessage("There are 4 tables.")])
    app = assemble_app(model, FakeDatabase([]), reports_path=tmp_path / "reports.sqlite3")
    app.ask("Delete all reports mentioning Client X")
    app.confirm("no")

    assert app.ask("What data is there?").answer == "There are 4 tables."


def test_two_deletes_in_one_reply_are_confirmed_one_at_a_time(tmp_path):
    path = tmp_path / "reports.sqlite3"
    store = ReportStore(path)
    save(store, "ceo", "Client X review")
    save(store, "ceo", "Client Y review")
    two_deletes = AIMessage(
        content="",
        tool_calls=[
            {"name": "delete_reports", "args": {"mentioning": "Client X"}, "id": "call_x"},
            {"name": "delete_reports", "args": {"mentioning": "Client Y"}, "id": "call_y"},
        ],
    )
    app = assemble_app(ScriptedChatModel(script=[two_deletes]), FakeDatabase([]), reports_path=path)

    first = app.ask("Delete the Client X and Client Y reports")
    second = app.confirm("yes" if "Client X" in first.confirmation else "no")
    done = app.confirm("no" if "Client X" in first.confirmation else "yes")

    assert "Client Y" in second.confirmation or "Client X" in second.confirmation
    assert done.confirmation is None
    assert titles(store) == ["Client Y review"]
