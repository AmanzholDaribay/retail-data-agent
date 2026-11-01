from datetime import datetime

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from retail_agent.agent import current_turn
from retail_agent.db.base import QueryError
from retail_agent.query_safety.gateway import QueryGateway
from retail_agent.reports import ReportStore, SavedReport
from retail_agent.result_format import format_result


def make_run_sql(gateway: QueryGateway) -> BaseTool:
    @tool
    def run_sql(sql: str) -> str:
        """Run one SELECT statement against the retail database and return the rows as a table.

        Call it several times in one reply for queries that don't depend on each other."""
        try:
            result = gateway.run(sql)
        except QueryError as error:
            # The model reads the error and sends corrected SQL. Other exceptions are bugs and still raise.
            return f"Error: {error}"
        return format_result(result)

    return run_sql


def make_save_report(store: ReportStore, owner: str) -> BaseTool:
    # return_direct: the saved report is the final answer, so saving costs no extra LLM call.
    @tool(return_direct=True)
    def save_report(title: str, report: str, runtime: ToolRuntime) -> str:
        """Save a finished report and show it to the user. Call it only when the user asked for a report,
        once, after the queries, with the full report text. Its result is your answer, so don't call other tools with it."""
        # The model writes only the title and text. Who asked, where and with which SQL comes from the conversation.
        messages = runtime.state["messages"]
        question = next(message for message in reversed(messages) if isinstance(message, HumanMessage))
        turn = current_turn(messages)
        # Only the SQL behind the report's numbers: leave out queries that were rejected or failed.
        failed = {m.tool_call_id for m in turn if isinstance(m, ToolMessage) and m.text.startswith("Error:")}
        sql = [
            call["args"]["sql"]
            for message in turn
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if call["name"] == "run_sql" and call["id"] not in failed
        ]
        report_id = store.save(
            owner=owner,
            conversation_id=runtime.config["configurable"]["thread_id"],
            question=question.text,
            sql=sql,
            title=title,
            report=report,
        )
        return f"{report}\n\n(Saved as report {report_id}: {title}.)"

    return save_report


def make_list_reports(store: ReportStore, owner: str) -> BaseTool:
    # return_direct: the list is the answer, so listing costs one LLM call.
    @tool(return_direct=True)
    def list_reports(runtime: ToolRuntime, mentioning: str = "", this_conversation: bool = False) -> str:
        """List the user's saved reports: id, date and title, newest first. Its result is your answer.

        mentioning: only reports whose title, question or text contain this, e.g. "Client X". Empty for all.
        this_conversation: only reports made in this conversation."""
        # The owner and the conversation come from the code, so the model can't list someone else's reports.
        conversation_id = runtime.config["configurable"]["thread_id"]
        reports = store.find(
            owner, mentioning=mentioning or None, conversation_id=conversation_id if this_conversation else None
        )
        which = (f" mentioning '{mentioning}'" if mentioning else "") + (" from this conversation" if this_conversation else "")
        if not reports:
            return f"No saved reports{which}."
        lines = [_describe(report, conversation_id) for report in reports]
        return f"Your saved reports{which} ({len(reports)}):\n" + "\n".join(lines)

    return list_reports


def _describe(report: SavedReport, conversation_id: str) -> str:
    """E.g. `3 · 2026-11-01 16:11 UTC · Q1 2026 report (this conversation)`."""
    created = datetime.fromisoformat(report.created_at).strftime("%Y-%m-%d %H:%M UTC")
    here = " (this conversation)" if report.conversation_id == conversation_id else ""
    return f"{report.id} · {created} · {report.title}{here}"
