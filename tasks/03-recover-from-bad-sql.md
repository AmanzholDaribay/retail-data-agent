# 03: Recover from bad SQL

**What to build:** when BigQuery rejects a query, the agent sees the error, fixes its SQL and still answers the manager, and the chat keeps going. The CLI shows both the failed and the corrected SQL, so it's clear what happened. A question never uses more than 5 LLM calls, the budget the client set for Gemini's free tier. A model that keeps calling the tool stops there with a clear message instead of hanging.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 01 (Walking skeleton)

**Status:** done 2026-10-31

- [x] Adapters turn engine failures into one common query error with a readable message. Any other exception still surfaces as a bug. Known infrastructure reasons (outages, rate limits, access, billing, quota) still raise; every other rejected query goes to the model, since SQL mistakes are open-ended and the call budget caps a wrong guess. Errors are matched on BigQuery's reason, not the exception class, because the client raises an over-the-cap query as a 500.
- [x] `run_sql` catches only that common query error and returns `Error: <message>` to the model.
- [x] After an error the turn continues, so the model can send corrected SQL.
- [x] The TurnResult lists every SQL statement run during the turn, in order, including failed attempts, and the CLI prints them all.
- [x] Each question may use at most 5 LLM calls (a normal turn uses 2). Reaching the budget ends the turn with a clear message, and the chat continues with the next question. Built as `CallBudget`, a small subclass of LangChain's `ModelCallLimitMiddleware` that ends the turn with a message for the user. (Found in ticket 01: `create_agent` sets the recursion limit to 9,999 by default, so nothing caps a turn until this budget exists.)
- [x] Offline tests through **assemble** pass:
  - the fake database fails the first query; the model sees `Error: <message>`, its corrected SQL runs, and the TurnResult lists both statements in order;
  - a model that never stops calling the tool ends at the 5-call budget with a clear message instead of hanging;
  - an answer on the 5th call is returned as a normal answer.
- [x] Manual check: a question that leads to a SQL error on real BigQuery (for example, after temporarily giving a column a misleading note in `schema.yaml`) still ends in an answer, and both SQL statements are shown. Passed 2026-10-31 without changing `schema.yaml`, because real errors happened on their own. On `gemini-3.5-flash-lite`, "top 5 brands by revenue last month?" first compared a TIMESTAMP with a DATETIME; the model read the error, fixed the SQL and answered, and the TurnResult held both statements. In a separate live report test on `gemini-3.6-flash`, one of 6 parallel queries failed ("Column name id is ambiguous") and only that query was fixed and re-run.
