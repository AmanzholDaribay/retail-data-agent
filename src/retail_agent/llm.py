from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from retail_agent.config import Settings


def build_model(settings: Settings, model_name: str) -> BaseChatModel:
    """Build a chat model of the configured provider. Switching providers is a settings change."""
    return init_chat_model(
        f"{settings.llm_provider}:{model_name}",
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
