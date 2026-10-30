import logging
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
        for sql in result.sql:
            print(f"\n[SQL]\n{sql}")
        print(f"\n{result.answer}")


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
