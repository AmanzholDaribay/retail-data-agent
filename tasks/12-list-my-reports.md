# 12: List my reports

**What to build:** "show my reports", "which of my reports mention Brazil?" and "what reports did we make in this conversation?" list the asking user's saved reports: id, title, date, and whether it's from this conversation. A user never sees anyone else's reports, the CEO included. This is the second slice of step 5, and it builds the selection that ticket 13 (delete) will reuse, so "delete all reports mentioning Client X" deletes exactly what "list reports mentioning Client X" shows.

Source: [plan, step 5](../docs/plan.md). Builds on [ticket 11](11-create-and-save-a-report.md).

**Blocked by:** nothing (ticket 11 is done)

**Status:** built 2026-11-01

**Decisions (2026-11-01, proposed):**
- **A `list_reports(mentioning, this_conversation)` tool.** Both filters are optional: `mentioning` is text to look for, `this_conversation` keeps only reports from the current CLI run. The owner and the conversation id come from the code, as in `save_report`, so the model can't ask for someone else's reports.
- **Ownership is in the SQL.** `ReportStore.find(owner, mentioning, conversation_id)` always has `WHERE owner = ?`. There is no method that reads reports without an owner.
- **"Mentioning" is a plain text search**, case-insensitive, over the title, the question and the report text (`LIKE '%' || ? || '%'`, with `%` and `_` in the search text escaped). It finds "Client X" exactly as typed, which is what the brief's delete example needs. The plan's FTS5 index is dropped for now: it splits text into words and ranks matches, which this prototype's few reports don't need. It stays in the design doc as the way to scale.
- **No extra LLM call.** Like `save_report`, `list_reports` has `return_direct=True`: the list is the answer. "Show my reports" costs 1 LLM call. If the model wants to say more about the list, it can't in this turn; that's fine for a list.
- **Shown per report:** `3 · 2026-11-01 16:11 UTC · Q1 2026 Retail Performance Report (this conversation)`, newest first, and "No saved reports" / "No saved reports mention 'Brazil'" when nothing matches. Not the full text: reading one report back comes later, if at all.
- **No report file yet:** listing returns "No saved reports" without creating the file, as saving does only on the first save.

```
"which of my reports mention Brazil?" → model: list_reports(mentioning="Brazil")
  → ReportStore.find(owner="ceo", mentioning="Brazil")   ← owner from --user, never from the model
  → "2 reports mention 'Brazil': …" is the answer (1 LLM call)
```

- [x] `reports.py`: a `SavedReport` dataclass (id, created_at, conversation_id, title) and `ReportStore.find(owner, mentioning=None, conversation_id=None) -> list[SavedReport]`, newest first; an empty list when the file doesn't exist.
- [x] `tools.py`: `make_list_reports(store, owner)` returns `list_reports` (`return_direct=True`), which passes the conversation id from the config when `this_conversation` is true and formats the list.
- [x] `app.py`: `assemble` adds the tool.
- [x] `prompts/system.md`: one line: to show, find or count the user's saved reports, call `list_reports`.
- [x] Offline tests (`tests/test_reports.py`):
  - user A's list never has user B's reports, also when B's report mentions the searched text;
  - `mentioning` matches title, question or report text, ignoring case; `%` and `_` are taken literally;
  - `this_conversation` keeps only the current run's reports;
  - newest first; "No saved reports" with no file, and no file is created;
  - through the app: "show my reports" is answered in 1 LLM call with the list.
- [x] Manual check on Gemini: save a report, then "show my reports" and "which reports mention Q1?" in the same run, and in a new run as another user (sees none).
- [x] Docs: step 5 in `docs/plan.md` (and the FTS5 change), the HLD's reports section; say which explainers are stale.

**Built:** as planned, with changes from review and the live run. The search is a contains-check over Python's `casefold` (a small SQLite function), not `LIKE`: SQLite folds only A–Z, so "ümraniye" missed "Ümraniye", and there are no wildcards left to escape. `find` creates the table if a file exists without it, so an empty file lists nothing instead of failing. The header names both filters when both are set, and reads "Your saved reports mentioning 'Q1' (1):" (the live run showed "1 saved report mention 'Q1'"). A small `_describe` helper formats one line. Live check on Gemini: as the CEO, "show my reports" and "which reports mention Q1?" listed the Q1 report saved in ticket 11, "what reports did we make in this conversation?" in a new run said none, and the Levi's manager saw none.
