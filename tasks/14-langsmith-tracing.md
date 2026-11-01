# 14: LangSmith tracing with our own context

**What to build:** with `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` in `.env`, every question becomes one LangSmith trace (LangChain sends it; no code needed for that). This ticket adds what the default trace lacks: who asked, with which model, in which conversation, and the SQL BigQuery actually ran after the gateway's rewrites. Tracing stays off by default, so reviewers need no account.

Source: [plan, step 6](../docs/plan.md).

**Blocked by:** nothing

**Status:** built 2026-11-01; live check waits for the LangSmith key

**Decisions (2026-11-01):**
- **Each question is a run named `question`**, with metadata `user`, `brands`, `model` and `thread_id`, and the user as a tag. LangSmith groups traces that share a `thread_id` into one thread, so a CLI run's follow-ups and a delete's confirmation read as one conversation. `App` gets the user and model from `assemble`.
- **The gateway is a traced step, `query_gateway`** (`@traceable`), under each `run_sql`. Its metadata adds the final SQL after pseudonyms, small groups and brand scope (`rewritten_sql`), `rows` and `bytes_processed`. A rejected query shows as an error on that step, with the message the model got.
- **The salt never reaches a trace:** it's a BigQuery query parameter, not part of the SQL or the gateway's inputs.
- **Tests never send traces:** a `tests/conftest.py` sets `LANGSMITH_TRACING=false` for every test, whatever the shell has.
- **`langsmith` becomes a direct dependency** in `pyproject.toml`, since the gateway imports it (it was already installed through LangChain).

- [x] `app.py`: `App(agent, schema_text, user, model_name)`; `_run` adds `run_name`, `metadata` and `tags` to the config.
- [x] `query_safety/gateway.py`: `@traceable(name="query_gateway")` on `run`, and the rewritten SQL, rows and bytes added to the current trace step when there is one.
- [x] `pyproject.toml`: `langsmith`; `.env.example`: the three settings explained, still commented out.
- [x] Offline tests (`tests/test_tracing.py`):
  - every LLM call of a question carries the user, brands, model and conversation id as metadata, and the user as a tag;
  - with tracing on and a fake LangSmith client, the gateway step is named `query_gateway` and its metadata holds the rewritten SQL (with `_pseudonymized_`), rows and bytes;
  - tracing off: no client is called and answers are unchanged (the whole suite runs this way).
- [ ] Manual check with the real key: one question as the CEO, one as the Levi's manager, and a follow-up; in LangSmith, filter by user, open the gateway step's rewritten SQL, see the follow-up in the same thread.
- [x] Docs: step 6 in `docs/plan.md`, the HLD's observability section (with the privacy note); say which explainers are stale.

**Built:** as planned, plus one change from review: a resumed turn (the yes or no to a delete) is a run named `confirmation`, not a second `question`. A probe with a fake LangSmith client showed the trace tree `question → model → tools → run_sql → query_gateway`, the gateway step's only input is the model's SQL, and the salt appears nowhere. The test fake now records the trace metadata and tags each LLM call carried. Known and left: the `model` metadata is the configured model, even when the fallback answered (the LLM step inside the trace has the real one); result rows appear in both the `run_sql` and `query_gateway` steps.
