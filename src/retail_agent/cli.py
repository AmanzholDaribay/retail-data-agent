from dotenv import load_dotenv

from retail_agent.app import build_app
from retail_agent.config import Settings


def main() -> None:
    # Export .env into the environment so the LLM and Google SDKs find their credentials.
    load_dotenv()
    app = build_app(Settings())

    print("Retail data agent. Ask a question, or type 'exit' to quit.")
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

        result = app.ask(question)
        for sql in result.sql:
            print(f"\n[SQL]\n{sql}")
        print(f"\n{result.answer}")
