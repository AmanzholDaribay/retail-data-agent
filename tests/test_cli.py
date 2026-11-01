from retail_agent import cli
from retail_agent.app import SqlRun, TurnResult

REJECTED = "SELECT * FROM products"
FIXED = "SELECT COUNT(id) AS n FROM products"
SENT = "SELECT COUNT(id) AS n FROM products WHERE brand IN ('Levi's')"  # as the gateway rewrote it


class OneAnswerApp:
    """Answers every question with one rejected query, then the fixed one that ran."""

    schema_text = "Table products"

    def ask(self, question: str) -> TurnResult:
        return TurnResult(
            answer="There are 12 products.",
            sql=[SqlRun(REJECTED, error="Name the columns you need instead of using *."), SqlRun(FIXED, error=None, sent_sql=SENT)],
        )


def run_cli(monkeypatch, capsys, *flags: str) -> str:
    """Run the CLI for one question as the CEO, and return what it printed."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)  # keep the developer's .env out of the test run
    monkeypatch.setenv("GCP_PROJECT", "test-project")
    monkeypatch.setattr(cli, "build_app", lambda settings, user: OneAnswerApp())
    monkeypatch.setattr("sys.argv", ["retail-agent", "--user", "ceo", *flags])
    typed = iter(["How many products are there?", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    cli.main()
    return capsys.readouterr().out


def test_a_plain_run_shows_only_the_answer(monkeypatch, capsys):
    printed = run_cli(monkeypatch, capsys)

    assert "There are 12 products." in printed
    assert FIXED not in printed
    assert REJECTED not in printed
    assert SENT not in printed


def test_verbose_shows_every_sql_with_its_outcome(monkeypatch, capsys):
    printed = run_cli(monkeypatch, capsys, "--verbose")

    assert f"{REJECTED}\n❌ Name the columns you need instead of using *." in printed
    assert f"{FIXED}\n✅ ran\nSent to BigQuery:\n{SENT}" in printed
    assert printed.index(REJECTED) < printed.index(FIXED)


def test_verbose_shows_the_answer_after_the_sql(monkeypatch, capsys):
    printed = run_cli(monkeypatch, capsys, "--verbose")

    assert printed.index(SENT) < printed.index("There are 12 products.")
