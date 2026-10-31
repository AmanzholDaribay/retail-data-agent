from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

# Name on the closing message of a question stopped by the call budget, so the app can tell it from an answer.
BUDGET_REACHED = "budget_reached"


class CallBudget(ModelCallLimitMiddleware):
    """Ends a question after `max_llm_calls` model calls, with a message meant for the user.

    It ends the turn instead of raising, so the SQL run so far is kept.
    """

    def __init__(self, max_llm_calls: int) -> None:
        super().__init__(run_limit=max_llm_calls, exit_behavior="end")

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
