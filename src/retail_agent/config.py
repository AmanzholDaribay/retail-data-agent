from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime settings, read from environment variables.

    Provider API keys and GOOGLE_APPLICATION_CREDENTIALS are not fields here:
    each SDK reads its own variable from the environment.
    """

    llm_provider: str = "google_genai"
    llm_model: str = "gemini-3.8-flash"
    gcp_project: str
    max_bytes_billed: int = 1_000_000_000
    max_rows: int = 50
    schema_path: Path = Path("schema.yaml")
    prompt_path: Path = Path("prompts/system.md")
