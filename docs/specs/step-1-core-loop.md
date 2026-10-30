# Spec: Step 1, the core loop

Status: **ready-for-agent**. There is no issue tracker yet; move this into a GitHub issue with the `ready-for-agent` label once the repo exists.
Written 2026-10-29. The decisions and rejected alternatives come from the "Step 1 design" section of the plan (`docs/plan.md`).

## Problem Statement

Managers at the retail company want answers to everyday business questions, such as revenue by brand, top products or order trends, from the `thelook_ecommerce` data in BigQuery. Today that means writing BigQuery SQL themselves or waiting for an analyst.

On the project side nothing runs yet. Every later requirement (query safety, brand scoping, PII protection, resilience, report-deletion oversight, observability) needs a working question → answer loop to attach to. Without that loop none of them can be built or shown.

## Solution

A chat in the terminal. The manager types a question in plain English. The agent writes SQL for the dataset and runs it through one controlled path, the **query gateway**, to BigQuery. It reads the rows and answers in plain language, and the CLI shows the SQL that ran. Follow-up questions work within the same run.

The LLM is Gemini by default, on a free Google AI Studio key, with at most 5 LLM calls per question. Other providers (Anthropic, OpenAI, Azure OpenAI) stay available through config but are switched off. The database engine (BigQuery first, behind an adapter) can be swapped too. This step is deliberately thin, but it's structured so steps 2–7 extend it instead of rewriting it.

## User Stories

