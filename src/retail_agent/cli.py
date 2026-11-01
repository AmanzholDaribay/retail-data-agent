import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from retail_agent.app import build_app
from retail_agent.config import Settings
from retail_agent.users import UnknownUser, load_user

LOG_FILE = Path("logs/retail-agent.log")

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask retail analytics questions in plain language.")
    parser.add_argument("--user", required=True, help="who is asking, one of the names in the users file")
    parser.add_argument("--verbose", action="store_true", help="show every SQL the model tried, with ✅ or ❌ and the reason")
    args = parser.parse_args()
    # Export .env into the environment so the LLM and Google SDKs find their credentials.
    load_dotenv()
    _configure_logging()
    settings = Settings()
    try:
        user = load_user(settings.users_path, args.user)
    except UnknownUser as error:
        sys.exit(str(error))
    except ValidationError as error:
        sys.exit(f"{settings.users_path} is invalid:\n{error}")
    app = build_app(settings, user)
    brands = ", ".join(sorted(user.brands)) if user.brands else "all"
    logger.info("Started with model %s:%s", settings.llm_provider, settings.llm_model)
    logger.info("User %s, brands: %s", user.name, brands)
    logger.info("Schema:\n%s", app.schema_text)

    if args.verbose:
        print(_block(f"SCHEMA ({settings.schema_path})", app.schema_text, MAGENTA))
        print()
    print(f"Retail data agent ({settings.llm_model}). Signed in as {user.name}, brands: {brands}.")
    print("Ask a question, or type 'exit' to quit.")
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
            # A paused turn, e.g. a delete, waits for a typed answer; Ctrl-D or Ctrl-C counts as no.
            while result.confirmation:
                try:
                    reply = input(_block("CONFIRM", result.confirmation, YELLOW) + " ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    reply = ""
                result = app.confirm(reply)
        except Exception as error:
            logger.exception("Question failed")
            print(f"\n{_describe_failure(error)}\nDetails: {LOG_FILE}")
            continue
        # The plain run shows only the answer. --verbose shows every SQL the model tried, whether it ran or why not,
        # and the SQL BigQuery got after the gateway's rewrites (pseudonyms, brand scope).
        if args.verbose:
            for number, run in enumerate(result.sql, start=1):
                title = "SQL" if len(result.sql) == 1 else f"SQL {number} of {len(result.sql)}"
                body = run.sql + ("\n✅ ran" if run.error is None else f"\n❌ {run.error}")
                if run.sent_sql is not None:
                    body += f"\nSent to BigQuery:\n{run.sent_sql}"
                print(_block(title, body, CYAN))
        print(_block("ANSWER", result.answer, GREEN))


CYAN = "36"
YELLOW = "33"
MAGENTA = "35"
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
