# 15: Show each SQL's outcome with --verbose

**What to build:** `retail-agent --user ceo --verbose` (`make run_logs`) shows every SQL the model wrote in this turn, each marked ✅ ran or ❌ with the reason (the guard's rejection or the BigQuery error). The plain run (`make run`) shows only the SQL that ran, then the answer: today it also shows rejected SQL with no mark, so a rejected attempt looks like it ran. The log file stays as it is.

Source: [plan, step 6](../docs/plan.md) (observability), agreed in chat 2026-11-01.

**Blocked by:** nothing. `app.py` is also being changed by [ticket 14](14-langsmith-tracing.md); keep the edits apart.

**Status:** built 2026-11-01

**Decisions (2026-11-01):**
- **The outcome comes from the turn's messages.** `run_sql` already returns `Error: …` for a rejected or failed query, and that `ToolMessage` is in the turn. `App._run` pairs each `run_sql` call with its result by `tool_call_id`, the same check `save_report` uses to leave out failed SQL. No change to the gateway and no extra LLM call.
- **`TurnResult.sql` becomes a list of `SqlRun(sql, error)`**, where `error` is `None` when the query ran. The order stays the order the model wrote them in.
- **The SQL shown is the model's**, not the rewritten one: it's readable, and the rewritten SQL is in the log and the LangSmith trace (ticket 14).
- **`--verbose` is a CLI flag**, like `--user`: it changes what one run prints, not a setting.

```
make run                        make run_logs (--verbose)
── SQL ───────────               ── SQL 1 of 2 ────────────
SELECT state, SUM(...)          SELECT email, ...
                                ❌ Column users.email is not allowed
── ANSWER ────────                
...                             ── SQL 2 of 2 ────────────
                                SELECT state, SUM(...)
                                ✅ ran

                                ── ANSWER ────────────────
```

- [x] `app.py`: `SqlRun` dataclass; `_run` fills `TurnResult.sql` with each `run_sql` call and its error.
- [x] `cli.py`: `--verbose`; the plain run prints only SQL with no error.
- [x] Offline tests: a rejected then fixed query gives `[SqlRun(bad, "…"), SqlRun(good, None)]`; existing `result.sql` asserts compare `.sql`; the CLI's plain run hides the rejected SQL and `--verbose` shows ❌ with the reason.
- [ ] Manual check on Gemini: `make run_logs`, ask for customer emails, see ❌ then ✅.
- [x] Docs: step 6 in `docs/plan.md`; say which explainers are stale.

**Built:** as planned. The CLI tests (`tests/test_cli.py`) run `cli.main()` with a stand-in app and keep the developer's `.env` out of the test run. The manual Gemini check is still open.
