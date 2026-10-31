from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime settings, read from environment variables.

    Provider API keys and GOOGLE_APPLICATION_CREDENTIALS are not fields here:
    each SDK reads its own variable from the environment.
    """

    llm_provider: str = "google_genai"
    llm_model: str = "gemini-3.8-flash"
    # Tried in order when a call to llm_model fails (e.g. 429 or 503). Same provider, so Gemini falls back to Gemini.
    # In .env as a JSON list; [] turns the fallback off.
    llm_fallback_models: list[str] = ["gemini-3.5-flash-lite"]
    # Fail fast on a busy or unreachable model: the Gemini client otherwise retries 6 times with no timeout.
    llm_timeout_seconds: float = 30
    llm_max_retries: int = 1
    # LLM calls allowed per question (a normal one uses 2), to stay within Gemini's free-tier limits.
    max_llm_calls: int = 5
    gcp_project: str
    max_bytes_billed: int = 1_000_000_000
    max_rows: int = 50
    schema_path: Path = Path("schema.yaml")
    prompt_path: Path = Path("prompts/system.md")
