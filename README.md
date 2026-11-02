# Retail data agent

A chat agent for executives: ask retail questions in plain language, get answers, discuss them,
and save reports with insights and action items. It writes SQL over BigQuery's public
`thelook_ecommerce` dataset, and every query passes a gateway that masks customer data and
limits each user to their own brands.

- **CLI** (`retail-agent`), as the brief asks, and a **Streamlit page** for demos.
- **Gemini** by default, on a free Google AI Studio key, in 2–5 LLM calls per question.
- Design: [docs/hld.md](docs/hld.md). Visual explainers (PDF): [docs/pdf/](docs/pdf/).

## Setup

Needs Python 3.12+ and access to BigQuery (the free tier is enough).

**1. Install**, with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra ui
```

or with pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**2. Sign in to Google Cloud** (BigQuery queries are billed to your project; the dataset is public):

```bash
gcloud auth application-default login
```

**3. Configure:** copy `.env.example` to `.env` and fill in two values:

```
GOOGLE_API_KEY=   # free key from https://aistudio.google.com/apikey
GCP_PROJECT=      # your Google Cloud project id
```

Everything else has a working default (model, fallback model, limits). LangSmith tracing is
optional: uncomment the `LANGSMITH_*` lines.

## Run

```bash
make run                     # CLI as the CEO (all brands)
make run WHO=levis_manager   # CLI as a brand manager (Levi's only)
make run_logs                # CLI that also shows every SQL, ✅ or ❌, and the SQL sent to BigQuery
make ui                      # Streamlit page at http://localhost:8501
```

Without `make`: `uv run retail-agent --user ceo` (or `retail-agent --user ceo` after pip).
Users and their brands live in [users.yaml](users.yaml): `ceo`, `levis_manager`, `denim_lead`.

## Example run

```
$ make run
Retail data agent (gemini-3.8-flash). Signed in as ceo, brands: all.
Ask a question, or type 'exit' to quit.

> Top 3 brands by revenue in March 2026

── ANSWER ──────────────────────────────────
In March 2026, the top three brands by revenue (sum of sale price for items
not Cancelled or Returned) were:
1. Calvin Klein: $4,158.30
2. Diesel: $4,131.30
3. Carhartt: $3,878.27
```

More questions to try, grouped by requirement: [docs/demo-questions.md](docs/demo-questions.md).

## How each requirement is handled

| Requirement | In the prototype | Where |
|---|---|---|
| Ask, discuss, multi-step questions, reports | Agent loop with conversation memory; independent queries sent together; reports saved with insights and action items | `agent.py`, `app.py`, `tools.py` |
| Safety & PII masking | Every query goes through one gateway: a SELECT-only guard, a column allow-list (no names, emails, addresses), customer pseudonyms, demographics only in groups of 5+, each user's brands only, a byte cap | `query_safety/` |
| High-stakes oversight | Deleting reports shows the exact list and waits for an explicit yes; users see and delete only their own reports | `tools.py`, `reports.py` |
| Resilience | SQL errors and empty results go back to the model to fix, within a 5-call budget; Gemini 429/5xx falls back to another Gemini model; failures never crash the chat | `agent.py`, `app.py` |
| Observability | Log file of every question, LLM call and SQL; optional LangSmith traces with the rewritten SQL, rows and bytes | `observability.py`, `logs/` |
| Golden Knowledge, learning loop, evaluation, persona | Design only, as the brief allows | [docs/hld.md](docs/hld.md) |

## Why LangChain and LangGraph

I've worked with LangChain and LangGraph before and completed their courses, so this was the
tool I could build on fastest and most reliably. It fits this task well:

- **Tools are easy to add.** A tool is a decorated function, so charts, email or web search
  plug in without touching the agent loop.
- **Agent-level rules are middleware.** The LLM call budget and the model fallback attach to
  the loop instead of being written into it.
- **Interrupts** give the delete confirmation: the run pauses, the user answers, the run resumes.
- **Observability is built in.** LangSmith traces every model and tool step, and LangGraph
  Studio shows the graph's nodes while debugging.
- **The provider is a config change** (`init_chat_model`), so Gemini can be swapped without code.

Data policy deliberately stays outside the framework, in the query gateway, so it doesn't depend
on any one agent library.

## Tests

```bash
uv run pytest
```

With pip, install pytest first (it's a dev tool, not in `requirements.txt`):

```bash
pip install pytest && pytest
```

185 tests, using a scripted model and a fake database, so they need no keys or network.

## Project layout

```
src/retail_agent/
  cli.py, ui.py        CLI and Streamlit page
  app.py               one conversation: question in, answer + SQL out
  agent.py             agent loop, call budget, model fallback
  tools.py             run_sql, save/list/delete reports
  query_safety/        the gateway: guard, personal data, brand scope
  db/                  database protocol and the BigQuery adapter
prompts/system.md      the system prompt
schema.yaml            tables and columns the agent may use, with notes
users.yaml             users and their brands
docs/                  design (hld.md) and visual explainers
```

## Known limits

- Subtracting two allowed totals can still single out one customer (needs noise, i.e.
  differential privacy).
- Returned rows aren't checked a second time; BigQuery transient errors aren't retried.
- Gemini's free tier allows about 20 requests per model per day; a long session can run out.
