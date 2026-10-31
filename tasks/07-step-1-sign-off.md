# 07: Step 1 sign-off

**What to build:** confirmation that step 1 meets its "done when" as a whole, not ticket by ticket. The core loop works end to end on Gemini's free tier the way the client's reviewers will run it, the offline suite is green, and the plan reflects what was actually built.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 02 (Large, empty and typed results), 03 (Recover from bad SQL), 04 (Follow-up questions). Tickets 05 and 06 moved out of step 1 on 2026-10-30, to steps 4 and 7.

**Status:** done 2026-10-31

- [x] The full offline test suite passes with no API keys and no network access. 28 passed, 2026-10-31.
- [x] On Gemini with a free AI Studio key, against real BigQuery:
  - "What were the top 5 brands by revenue last month?" returns a sensible answer, states the revenue definition, and shows its SQL;
  - the question takes at most 5 LLM calls;
  - a follow-up question works.
  - **Passed 2026-10-31 on `gemini-3.5-flash-lite`**: 3 LLM calls (the first SQL failed on `TIMESTAMP_SUB … MONTH` and the model fixed it), the answer stated the revenue definition and showed both SQL statements, and the follow-up "And how did the first one do the month before?" was answered in 2 calls. The default `gemini-3.8-flash` returned 504 DEADLINE_EXCEEDED twice at the 30 s timeout. `gemini-3.6-flash` ran out of its free-tier daily quota: **20 requests per day per model**.
- [x] The plan's status marks step 1 as done, and any differences from the step 1 spec are noted in the plan.
