# 11: Create and save a report

**What to build:** the thinnest end-to-end slice of step 5. When the user asks for a report ("create a report for Q1 including insights and action items for Q2"), the agent runs the queries it needs, writes findings, insights and action items, and saves the report to a local SQLite store with its owner, conversation id and creation time. A saved report holds the question, the SQL that ran and the report text (confirmed by the client). Listing and deleting come in the next slices; this one only creates and saves.

Source: [plan, step 5](../docs/plan.md). Research: [report-questions-db-agent.md](../docs/research/report-questions-db-agent.md) and [report-queries.html](../docs/report-queries.html).

**Blocked by:** nothing (steps 1–3 done; step 3's row check can come later)

**Status:** built 2026-11-01

**Step 5 in slices:**

| Ticket | Slice | Done when |
|---|---|---|
| **11 (this)** | Create and save a report | A Q1 report request ends with a saved report and an answer, within the 5-call budget |
| 12 | List my reports | "Show my reports" lists only the asking user's reports |
| 13 | Delete with confirmation | Both brief examples ("mentioning Client X", "made in this conversation") show the exact list and delete only after a typed yes; cancelling changes nothing |

**Decisions (2026-11-01, proposed):**
- **The model saves the report through a tool, `save_report(title, report)`.** The model writes the report text as the tool's argument; the code adds everything the model must not choose: the owner (the `--user` name), the conversation id (the App's `thread_id`), the time, the user's question and the SQL that ran this turn (read from the conversation state, through LangGraph's `ToolRuntime`). The model can't save a report for someone else.
- **No extra LLM call.** `save_report` has `return_direct=True`: its result (the report plus "Saved as report 3") is the final answer, so the model doesn't need another call to say "done". A report costs: 1 call for a batch of parallel queries, 1 call to write and save it. 2–3 calls, inside the 5.
- **When to save:** the prompt tells the model to call `save_report` when the user asks for a report, and to answer normal questions as today. A normal question never writes to the store.
- **Batch first** (from the research note): the prompt asks the model to send independent queries together as parallel `run_sql` calls in one reply, and to use at most 2 rounds before writing. No planner node, no sub-agents.
- **SQLite file, path from settings:** `REPORTS_PATH` (default `data/reports.sqlite3`, ignored by git). One table `reports(id, owner, conversation_id, created_at, question, sql, title, report)`. `sql` is the list of statements as JSON. The table is created on first use. An FTS5 index for "mentioning Client X" waits for ticket 13.
- **Report budget (open decision in the plan):** keep 5 calls. The manual check shows whether a Q1 report fits; ask Harry for more only if it doesn't.
- **PII:** a report is built only from `run_sql` results, which the gateway has already masked, so the store holds no personal data either.

```
"create a Q1 report…" → model: 3–5 run_sql in one reply → gateway → BigQuery (parallel)
                      → model: save_report(title, report) → ReportStore.save(owner, conversation, question, sql, …)
                      → "Saved as report 3" + the report → CLI
```

- [x] `reports.py`: `Report` (dataclass) and `ReportStore(path)` with `save(...) -> int`; ownership is a column every later query filters on.
- [x] `tools.py`: `make_save_report(store, owner)` returns the `save_report` tool (`return_direct=True`), which reads the question and this turn's SQL from the state and the conversation id from the config.
- [x] `app.py`: `assemble` builds the store from `settings.reports_path` and passes both tools to the agent.
- [x] `Settings.reports_path` (`REPORTS_PATH`); `.env.example` explains it; `data/` in `.gitignore`.
- [x] `prompts/system.md`: the batch-first lines from the research note, plus when to call `save_report` and what a report contains (findings with numbers, insights, action items, the period and filters used).
- [x] Offline tests (`tests/test_reports.py`), with the scripted model and fake database:
  - a scripted report turn (two parallel `run_sql`, then `save_report`) saves one row with the right owner, conversation id, question, both SQL statements, title and text, and returns the report as the answer, in 2 LLM calls;
  - a normal question saves nothing;
  - two `App`s (two CLI runs) save under different conversation ids;
  - the store file and table are created when missing.
- [x] Manual check on Gemini and BigQuery: as the CEO, "create a report for Q1 2026 including insights and action items for Q2" ends with a saved report within 5 calls; the log shows the calls and SQL; the row is in `data/reports.sqlite3`.
- [x] Docs: update step 5 in `docs/plan.md` (and the report-budget decision), the HLD's reports section; say which explainers are stale.

**Built:** as planned, with three changes from review. The saved SQL leaves out queries that were rejected or failed, so it's only the SQL behind the numbers. On the last allowed call (no tools), the model is told to write the report in full and say it can't be saved this time, instead of silently not saving it. The connection is closed even if the insert fails. `_current_turn` moved from `app.py` to `agent.py` as `current_turn`, because the tool needs it too. Live check on `gemini-3.6-flash` and BigQuery as the CEO: 6 queries in one reply, run in parallel, then `save_report` on the second LLM call; one row saved with all 6 statements. Known gap: if the model sends `save_report` together with another tool, the turn goes on and could save twice; the prompt asks for it as the last step, alone.