1. As a manager, I want to type a business question in plain English in my terminal, so that I get an answer without writing SQL.
2. As a manager, I want the answer in plain language, so that I can act on it without reading raw tables.
3. As a manager, I want to see the SQL behind each answer, so that I can trust it or hand it to an analyst to check.
4. As a manager, I want to ask follow-up questions that build on the previous answer (for example, "and for last quarter?"), so that I don't have to repeat context.
5. As a manager, I want answers to come only from the `orders`, `order_items`, `products` and `users` tables, so that the numbers come from company data and not from the model's guesses.
6. As a manager, I want the agent to understand business terms such as revenue, brand and order status, so that my questions map to the right columns.
7. As a manager, I want the agent to tell me when it only saw part of a large result, so that I don't mistake a partial view for the whole picture.
8. As a manager, I want the agent to say plainly when a query returned no rows, so that I don't get a made-up answer.
9. As a manager, I want the agent to fix its own SQL when BigQuery rejects it, so that I still get an answer after a mistake.
10. As a manager, I want the chat to keep going after a failed query, so that one bad question doesn't end my session.
11. As a manager, I want to quit by typing `exit` or pressing Ctrl-D, so that leaving is obvious.
12. As a manager, I want each new run to start a fresh conversation, so that questions from an earlier session don't leak into a new one.
13. As an operator, I want to start the agent with one command, `retail-agent`, so that running it is simple.
14. As an operator, I want all settings in a `.env` file with a documented example, so that I can configure the agent without editing code.
15. As an operator, I want to pick the LLM provider and model through config, so that I can switch between Anthropic, OpenAI, Azure OpenAI and Gemini without code changes.
16. As an operator, I want Gemini on a free Google AI Studio key as the default, so that the agent costs nothing to run and matches how the client's team will test it.
17. As an operator, I want non-default providers to be optional installs that stay switched off unless I configure them, so that setup stays light.
18. As an operator, I want BigQuery access through my existing gcloud login, or a service-account key file if I prefer, so that I don't have to manage new credentials.
19. As an operator, I want queries billed to a GCP project I set in config, so that I know where the cost lands.
20. As an operator, I want every query capped by a maximum-bytes-billed limit, so that one bad query can't run up a large bill.
21. As an operator, I want a clear startup error when a required setting is missing, so that I know what to fix before chatting.
22. As an operator, I want the agent to check the schema file against the real tables at startup, so that a typo or a changed dataset fails fast with a clear message instead of causing confusing SQL errors mid-chat.
23. As an operator, I want that startup check to scan no data, so that it costs nothing.
24. As a maintainer, I want the schema described in one hand-curated file, `schema.yaml`, with short notes where column names don't explain themselves, so that I can improve SQL quality by editing data instead of code.
25. As a maintainer, I want `schema.yaml` to list only the tables and columns the agent should know about, so that the agent's view of the data is decided on purpose.
26. As a maintainer, I want the dataset name defined only in `schema.yaml`, so that it lives in exactly one place.
27. As a maintainer, I want the system prompt in an editable template file, so that persona and tone can change without a code change.
28. As a maintainer, I want the schema loaded into the prompt once at startup, instead of the agent fetching it on every question, so that each question costs fewer tokens and round trips.
29. As a maintainer, I want every query to pass through a single query gateway, so that later safety layers (validation, brand scoping, PII masking) plug into one place.
30. As a maintainer, I want the SQL tool to have no direct access to the database, so that nothing can bypass the gateway.
31. As a maintainer, I want the database behind a small protocol, so that another engine can be added as an adapter without touching the agent, tool or gateway.
32. As a maintainer, I want the BigQuery adapter to report bytes processed for each query, so that later cost checks and observability can use it.
33. As a maintainer, I want adapters to turn engine-specific failures into one common query error, so that the rest of the code handles failures the same way for every engine.
34. As a maintainer, I want the LLM built from config by one factory, so that provider-specific code lives in one place.
35. As a maintainer, I want all wiring in one composition point that accepts a model and a database from outside, so that real runs and tests share exactly the same wiring.
36. As a maintainer, I want query results sent to the model as a compact table capped at a configurable number of rows, so that token use stays predictable.
37. As a maintainer, I want dates and decimals in results turned into plain strings, so that every provider handles them the same way.
38. As a maintainer, I want query errors returned to the model as text instead of raised, so that step 4 can add a retry budget without reshaping the tool.
39. As a maintainer, I want the agent loop to stop at a recursion limit, so that a model stuck fixing SQL can't loop forever.
40. As a maintainer, I want conversation state kept by a LangGraph checkpointer, so that step 5's confirmation interrupt works without restructuring.
41. As a developer, I want the whole test suite to run offline without API keys or network access, so that tests are fast, free and give the same result every time.
42. As a developer, I want a scripted fake model that records every message it receives, so that tests can check exactly what reached the LLM. This is also the basis for step 3's "no PII reaches the LLM" guarantee.
43. As a developer, I want a fake database that records the SQL it receives and returns canned rows, errors or table descriptions, so that I can set up any situation on demand.
44. As a reviewer, I want to run the documented example question with my own free Gemini key and get a sensible answer and its SQL, so that I can see the prototype work end to end.
45. As a reviewer on the free tier, I want each question to use at most 5 LLM calls (usually 2), so that I stay within free-tier rate limits.
46. As a reviewer, I want the design decisions and the rejected alternatives written down, so that I can follow the reasoning (for example, why Deep Agents was not used).
47. As a manager, I want revenue answers to state which definition was used, and to follow my own definition when I give one, so that the numbers match what I mean.

## Implementation Decisions

**Agent and LLM**
- The agent is LangChain `create_agent` (LangGraph underneath), given one tool (`run_sql`), the rendered system prompt and an in-memory checkpointer. Deep Agents, a hand-written `StateGraph` and a framework-free loop were rejected (reasons are in the plan).
- There is no LLM interface of our own. An **LLM factory** turns the configured `provider:model` string into a LangChain chat model through `init_chat_model`. The default is `google_genai:gemini-3.8-flash`, the newest stable Flash model on the free tier, as the client confirmed. Its package is a regular dependency. Anthropic, OpenAI and Azure OpenAI stay available through config but are switched off by default: their packages are optional extras.
- Each question may use at most 5 LLM calls. A normal turn uses 2: one to write SQL, one to answer. Reaching the budget ends the turn with a clear message. This keeps a turn within Gemini's free-tier limits.
- Conversation memory: one checkpointer and one thread per app instance, which is one CLI run.

