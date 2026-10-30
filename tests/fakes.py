from itertools import count
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from retail_agent.db.base import QueryResult

_call_ids = count(1)


def run_sql_call(sql: str) -> AIMessage:
    """A model reply that calls the run_sql tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": "run_sql", "args": {"sql": sql}, "id": f"call_{next(_call_ids)}"}],
    )


class ScriptedChatModel(BaseChatModel):
    """Replies with a fixed script of messages and records every message list it was sent.

    A script entry can also be an exception, which the model raises instead of replying.
    """

    script: list[Any]  # AIMessage or Exception; Any stops pydantic coercing exceptions into messages
    received: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.received.append(list(messages))
        if len(self.received) > len(self.script):
            raise AssertionError(f"Model was called {len(self.received)} times, but the script has {len(self.script)} replies")
        reply = self.script[len(self.received) - 1]
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


class FakeDatabase:
    """Returns canned results in order and records every query it receives."""

    dialect = "fakesql"

    def __init__(self, results: list[QueryResult]) -> None:
        self._results = list(results)
        self.executed: list[str] = []

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        self.executed.append(sql)
        return self._results.pop(0)
