import pytest


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    """Tests never send traces to LangSmith, even when the shell has LANGSMITH_TRACING=true."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
