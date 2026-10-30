import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from retail_agent.app import build_app
from retail_agent.config import Settings

LOG_FILE = Path("logs/retail-agent.log")

logger = logging.getLogger(__name__)


def main() -> None:
    # Export .env into the environment so the LLM and Google SDKs find their credentials.
    load_dotenv()
    _configure_logging()
    settings = Settings()
    app = build_app(settings)
    logger.info("Started with model %s:%s", settings.llm_provider, settings.llm_model)

    print(f"Retail data agent ({settings.llm_model}). Ask a question, or type 'exit' to quit.")
    print(f"Logs: {LOG_FILE}")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break

        print("Working...")
        try:
            result = app.ask(question)
        except Exception as error:
            logger.exception("Question failed")
            print(f"\n{_describe_failure(error)}\nDetails: {LOG_FILE}")
            continue
        for number, sql in enumerate(result.sql, start=1):
            title = "SQL" if len(result.sql) == 1 else f"SQL {number} of {len(result.sql)}"
            print(_block(title, sql, CYAN))
        print(_block("ANSWER", result.answer, GREEN))


CYAN = "36"
GREEN = "32"
# Colors only on a real terminal, and never when NO_COLOR is set (https://no-color.org).
_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _block(title: str, body: str, color: str) -> str:
    """A titled block, e.g. `── SQL ────` then the body, with the title colored on a terminal."""
    header = f"── {title} " + "─" * max(4, 40 - len(title))
    if _USE_COLOR:
        header = f"\033[1;{color}m{header}\033[0m"
    return f"\n{header}\n{body.strip()}"


def _configure_logging() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _describe_failure(error: Exception) -> str:
    text = str(error)
    if "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower():
        return "The model is busy right now (503). Try again in a minute, or set another model in LLM_MODEL."
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "The model's rate limit is reached (429). Wait a minute, or set another model in LLM_MODEL."
    if "504" in text or "DEADLINE_EXCEEDED" in text or "timed out" in text.lower() or isinstance(error, TimeoutError):
        return "The model didn't answer in time. Try again, or set another model in LLM_MODEL."
    return f"That question failed: {type(error).__name__}: {text[:300]}"
