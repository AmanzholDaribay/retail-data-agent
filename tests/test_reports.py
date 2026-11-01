import json
import sqlite3

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
