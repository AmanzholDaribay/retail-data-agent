import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt

from retail_agent.agent import BUDGET_REACHED, build_agent, current_turn
from retail_agent.config import Settings
from retail_agent.db.base import Database
from retail_agent.db.bigquery import BigQueryDatabase
from retail_agent.llm import build_model
from retail_agent.observability import LlmCallLogger
from retail_agent.query_safety.gateway import QueryGateway
from retail_agent.query_safety.step1_guard import QueryGuard
from retail_agent.query_safety.step2_personal_data.columns import PersonalDataCheck
from retail_agent.query_safety.step2_personal_data.customer_pseudonyms import SALT_PARAMETER, CustomerPseudonyms
from retail_agent.query_safety.step2_personal_data.small_groups import SmallGroups
from retail_agent.query_safety.step3_brand_scope.brand_scope import BrandScope
from retail_agent.schema import Schema, load_schema
from retail_agent.reports import ReportStore
from retail_agent.tools import make_delete_reports, make_list_reports, make_run_sql, make_save_report
from retail_agent.users import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlRun:
    sql: str  # as the model wrote it
    error: str | None  # why it didn't run (a guard rejection or a BigQuery error); None when it ran
    sent_sql: str | None = None  # as BigQuery got it, after the gateway's rewrites; None when it didn't run


@dataclass(frozen=True)
class TurnResult:
    answer: str  # empty while the turn waits for a confirmation
    sql: list[SqlRun]  # every run_sql call of this turn, in order
    # Set when the turn is paused for the user to confirm, e.g. the list of reports to delete; resume with App.confirm.
    confirmation: str | None = None


