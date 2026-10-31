from pathlib import Path
from typing import Any

from fakes import FakeDatabase, ScriptedChatModel
from langchain_core.messages import ToolMessage

from retail_agent.app import App, assemble
from retail_agent.config import Settings
from retail_agent.schema import load_schema

ROOT = Path(__file__).parents[1]
ORDER_ITEMS = "`bigquery-public-data.thelook_ecommerce.order_items`"
PRODUCTS = "`bigquery-public-data.thelook_ecommerce.products`"


def assemble_app(model: ScriptedChatModel, database: FakeDatabase, **settings: Any) -> App:
    """The real schema file and prompt template, with a fake model and database."""
    return assemble(
        model=model,
        database=database,
        schema=load_schema(ROOT / "schema.yaml"),
        prompt_template=(ROOT / "prompts" / "system.md").read_text(),
        settings=Settings(gcp_project="test-project", **settings),
    )


def tool_output_seen_by_model(model: ScriptedChatModel) -> str:
    """The run_sql output, as the model received it on its second call."""
    message = model.received[1][-1]
    assert isinstance(message, ToolMessage)
    return message.text