**Database protocol and BigQuery adapter**
- The **`Database` protocol** has three members:
  - `dialect`: the SQL dialect name. It comes from the adapter, not from the schema file, because it belongs to the engine.
  - `execute(sql, max_rows)`: returns a **QueryResult** with column names, at most `max_rows` rows, a `truncated` flag, and the bytes processed when the engine reports them. The adapter decides how to detect truncation (e.g. fetching one extra row).
  - `describe_tables(names)`: returns the live columns and types for the named tables. It's used only by the startup check.
- Adapters turn engine failures into one common **query error** carrying a readable message. Nothing else is caught, so programming bugs still surface.
- The **BigQuery adapter** bills queries to the configured GCP project, sets `maximum_bytes_billed` on every job, and implements `describe_tables` through the table-metadata API, which runs no query job and scans no bytes. Auth uses Application Default Credentials, and `GOOGLE_APPLICATION_CREDENTIALS` also works.
- LangChain `SQLDatabase` and SQLAlchemy were rejected: they hide dry runs, byte caps and job stats.

**Schema**
- `schema.yaml` holds the fully qualified dataset name and a set of tables. Each table can have a description, and each column has a type and an optional short note. It lists only what the agent should know. Columns that exist in the database but are missing from the file are allowed; step 3 relies on this to keep columns out of view on purpose.
- The **schema module** loads the file once, renders it into a compact text block for the prompt, and checks it against `Database.describe_tables`. Every listed table and column must exist, or startup fails with an error that names the missing items.
- In step 3 the same file gains column tags (`pii`, `aggregate_only`) without changing its structure.

**Prompt**
- The system prompt is an editable template file. It's filled in once at startup with the rendered schema and the adapter's dialect.
- It tells the model to:
  - answer only from query results;
  - use only the listed tables, with fully qualified names;
  - write one SELECT per tool call in the given dialect;
  - say so when a result was cut off or empty;
  - state the revenue definition it used, and follow the user's definition when one is given.

**Query gateway**
- The **query gateway** is the only component that holds the `Database`. Its one operation takes SQL and returns a QueryResult.
- In step 1 it passes queries straight to the adapter, applying the configured row cap. Steps 2–3 add validation, dry-run cost checks, brand scoping and PII masking here.

**`run_sql` tool**
- **Input:** one SQL string. It calls the gateway; it never calls the database directly.
- **On success:** a compact text table (a header row, then the data rows). Dates, timestamps and decimals become plain strings. When the result was cut off, a footer says only the first N rows are shown and more exist.
- **On an empty result:** an explicit "query returned no rows" message.
- **On a query error:** `Error: <message>` goes back to the model, so it can fix the SQL and try again.

**App (composition point)**
- The app is wired in two parts:
  - **build from settings:** creates the real chat model and the BigQuery adapter, and loads the schema file and the prompt template.
  - **assemble:** accepts a model, a database, the schema, the prompt and the settings. It runs the startup schema check, then builds the gateway, the tool and the agent.
- Tests call **assemble** directly with fakes. This is the only place concrete classes are chosen.
- The app's turn API takes a question and returns a **TurnResult**: the final answer text, plus the list of SQL statements the agent ran during that turn, in order, including failed attempts. The SQL is read from the tool calls in the turn's messages.

**CLI**
- A `retail-agent` console script. It builds the app from settings and runs a plain read–print loop:
  - empty lines are ignored;
  - `exit`, `quit` or Ctrl-D quits;
  - each turn prints the SQL that ran and then the answer.
- Startup errors (missing settings, schema check failures) print a clear message and exit with a non-zero status.
- No CLI library is used in this step.

**Config**
- Typed settings come from the environment or `.env`: `LLM_PROVIDER`, `LLM_MODEL`, `GCP_PROJECT`, `MAX_BYTES_BILLED`, `MAX_ROWS` (default 50), `SCHEMA_PATH`, `PROMPT_PATH`, and the API key for whichever provider is configured (`GOOGLE_API_KEY` by default).
- The dataset name is not a setting; it lives in `schema.yaml`.
- `.env.example` documents every field.

