from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRequest, ModelResponse, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

# Name on the closing message of a question stopped by the call budget, so the app can tell it from an answer.
BUDGET_REACHED = "budget_reached"


# Added to the system message on the last allowed call, which can't call tools.
LAST_CALL_NOTE = (
    "This is your last step for this question: you can't run more queries. "
    "Answer now from the results you already have, and say plainly what is missing."
)


class CallBudget(ModelCallLimitMiddleware):
    """Limits a question to `max_llm_calls` model calls.

    The last allowed call can't call tools, so the question ends with an answer built from the
    results already fetched. If a model calls a tool anyway, the turn ends with a message meant
    for the user instead of raising, so the SQL run so far is kept.
    """

    def __init__(self, max_llm_calls: int) -> None:
        super().__init__(run_limit=max_llm_calls, exit_behavior="end")

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        if request.state.get("run_model_call_count", 0) == self.run_limit - 1:
            system_text = request.system_message.text if request.system_message else ""
            # Gemini was seen calling run_sql with tool_choice="none", and even with no tools declared,
            # copying the tool calls in the history. So this call gets no tools and a history in plain text.
            request = request.override(
                tools=[],
                tool_choice=None,
                messages=_tool_exchanges_as_text(request.messages),
                system_message=SystemMessage(f"{system_text}\n\n{LAST_CALL_NOTE}".strip()),
            )
        return handler(request)

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        update = super().before_model(state, runtime)
        if update is not None:
            update["messages"] = [
                AIMessage(
                    f"I couldn't finish this question within {self.run_limit} model calls. "
                    "Try asking it in a simpler or more specific way.",
                    name=BUDGET_REACHED,
                )
            ]
        return update


def _tool_exchanges_as_text(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """The same conversation with each tool call and tool result written out as plain messages.

    Only the request is rewritten; the saved conversation keeps the real tool messages.
    """
    rewritten: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            queries = "\n\n".join(f"{call['name']}:\n{call['args'].get('sql', call['args'])}" for call in message.tool_calls)
            rewritten.append(AIMessage(f"{message.text}\n\nI ran:\n{queries}".strip()))
        elif isinstance(message, ToolMessage):
            rewritten.append(HumanMessage(f"[{message.name} result]\n{message.text}"))
        else:
            rewritten.append(message)
    return rewritten


def build_agent(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
    max_llm_calls: int,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    return create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        middleware=[CallBudget(max_llm_calls)],
        checkpointer=checkpointer,
    )
