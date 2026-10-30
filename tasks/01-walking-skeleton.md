# 01: Walking skeleton: ask a question, get an answer and its SQL

**What to build:** the thinnest working path through every layer. A manager runs `retail-agent`, types "What were the top 5 brands by revenue last month?" and gets a plain-language answer from real BigQuery data on Gemini, with the SQL that produced it printed alongside. Every layer exists in its simplest form, structured so later tickets extend it instead of rewriting it. The offline test seam lands here too.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** None (can start immediately)

**Status:** done

- [x] The folder is a git repo and a `uv`-managed Python 3.12 project, with a `src/` layout, `pytest`, and a `retail-agent` console script.
- [x] Typed settings load from the environment or `.env`: `LLM_PROVIDER`, `LLM_MODEL`, `GCP_PROJECT`, `MAX_BYTES_BILLED`, `MAX_ROWS`, `SCHEMA_PATH`, `PROMPT_PATH`, plus the provider API key. A `.env.example` exists.
- [x] The LLM factory builds a LangChain chat model from the configured `provider:model` through `init_chat_model`. The default is `google_genai:gemini-3.8-flash` (changed from Claude after the client's answers on 2026-10-29). There is no LLM interface of our own.
- [x] The `Database` protocol exposes `dialect` and `execute(sql, max_rows)`, which returns a QueryResult (columns, rows, truncated, bytes processed).
- [x] The BigQuery adapter bills queries to `GCP_PROJECT` and sets `maximum_bytes_billed` on every job.
- [x] `schema.yaml` holds the dataset name and the 4 tables, with column types and short notes where names don't explain themselves (status values, `sale_price` vs `retail_price`, joins). It's loaded once at startup and rendered, together with the adapter's dialect, into the system-prompt template.
- [x] The system prompt tells the model to state the revenue definition it used, and to follow the user's own definition when one is given.
- [x] The query gateway is the only component holding the `Database`; in this ticket it passes queries straight through.
- [x] `run_sql` calls only the gateway and returns the rows to the model as a compact text table.
- [x] The agent is built with LangChain `create_agent`, using `run_sql` and the rendered system prompt.
- [x] The app is wired in two parts:
  - **build from settings** creates the real model and the BigQuery adapter;
  - **assemble** takes a model, a database, the schema, the prompt and the settings, and wires everything.

  The app's turn API takes a question and returns a TurnResult: the answer, plus the SQL statements run during the turn.
- [x] The CLI runs a plain read–print loop: it prints the SQL and then the answer, ignores empty lines, and quits on `exit`, `quit` or Ctrl-D.
- [x] Test fakes:
  - a scripted fake chat model that accepts tool binding and records every message it receives;
  - a fake database that records the SQL it receives and returns canned results.
- [x] Offline tests that go through **assemble** with the fakes pass:
  - **happy path:** the TurnResult holds the answer and the SQL, and the fake database received that same SQL;
  - the system message the model receives contains the rendered schema and the dialect.
- [x] Manual check: the example question, run on Gemini (free AI Studio key) against real BigQuery, returns a sensible answer and its SQL. Passed 2026-10-30 on `gemini-3.6-flash`, because `gemini-3.8-flash` was returning 503 "high demand" at the time. The answer gave the top 5 brands for September 2026, stated the revenue definition, and took 3 LLM calls.