**Tooling**
- Python 3.12, `uv`, a `src/` layout, `pytest`.
- Dependencies: LangChain (with LangGraph), the BigQuery client, `pydantic-settings` and a YAML parser. The provider integrations are optional extras.

## Testing Decisions

- **What a good test is:** it drives the app through its turn API, exactly as the CLI does, and checks only behavior visible from outside:
  - the answer;
  - the SQL reported in the TurnResult;
  - the SQL the database received;
  - the exact messages the model received.

  It never checks internal calls, private helpers or how modules are split. A refactor that keeps behavior the same must not break a test.
- **One seam:** tests call **assemble** with two fakes, at interfaces that already exist: the chat model and the `Database` protocol. No other seams, no monkeypatching.
- **Scripted fake model:** it returns a planned sequence of responses (tool calls, then final answers) and records every message list it's sent. It has to accept tool binding, because `create_agent` binds tools to the model.
- **Fake database:** it returns canned QueryResults, or raises the common query error. It reports configured table descriptions for the startup check and records every SQL statement it receives. It sets its own dialect.
- **Cases to cover:**
  1. Happy path: the model calls `run_sql`, the fake database returns rows, and the model answers. The TurnResult holds the answer and that SQL, and the database received the same SQL.
  2. The model's next input contains the rows as a compact table.
  3. Row cap: when the database has more rows than `MAX_ROWS`, the model sees only `MAX_ROWS` rows plus the cut-off footer.
  4. Empty result: the model sees the "no rows" message.
  5. Query error: the model sees `Error: <message>`, the turn continues, the model's corrected SQL runs, and the TurnResult lists both statements in order.
  6. Dates, timestamps and decimals reach the model as plain strings.
  7. Follow-up: a second question to the same app instance includes the first turn's messages in what the model receives.
  8. A new app instance starts with no earlier messages.
  9. Startup check: if `schema.yaml` lists a table or column the fake database doesn't have, assemble fails and the error names it. Extra live columns that the file doesn't list don't cause a failure.
  10. The system message the model receives contains the rendered schema (table names, notes) and the dialect.
  11. A model that never stops calling the tool ends at the 5-call budget with a clear message instead of hanging.
- **Prior art:** none, since the repo is empty. These tests set the pattern for later steps. Step 3's PII tests, for example, reuse the recorded model input to prove that no PII reached the LLM.
- **Manual smoke check (not in pytest):** run the example question, "What were the top 5 brands by revenue last month?", against real BigQuery on Gemini with a free AI Studio key, and confirm it takes at most 5 LLM calls. This also exercises the real startup check. Settings validation is left to `pydantic-settings` and this smoke check.

## Out of Scope

- SQL validation and dry-run cost checks (step 2).
- User identity, `--user`, brand scoping and PII masking (step 3).
- Retry budgets, LLM backoff, falling back between Gemini models, and outage handling (step 4). In step 1, a provider or network failure may end the run.
- Saved reports and confirmed deletion (step 5).
- Structured logging and tracing (step 6).
- The HLD, the technical write-up and the design-only parts (step 7).
- Persistent conversation memory, streaming output, a rich CLI, a second real database adapter, and testing Anthropic, OpenAI or Azure OpenAI (available through config, but not part of step 1's checks).

## Further Notes

- The folder is not a git repo yet. The first task is `git init` plus the `uv` project setup.
- Some details were settled while writing this spec, all consistent with the plan:
  - the dialect comes from the adapter, not from `schema.yaml`;
  - the adapter detects truncation and turns engine errors into a common query error;
  - the app is split into "build from settings" and "assemble";
  - the TurnResult lists every SQL statement run in the turn, including failed attempts.
- Updated 2026-10-30 after the client's second round of answers: Gemini became the default, a 5-call budget per question was added, and revenue answers now state their definition.
- Done when: the manual smoke check returns a sensible answer and its SQL on Gemini's free tier within 5 LLM calls, and the offline test suite passes.
