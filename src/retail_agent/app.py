import logging
from dataclasses import dataclass
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from retail_agent.agent import BUDGET_REACHED, build_agent
from retail_agent.config import Settings
from retail_agent.db.base import Database
from retail_agent.db.bigquery import BigQueryDatabase
from retail_agent.gateway import QueryGateway
from retail_agent.llm import build_model
from retail_agent.observability import LlmCallLogger
from retail_agent.schema import Schema, load_schema
from retail_agent.tools import make_run_sql

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    answer: str
    sql: list[str]


class App:
    """One conversation: every question asked of the same App sees the earlier turns."""

    def __init__(self, agent: CompiledStateGraph, schema_text: str) -> None:
        self._agent = agent
        self.schema_text = schema_text  # the schema exactly as the LLM sees it in the system prompt
        # The checkpointer keeps the conversation under this id; a new App (one CLI run) starts a new one.
        self._thread_id = uuid4().hex

    def ask(self, question: str) -> TurnResult:
        logger.info("Question: %s", question)
        config = {"callbacks": [LlmCallLogger()], "configurable": {"thread_id": self._thread_id}}
        try:
            state = self._agent.invoke({"messages": [HumanMessage(question)]}, config=config)
        except Exception as error:
            self._close_unanswered_tool_calls(config, error)
            raise
        turn = _current_turn(state["messages"])
        sql = [
            call["args"]["sql"]
            for message in turn
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if call["name"] == "run_sql"
        ]
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


def _current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Messages after the latest question, so earlier turns don't leak into this result."""
    start = max(i for i, message in enumerate(messages) if isinstance(message, HumanMessage))
    return messages[start + 1 :]


def build_app(settings: Settings) -> App:
    """Build the app with the real model and database."""
    return assemble(
        model=build_model(settings),
        database=BigQueryDatabase(settings.gcp_project, settings.max_bytes_billed),
        schema=load_schema(settings.schema_path),
        prompt_template=settings.prompt_path.read_text(),
        settings=settings,
    )


def assemble(
    model: BaseChatModel,
    database: Database,
    schema: Schema,
    prompt_template: str,
    settings: Settings,
) -> App:
    """Wire the app from its parts. Tests call this directly with fakes."""
    gateway = QueryGateway(database, settings.max_rows)
    schema_text = schema.render()
    system_prompt = prompt_template.format(dialect=database.dialect, schema=schema_text)
    agent = build_agent(
        model, [make_run_sql(gateway)], system_prompt, settings.max_llm_calls, checkpointer=InMemorySaver()
    )
    return App(agent, schema_text)
