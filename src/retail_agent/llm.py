from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from retail_agent.config import Settings


def build_model(settings: Settings) -> BaseChatModel:
    """Build the configured chat model. Switching providers is a settings change."""
    return init_chat_model(f"{settings.llm_provider}:{settings.llm_model}")
