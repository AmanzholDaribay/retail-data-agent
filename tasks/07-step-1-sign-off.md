# 07: Step 1 sign-off

**What to build:** confirmation that step 1 meets its "done when" as a whole, not ticket by ticket. The core loop works end to end on Gemini's free tier the way the client's reviewers will run it, the offline suite is green, and the plan reflects what was actually built.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 02 (Large, empty and typed results), 03 (Recover from bad SQL), 04 (Follow-up questions), 05 (Fail fast on bad schema or settings), 06 (Other providers and setup guide)

**Status:** ready-for-agent

- [ ] The full offline test suite passes with no API keys and no network access.
- [ ] On Gemini with a free AI Studio key, against real BigQuery:
  - "What were the top 5 brands by revenue last month?" returns a sensible answer, states the revenue definition, and shows its SQL;
  - the question takes at most 5 LLM calls;
  - a follow-up question works.
- [ ] The README's example run matches the current behavior.
- [ ] The plan's status marks step 1 as done, and any differences from the step 1 spec are noted in the plan.
