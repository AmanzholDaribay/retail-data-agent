# 04: Follow-up questions within a run

**What to build:** a manager can ask a follow-up that builds on the previous answer, such as "…and the month before?", without repeating the context. Each new run of `retail-agent` starts with a fresh conversation.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 01 (Walking skeleton)

**Status:** done 2026-10-31

- [x] The agent keeps conversation state in an in-memory LangGraph checkpointer (`InMemorySaver`, created in **assemble**).
- [x] Each app instance (one CLI run) uses a single conversation thread: `App` makes one random `thread_id` and passes it on every question.
- [x] Found while building it: with memory, a question that fails mid-query (an outage, which ticket 03 raises on purpose) left the model's tool call saved with no result. Providers reject that, so every later question in the run would have failed. Now `App.ask` saves `Error: this query did not run because of …` as the result of each unanswered tool call before re-raising. The question still ends at once, the query isn't retried on its own, and "try again" works because the model can see what failed.
- [x] Offline tests through **assemble** pass:
  - a second question to the same app instance includes the first turn's messages in what the model receives;
  - a new app instance starts with no earlier messages;
  - a question that fails mid-query (e.g. BigQuery is down) leaves a valid conversation, so the next question still works;
  - the 5-call budget still counts each question separately. LangChain keeps that counter out of the checkpoint (`run_model_call_count` is an `UntrackedValue`), so it starts at 0 on every question.
- [x] Manual check: "What were the top 5 brands by revenue last month?" followed by "and the month before?" gives a correct follow-up answer. Passed 2026-10-31 on `gemini-3.5-flash-lite` through `build_app` and `App.ask`, the same path the CLI uses: the follow-up reused the brand query with `INTERVAL 2 MONTH`. (`gemini-3.6-flash` timed out at 30 s and `gemini-3.8-flash` returned 503 at the time.)
