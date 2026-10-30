import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)


class LlmCallLogger(BaseCallbackHandler):
    """Logs every LLM call with its duration, so a slow or failing model is visible in the log."""

    def __init__(self) -> None:
        self._started: dict[UUID, float] = {}

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[Any]], *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = time.monotonic()
        model = (kwargs.get("metadata") or {}).get("ls_model_name", "unknown model")
        logger.info("LLM call started: %s, %d messages", model, len(messages[0]))

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        logger.info("LLM call finished in %.1fs", self._elapsed(run_id))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        logger.warning("LLM call failed after %.1fs: %s", self._elapsed(run_id), error)

    def _elapsed(self, run_id: UUID) -> float:
        return time.monotonic() - self._started.pop(run_id, time.monotonic())
