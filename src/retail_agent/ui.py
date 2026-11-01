"""A Streamlit chat page over the same App the CLI uses. Run it with `make ui`."""

import logging

import streamlit as st
import yaml
from dotenv import load_dotenv

from retail_agent.app import App, TurnResult, build_app
from retail_agent.cli import LOG_FILE, _configure_logging, _describe_failure
from retail_agent.config import Settings
from retail_agent.users import load_user

logger = logging.getLogger(__name__)


def main() -> None:
    st.set_page_config(page_title="Retail data agent", page_icon="🛍️")
    settings = _settings()
    names = list(yaml.safe_load(settings.users_path.read_text())["users"])

    with st.sidebar:
        name = st.selectbox("User", names)
        # Switching user starts a new conversation, so one user's brand scope never carries into another's.
        if st.session_state.get("user_name") != name or st.button("New conversation"):
            user = load_user(settings.users_path, name)
            st.session_state.user_name = name
            st.session_state.user = user
            st.session_state.app = build_app(settings, user)
            st.session_state.messages = []  # {"role", "text", "sql"} in the order shown
            st.session_state.confirmation = None  # the question of a paused turn, e.g. a delete
        user = st.session_state.user
        st.caption(f"Brands: {', '.join(sorted(user.brands)) if user.brands else 'all'}")
        st.caption(f"Model: {settings.llm_model}")
        st.caption(f"Logs: {LOG_FILE}")

    st.title("Retail data agent")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            _show_sql(message["sql"])
            st.markdown(message["text"])

    app: App = st.session_state.app
    # A paused turn waits for Yes or Cancel; the delete tool treats anything but "yes" as cancel.
    if st.session_state.confirmation:
        with st.chat_message("assistant"):
            # Two trailing spaces keep the tool's line breaks (one report per line) in markdown.
            st.warning(st.session_state.confirmation.replace("\n", "  \n"))
            yes, cancel = st.columns(2)
            if yes.button("Yes", use_container_width=True):
                _run(lambda: app.confirm("yes"))
            if cancel.button("Cancel", use_container_width=True):
                _run(lambda: app.confirm("no"))
        return

    if question := st.chat_input("Ask a question about the retail data"):
        st.session_state.messages.append({"role": "user", "text": question, "sql": []})
        # Shown now, so the question stays on screen while the agent works.
        with st.chat_message("user"):
            st.markdown(question)
        _run(lambda: app.ask(question))


@st.cache_resource
def _settings() -> Settings:
    # Export .env into the environment so the LLM and Google SDKs find their credentials. Once per server.
    load_dotenv()
    _configure_logging()
    return Settings()


def _run(turn) -> None:
    """Run one turn (a question or a confirmation), store what it produced and redraw the page."""
    try:
        with st.spinner("Working..."):
            result: TurnResult = turn()
    except Exception as error:
        logger.exception("Question failed")
        st.session_state.confirmation = None
        st.session_state.messages.append({"role": "assistant", "text": f"⚠️ {_describe_failure(error)}", "sql": []})
        st.rerun()
    st.session_state.confirmation = result.confirmation
    if not result.confirmation:
        st.session_state.messages.append({"role": "assistant", "text": result.answer, "sql": result.sql})
    st.rerun()


def _show_sql(runs) -> None:
    """Each SQL the model tried, folded: as written, whether it ran or why not, and what BigQuery got."""
    for number, run in enumerate(runs, start=1):
        mark = "✅" if run.error is None else "❌"
        with st.expander(f"SQL {number} of {len(runs)} {mark}"):
            st.code(run.sql, language="sql")
            if run.error is not None:
                st.error(run.error)
            if run.sent_sql is not None:
                st.caption("Sent to BigQuery")
                st.code(run.sent_sql, language="sql")


main()
