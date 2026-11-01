import secrets
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


def _random_salt() -> SecretStr:
    return SecretStr(secrets.token_hex(16))


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
    users_path: Path = Path("users.yaml")
    # The secret behind customer pseudonyms (cust_72ac84091163). Unset or empty: a random one per run, so
    # pseudonyms stay the same within a run but change between runs.
    pseudonym_salt: SecretStr = Field(default_factory=_random_salt)
    # Totals of age, city and other aggregate_only columns over fewer customers than this are hidden.
    min_group_size: int = Field(default=5, ge=1)

    @field_validator("pseudonym_salt")
    @classmethod
    def _replace_empty_salt(cls, salt: SecretStr) -> SecretStr:
        """An empty salt would make every pseudonym a plain hash of the id, which anyone can recompute."""
        return salt if salt.get_secret_value() else _random_salt()