class App:
    """One conversation: every question asked of the same App sees the earlier turns."""

    def __init__(self, agent: CompiledStateGraph, schema_text: str, user: User, model_name: str) -> None:
        self._agent = agent
        self.schema_text = schema_text  # the schema exactly as the LLM sees it in the system prompt
        # The checkpointer keeps the conversation under this id; a new App (one CLI run) starts a new one.
        self._thread_id = uuid4().hex
        # Context for LangSmith traces (sent only when LANGSMITH_TRACING=true). LangSmith groups traces that share a
        # thread_id into one thread, so a CLI run reads as one conversation.
        self._trace_metadata = {
            "user": user.name,
            "brands": ", ".join(sorted(user.brands)) if user.brands else "all",
            "model": model_name,
            "thread_id": self._thread_id,
        }
        self._trace_tags = [user.name]
        # What a paused turn waits on, until App.confirm resumes it: one Interrupt per tool that paused.
        self._paused: list[Interrupt] = []

    def ask(self, question: str) -> TurnResult:
        logger.info("Question: %s", question)
        return self._run({"messages": [HumanMessage(question)]})

    def confirm(self, answer: str) -> TurnResult:
        """Resume the paused turn with what the user typed. The tool that paused it decides what the answer means."""
        if not self._paused:
            raise RuntimeError("No turn is waiting for a confirmation")
        logger.info("Confirmation answer: %r", answer)
        # By id: when one reply paused two tools (two deletes), each gets its own answer, one question at a time.
        first = self._paused[0]
        return self._run(Command(resume={first.id: {**first.value, "answer": answer}}))

    def _run(self, agent_input: dict | Command) -> TurnResult:
        config = {
            "callbacks": [LlmCallLogger()],
            "configurable": {"thread_id": self._thread_id},
            # A resumed turn (the user's yes or no to a delete) is its own trace, in the same thread.
            "run_name": "confirmation" if isinstance(agent_input, Command) else "question",
            "metadata": self._trace_metadata,
            "tags": self._trace_tags,
        }
        try:
            state = self._agent.invoke(agent_input, config=config)
        except Exception as error:
            self._close_unanswered_tool_calls(config, error)
            raise
        turn = current_turn(state["messages"])
        # run_sql returns "Error: …" when the gateway rejects a query or BigQuery fails it.
        errors = {
            message.tool_call_id: message.text.removeprefix("Error: ")
            for message in turn
            if isinstance(message, ToolMessage) and message.text.startswith("Error:")
        }
        # The run_sql artifact: the SQL the gateway sent to BigQuery. The model never sees it.
        sent = {message.tool_call_id: message.artifact for message in turn if isinstance(message, ToolMessage)}
        sql = [
            SqlRun(sql=call["args"]["sql"], error=errors.get(call["id"]), sent_sql=sent.get(call["id"]))
            for message in turn
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if call["name"] == "run_sql"
        ]
        if state.get("__interrupt__"):
            self._paused = state["__interrupt__"]
            logger.info("Waiting for the user to confirm")
            return TurnResult(answer="", sql=sql, confirmation=self._paused[0].value["question"])
        self._paused = []
        if turn[-1].name == BUDGET_REACHED:
            logger.warning("Stopped at the LLM call budget after %d SQL statements", len(sql))
        else:
            logger.info("Answered with %d SQL statements", len(sql))
        return TurnResult(answer=turn[-1].text, sql=sql)

    def _close_unanswered_tool_calls(self, config: dict, error: Exception) -> None:
        """Save an error result for each tool call a failed question left without one.

        An outage ends the question before the tool returns, but the checkpointer has already saved
        the model's tool call. Providers reject a tool call with no result, so without this every
        later question in the run would fail too. With it, the model knows the query didn't run
        and can run it again if the user asks.
        """
        messages = self._agent.get_state(config).values.get("messages", [])
        answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage):
            return
        results = [
            ToolMessage(
                f"Error: this query did not run because of {type(error).__name__}: {error}",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
            for call in last.tool_calls
            if call["id"] not in answered
        ]
        if results:
            self._agent.update_state(config, {"messages": results})
            logger.info("Saved an error result for %d unanswered tool calls", len(results))


def build_app(settings: Settings, user: User) -> App:
    """Build the app with the real model and database, for `user`."""
    return assemble(
        model=build_model(settings, settings.llm_model),
        database=BigQueryDatabase(
            settings.gcp_project,
            settings.max_bytes_billed,
            query_parameters={SALT_PARAMETER: settings.pseudonym_salt.get_secret_value()},
        ),
        schema=load_schema(settings.schema_path),
        prompt_template=settings.prompt_path.read_text(),
        settings=settings,
        user=user,
        fallback_models=[build_model(settings, name) for name in settings.llm_fallback_models],
    )


def assemble(
    model: BaseChatModel,
    database: Database,
    schema: Schema,
    prompt_template: str,
    settings: Settings,
    user: User,
    fallback_models: Sequence[BaseChatModel] = (),
) -> App:
    """Wire the app from its parts. Tests call this directly with fakes."""
    guard = QueryGuard(database.dialect, allowed_tables=[schema.qualified_name(table) for table in schema.tables])
    personal_data = PersonalDataCheck(database.dialect, schema)
    small_groups = SmallGroups(database.dialect, schema, settings.min_group_size)
    pseudonyms = CustomerPseudonyms(database.dialect, schema.dataset)
    scope = BrandScope(database.dialect, schema.dataset, user.brands)
    gateway = QueryGateway(
        database, guard, personal_data, small_groups, pseudonyms, settings.max_rows, settings.max_bytes_billed, scope
    )
    schema_text = schema.render()
    # The brand scope above is the real lock; telling the model the brands lets it say so instead of querying blind.
    user_brands = ", ".join(sorted(user.brands)) if user.brands else "all brands"
    system_prompt = prompt_template.format(dialect=database.dialect, schema=schema_text, **{"user-brands": user_brands})
    reports = ReportStore(settings.reports_path)
    agent = build_agent(
        model,
        [
            make_run_sql(gateway),
            make_save_report(reports, user.name),
            make_list_reports(reports, user.name),
            make_delete_reports(reports, user.name),
        ],
        system_prompt,
        settings.max_llm_calls,
        checkpointer=InMemorySaver(),
        fallback_models=fallback_models,
    )
    return App(agent, schema_text, user, settings.llm_model)
