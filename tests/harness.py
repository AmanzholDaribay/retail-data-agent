from pathlib import Path
from typing import Any

from fakes import FakeDatabase, ScriptedChatModel
from langchain_core.messages import ToolMessage

from retail_agent.app import App, assemble
from retail_agent.config import Settings
from retail_agent.schema import load_schema
from retail_agent.users import User

ROOT = Path(__file__).parents[1]
ORDER_ITEMS = "`bigquery-public-data.thelook_ecommerce.order_items`"
PRODUCTS = "`bigquery-public-data.thelook_ecommerce.products`"
ORDERS = "`bigquery-public-data.thelook_ecommerce.orders`"
USERS = "`bigquery-public-data.thelook_ecommerce.users`"
CEO = User(name="ceo", brands=None)


def assemble_app(
    model: ScriptedChatModel,
    database: FakeDatabase,
    fallback_models: tuple[ScriptedChatModel, ...] = (),
    user: User = CEO,
    **settings: Any,
) -> App:
    """The real schema file and prompt template, with fake models and a fake database."""
    return assemble(
        model=model,
        database=database,
        schema=load_schema(ROOT / "schema.yaml"),
        prompt_template=(ROOT / "prompts" / "system.md").read_text(),
        settings=Settings(gcp_project="test-project", **settings),
        user=user,
        fallback_models=fallback_models,
    )


def tool_output_seen_by_model(model: ScriptedChatModel) -> str:
    """The run_sql output, as the model received it on its second call."""
    message = model.received[1][-1]
    assert isinstance(message, ToolMessage)
    return message.text
