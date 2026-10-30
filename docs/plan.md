# Initial plan: Retail data agent (OpsFleet assignment)

Status: step 1 in progress (ticket 01 built, live check pending). Last updated 2026-10-30.

## How we build

Step by step: first one tiny slice that works end to end, then grow it.

- The first slice only has to work, not be complete. It still follows good structure: clear module boundaries, small interfaces, config kept out of code.
- Each step adds one layer of complexity and leaves the project working.
- Later steps extend the code instead of rewriting it.

## The brief in short

A CLI chat agent that answers retail analytics questions over `bigquery-public-data.thelook_ecommerce` (`orders`, `order_items`, `products`, `users`). It runs on Gemini: the client's team tests it with their own free Google AI Studio key, so it must stay within free-tier rate limits, at roughly 2–5 LLM calls per question. The provider stays swappable through config (see "Step 1 design"). Expected effort is 6–12 hours. The submission is a public GitHub repo with docs, source code and an architecture diagram. Grading focuses on system design, the technical explanation and an elegant prototype.

**Built in the prototype**
- Safety and PII masking: analysis only, no PII in the output, and each user sees only their own brands.
- High-stakes oversight: confirmation before deleting saved reports. Users can delete only their own.
- Resilience: recover from SQL errors and empty results without running up cost, and survive third-party outages.
- Observability.
- CLI.

**Design only (documented, not built)**
- Golden Knowledge bucket (Question → SQL → Report trios).
- Learning loop at the user and system level.
- QA and evals.
- Persona and tone updates without redeploying.

**Client answers (Harry, 2026-10-27)**
- Access is scoped by **brand**. Each manager has one or more brands, and the CEO sees everything. A config file or access table is fine. Customer and order data are visible only through purchases of in-scope products.
- Never show first or last name, email, street address, postal code or exact coordinates. City, state, country, age, gender and traffic source may appear only in aggregates.
- PII must never reach the LLM, so it is masked before data goes to the model. Matching on keywords or column names alone is not robust enough.
- Individual customers can be referenced by `user_id` or a stable pseudonym (e.g. a salted hash) so follow-up questions work.
- Report deletion is a **hard delete** with no recovery. Show the exact list first and require strict confirmation. Only the author can see or delete a report.

**Client answers, round 2 (Harry 2026-10-29, Vlad 2026-10-30)**
- **LLM provider:** Gemini is preferred. The team tests with its own free Gemini API key from Google AI Studio, so the agent must work within free-tier rate limits, at roughly 2–5 LLM calls per question. By default, fall back between Gemini models only; other providers stay available in config but switched off. A swappable provider is "a nice touch", and the design should explain the trade-off.
- **Small groups:** hide aggregate groups below a minimum size. Default 5, configurable, and noted in the design doc.
- **Revenue:** the sum of `sale_price`, excluding Cancelled and Returned items, is a good default. State the definition in the answer and let users override it.
- **Saved reports:** private to their author. A report holds the question, the SQL and the answer.
- **IP address:** it appears only in `events`, which the prototype doesn't touch, so there's nothing to handle. List it as PII in the design.

## Step 1: the core loop (start here)

`retail-agent` CLI → agent → `run_sql` tool → query gateway → database adapter (BigQuery) → rows back to the LLM → plain-language answer, printed together with the SQL that ran.

Why this comes first: every other requirement sits on this loop. Scoping and masking filter what it returns, self-correction retries a step inside it, report deletion is another tool on the same agent, and observability records what it does.

**In scope**
- A chat loop in the terminal that remembers earlier turns within the same run.
- `schema.yaml` loaded once at startup into the system prompt, and checked once against the live tables.
- One tool that runs a SELECT and returns rows.
- The LLM turns the rows into an answer, and the CLI shows the SQL it ran. Revenue answers state the definition used, and users can give their own.
- At most 5 LLM calls per question (a normal turn uses 2), to fit Gemini's free tier.
- `maximum_bytes_billed` on every query: one line that prevents surprise costs.
- Project setup: git repo, Python project, `.env.example` for the LLM keys and the GCP project.
- Offline tests with a fake database and a scripted fake model.

