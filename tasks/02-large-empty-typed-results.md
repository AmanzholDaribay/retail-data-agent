# 02: Large, empty and typed results reach the model correctly

**What to build:** query results always reach the model in a predictable, compact form.
- A question that returns hundreds of rows shows the model only the first `MAX_ROWS` rows plus a note that more exist, and the answer says it's based on part of the result.
- A query that returns nothing produces an honest "no rows" answer instead of a made-up one.
- Dates, timestamps and decimals arrive as plain text, so every provider handles them the same way.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 01 (Walking skeleton)

**Status:** done 2026-10-31

- [x] The adapter returns at most `max_rows` rows and sets `truncated` when more exist; it decides how to detect that, for example by fetching one extra row. Built from BigQuery's own `total_rows`, which `QueryResult` now carries so the note can give the real count; `truncated` is a property derived from it, so the two can't disagree. Formatting lives in `result_format.py`.
- [x] The gateway applies `MAX_ROWS` from settings (default 50).
- [x] When the result was cut off, the `run_sql` output ends with a footer saying only the first N rows are shown and more exist.
- [x] An empty result gives an explicit "query returned no rows" message.
- [x] Dates, timestamps and decimals become plain strings in the tool output.
- [x] The system prompt tells the model to say when a result was cut off or empty.
- [x] Offline tests through **assemble** pass:
  - the model's next input contains the rows as a compact table;
  - with more rows than `MAX_ROWS`, the model sees exactly `MAX_ROWS` rows plus the footer;
  - an empty result shows the model the "no rows" message;
  - dates, timestamps and decimals reach the model as plain strings.
- [x] Manual check: a question that returns many rows (for example, listing every product of a large brand) gives an answer that says it's partial.
  - **Failed 2026-10-31** on `gemini-3.6-flash`: "List every product of the brand Calvin Klein with its retail price." The model saw the footer but kept re-querying (the same list twice, plus counts and a summary by category) until the 5-call budget ended the turn, so the user got no answer. Cause: the prompt offers "say it's partial *or* aggregate in SQL to see all of it", and when the user asks for a full list the model takes the second branch. Also, the budget message throws away the results already fetched.
  - **Fixed and passed 2026-10-31** on `gemini-3.5-flash-lite`: the prompt now says to answer a list from the rows shown and give the total, and to aggregate only for totals, averages and rankings. The same question was answered in 2 LLM calls: "There are 497 products by the brand Calvin Klein in the catalog. Here are the first 50: …". The call budget was changed too, so the last allowed call always answers from the results so far (see the plan's step 1 notes).