**Out of scope for now:** SQL validation, scoping, masking, retry budgets, saved reports, logging.

**Key structural decision:** the database is reached only through one small module, the **query gateway**. It is the only path from the LLM to the data. Later steps plug SQL validation, brand scoping, PII masking and cost limits into it without touching the rest of the code. The gateway sits on top of a swappable database adapter, so the policy layer and the driver stay separate.

### Step 1 design

Settled 2026-10-29. Full spec, with user stories and test cases: [specs/step-1-core-loop.md](specs/step-1-core-loop.md). Broken into 7 tickets in [`tasks/`](../tasks/): 01 is the walking skeleton, 02–06 each depend only on 01, and 07 is the sign-off. Explainers: [architecture map](architecture.html) and [why `run_sql` has a factory](tool-factory.html).

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | **Agent framework:** LangChain `create_agent` (LangGraph underneath). It provides the model ↔ tools loop, and later layers attach as middleware (retries, call limits, the human-in-the-loop delete confirmation). | **Deep Agents:** its planning, virtual filesystem and sub-agent tools are built for long tasks. For a job of 1–3 tool calls per question they add tokens, latency and attack surface. **A hand-written `StateGraph`:** more code for now; still possible later if we need explicit control flow, without changing the tools or the gateway. **No framework:** we would rewrite the loop, checkpointing and interrupts ourselves. |
| 2 | **LLM:** no interface of our own. `build_model(settings)` calls `init_chat_model("<provider>:<model>")` and returns a LangChain chat model. Switching providers is a config change. Provider packages are optional extras. | **Our own `LLMClient` wrapper:** duplicates LangChain's `BaseChatModel` and fights `create_agent`, which expects a LangChain chat model. |
| 3 | **Providers:** default `google_genai:gemini-3.8-flash`, the newest stable Flash model on the free tier (per Google's docs, 2026-10-28), as the client confirmed on 2026-10-29. Its package is a regular dependency. Anthropic, OpenAI and Azure OpenAI stay available through config but are switched off by default: their packages are optional extras. Falling back between Gemini models (e.g. to `gemini-3.5-flash-lite` on rate limits) comes in step 4. | **Claude as the default** (the first pick): the client's reviewers test with a free Gemini key only. **Falling back to other providers:** the client asked for Gemini-only fallback, and other providers need keys reviewers don't have. |
| 4 | **Database:** our own `Database` protocol with `dialect`, `execute(sql, max_rows) → QueryResult(columns, rows, truncated, bytes_processed)` and `describe_tables(names)`. The `BigQueryDatabase` adapter sets `maximum_bytes_billed` on every job. Another engine means another adapter behind the same protocol. | **LangChain `SQLDatabase` / SQLAlchemy:** many engines for free, but they hide the BigQuery features we need soon: dry-run byte estimates (step 2) and job stats (step 6). |
| 5 | **Layering:** `run_sql` tool → QueryGateway (policy; in step 1 it only passes queries through and caps rows) → database adapter (driver only). The tool never calls the adapter directly. Safety lives in the gateway; middleware is only for agent-level concerns. | **Safety in middleware:** it would depend on the framework, and LangChain's PII middleware matches patterns, which Harry called not robust enough. |
| 6 | **Schema:** `schema.yaml` is the schema in cleaner form: tables, columns and types, plus short notes where a name doesn't explain itself (status values, `sale_price` vs `retail_price`, joins). It holds the dataset name too. It is loaded once at startup and rendered into the system prompt, and checked once against the live tables through `describe_tables()` (BigQuery metadata API, no bytes scanned). A missing table or column fails fast. Step 3 adds `pii` and `aggregate_only` tags to the same file. | **Reading the schema from the database:** column names without meaning lead to worse SQL. **Per-question `list_tables` / `get_schema` tools:** an extra round trip and extra tokens on every question. |
| 7 | **Prompt:** the template `prompts/system.md`, filled in with the schema and the dialect. | **A string in the code:** persona and tone changes would need a code change. |
| 8 | **Tool contract:** on success, a compact table capped at `MAX_ROWS` (default 50), with a footer when the result was cut off, and dates and decimals as plain strings. On failure, `Error: <message>` goes back to the model so it can fix the SQL. A budget of 5 LLM calls per question (a normal turn uses 2: one to write SQL, one to answer) keeps a turn inside Gemini's free-tier limits; reaching it ends the turn with a clear message (ticket 03). | **JSON rows:** noticeably more tokens. **Raising the exception:** bad SQL would crash the turn. |
| 9 | **Memory:** `InMemorySaver` checkpointer with one `thread_id` per CLI run. Follow-ups work, and the step 5 delete confirmation (a LangGraph interrupt) needs a checkpointer anyway. | **No memory:** breaks follow-ups. **Persistent memory:** not needed yet. |
| 10 | **CLI:** a `retail-agent` console script running a plain `input()` loop. It prints the answer and the SQL that ran; `exit` or Ctrl-D quits. `--user` arrives in step 3. | **Typer / Rich:** not needed yet. |
| 11 | **Config:** `pydantic-settings` reading `.env`: `LLM_PROVIDER`, `LLM_MODEL`, `GCP_PROJECT`, `MAX_BYTES_BILLED`, `MAX_ROWS`, `SCHEMA_PATH`, `PROMPT_PATH`, plus the provider API key (`GOOGLE_API_KEY` by default). The dataset name lives only in `schema.yaml`. BigQuery auth uses Application Default Credentials (`gcloud auth application-default login`); `GOOGLE_APPLICATION_CREDENTIALS` also works. Queries are billed to `GCP_PROJECT`. | |
| 12 | **Tooling and tests:** Python 3.12, `uv`, `src/` layout, `pytest`. One test seam: tests call `assemble(model, database, …)` with a scripted fake chat model (it records everything it was sent) and a `FakeDatabase`, then drive the app's turn API the way the CLI does. This runs offline, for free, with the same result every time, plus one manual live run on Gemini's free tier. | **A second real adapter (DuckDB/SQLite):** nothing needs one yet. |

Layout:

```
schema.yaml              # the schema in cleaner form, with short notes
prompts/system.md        # system prompt template
.env.example
src/retail_agent/
  config.py              # Settings (pydantic-settings, reads .env)
  llm.py                 # build_model(settings) -> chat model via init_chat_model
  schema.py              # load schema.yaml, render it for the prompt, check it against the database
  db/
    base.py              # Database protocol, QueryResult
    bigquery.py          # BigQueryDatabase adapter
  gateway.py             # QueryGateway: policy layer (pass-through now) + row cap
  tools.py               # run_sql: calls the gateway, formats the result or error for the LLM
  agent.py               # build_agent(model, tools, prompt, checkpointer) via create_agent
  app.py                 # build_app(settings) makes the real parts; assemble(model, database, …) wires them (tests pass fakes)
  cli.py                 # chat loop; prints the answer and the SQL that ran
tests/
  fakes.py               # FakeDatabase, scripted fake chat model
```

**Done when:** asking "What were the top 5 brands by revenue last month?" returns a sensible answer and the SQL behind it, on Gemini with a free AI Studio key, in at most 5 LLM calls.

## Steps after that

### Step 2: Query guard

Lives inside the gateway.

- Parse the SQL (e.g. with `sqlglot`) and accept only a single SELECT over the 4 allowed tables.
- Dry-run each query to get the bytes it would scan, and reject anything above the limit. The dry run becomes a new method on the `Database` protocol.

**Done when:** DML, DDL, multiple statements and other tables are rejected, with a clear message the model can act on.

### Step 3: Brand scoping and PII protection

Lives inside the gateway. This is the most heavily graded part.

- Identity: the user is chosen at CLI start (e.g. `--user`). A config file maps users to brands, and the CEO gets all brands.
- Scoping is enforced in the data layer, not the prompt. Table references are rewritten to brand-filtered versions, so orders and users are reachable only through in-scope products. (Choice to make here: rewrite the SQL or use scoped views.)
- PII columns are tagged in `schema.yaml`, resolved from the parsed query rather than matched by name, and blocked. Results are checked again before rows reach the model.
- `user_id` is replaced by a salted-hash pseudonym that stays stable across turns.
- Demographic columns (city, state, country, age, gender, traffic source) are allowed only in aggregated queries.
- Aggregate groups smaller than `MIN_GROUP_SIZE` customers (default 5, configurable) are hidden, so a small group can't identify a person. How group size is measured is a step 3 design choice.

**Done when:** a manager of brand X can't see brand Y's data, and adversarial prompts can't get PII into the model's input or into the output.

### Step 4: Resilience

- SQL error: the error goes back to the model to fix, within a fixed retry budget. The dry-run catches mistakes before they cost anything.
- Empty result: the model rechecks its filters and assumptions within the same budget, then says plainly that nothing matched.
- Gemini 429/5xx: exponential backoff with jitter, then fall back to another Gemini model (default `gemini-3.8-flash` → `gemini-3.5-flash-lite`, configurable). The fallback stays within Gemini, as the client asked. Seen on 2026-10-30: `gemini-3.8-flash` returned 503 "high demand", and the Gemini SDK's own silent retries made the CLI look frozen for minutes. So set a short timeout and few SDK retries, and fall back quickly. Done early (2026-10-30): each LLM call has a 30s timeout and 1 SDK retry (`LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`), and a failed question prints a clear message while the CLI keeps running. The Gemini fallback itself is still to do. BigQuery: timeouts, plus retries on transient errors.
- During an outage the user gets a clear message, and the CLI keeps running.

**Done when:** a bad column, a forced 429 and a network drop each end in either a recovered answer or a clean message, never a crash.

### Step 5: Saved reports and deletion

- A local store (e.g. SQLite) that records the owner of every report. A report holds the question, the SQL and the answer (confirmed by the client). Tools: save, list, delete.
- Ownership is checked in the store query, not left to the prompt.
- The model can only *propose* a deletion. Code, not the LLM, shows the exact list, asks for explicit typed confirmation (a LangGraph interrupt), and then hard-deletes.

**Done when:** user A can't see or delete user B's reports, and nothing is deleted without confirmation.

### Step 6: Observability

- Started early (2026-10-30): plain-text logs in `logs/retail-agent.log` covering each question, each LLM call with its duration or error, and each SQL statement with its duration, rows and bytes.
- **Tracing with LangSmith** (decided 2026-10-30). LangChain and LangGraph send traces to LangSmith with no code changes once `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` are set. Each question becomes one trace showing every LLM call, tool call, SQL, latency and token count. Tracing stays off by default, so reviewers don't need a LangSmith account.
- Add our own context to each trace: user, model, SQL bytes scanned, rows, retries. Use trace metadata/tags on the agent call, and the gateway's run as a traced step.
- Privacy: traces contain prompts and tool results, so they go to LangSmith's servers. After step 3 the gateway masks PII before rows reach the model, so traces only hold masked data. Note this in the design doc.
- The local log file stays as the offline fallback when tracing is off.

**Done when:** one question's LangSmith trace shows how its answer was produced (LLM calls, SQL, rows, timing), and the log file gives the same story when tracing is off.

### Step 7: Docs

The docs grow with each step, and this step finishes them.

- HLD with a Mermaid architecture diagram.
- Technical write-up: cloud services, LLM and framework choice with reasoning, data flow, error handling, setup with an example run, and how each requirement is met.
- Design for the design-only parts: Golden Knowledge bucket, learning loop, QA and evals, persona and tone updates without redeploying.
- Points the client asked to see covered: the provider trade-off (swappable providers vs. a Gemini-only default), the minimum group size, and IP address listed as PII.

## Requirement → step

| Requirement | Step |
|---|---|
| CLI | 1 |
| Safety: read-only queries, cost limit | 1–2 |
| Brand scoping, PII masking | 3 |
| Resilience | 4 |
| High-stakes oversight | 5 |
| Observability | 6 |
| HLD, write-up, design-only parts | 7 (grows alongside) |

## Open decisions

- **Scoping mechanism.** Rewrite the SQL or use scoped views. Decide in step 3.
- **Measuring group size.** How the gateway knows how many customers are behind an aggregate row. Decide in step 3.
