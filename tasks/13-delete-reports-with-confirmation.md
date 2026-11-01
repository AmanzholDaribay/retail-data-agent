# 13: Delete reports, with confirmation

**What to build:** "delete all reports mentioning Client X" and "delete all the reports we made in this conversation" (the brief's two examples) show the exact list of the user's matching reports and delete them only after the user types `yes`. Anything else cancels, and the chat goes on. Nothing is ever deleted without that typed yes, and a user can only delete their own reports. This is the last slice of step 5.

Source: [plan, step 5](../docs/plan.md). Builds on [ticket 12](12-list-my-reports.md) (`ReportStore.find`).

**Blocked by:** nothing (ticket 12 is done)

**Status:** built 2026-11-01

**Decisions (2026-11-01, proposed):**
- **The model can only propose.** A `delete_reports(mentioning, this_conversation)` tool takes the same filters as `list_reports` and selects with the same `ReportStore.find(owner, …)`, so it deletes exactly what a list with the same filters shows. The owner comes from `--user`.
- **Code asks, inside the chat.** The tool pauses the turn with LangGraph's `interrupt(...)`, carrying the exact list. The CLI prints it and asks `Type yes to delete these 2 reports, anything else cancels:`. The answer resumes the turn (`Command(resume=answer)`), so the conversation keeps its state and the next question works as usual. Checked in a probe: pause, resume and the final message cost 1 LLM call in total.
- **Only `yes` deletes** (trimmed, any case). "y", "ok", "sure" and an empty line all cancel. Cancelling returns "Cancelled. Nothing was deleted." and changes nothing.
- **Delete by id, with the owner in the SQL:** `ReportStore.delete(owner, ids)` runs `DELETE … WHERE owner = ? AND id IN (…)` and returns how many rows went. Hard delete, as the plan says.
- **Exactly the list that was shown.** On resume, LangGraph runs the tool again from the top, so `find` would run twice. The ids shown go into the interrupt, and on `yes` the tool deletes those ids, not a fresh search. (A report saved by another CLI run in between isn't deleted by surprise.)
- **Nothing matched:** no question is asked; the answer is "No saved reports mentioning 'Client X'. Nothing was deleted."
- **`return_direct`** like the other report tools: the outcome is the answer.
- **Not LangChain's `HumanInTheLoopMiddleware`:** it pauses *before* the tool runs and shows the tool's arguments (`mentioning="Client X"`), not the reports that match. The brief needs the exact list, which only exists after `find`.

```
"delete all reports mentioning Client X"
  → model: delete_reports(mentioning="Client X")            (1 LLM call)
  → tool: find(owner, "Client X") → [3, 7] → interrupt(list)  ── turn paused
CLI: "2 reports: 7 · … · Client X Q2 review / 3 · … · Client X Q1
      Type yes to delete these 2 reports, anything else cancels: "
  → user types yes → Command(resume="yes") → delete(owner, [3, 7]) → "Deleted 2 reports." (answer)
  → user types no  → "Cancelled. Nothing was deleted."            (answer)
```

**Before → after:**

```python
# app.py: before
@dataclass(frozen=True)
class TurnResult:
    answer: str
    sql: list[str]

class App:
    def ask(self, question: str) -> TurnResult

# after
@dataclass(frozen=True)
class TurnResult:
    answer: str                    # empty while waiting for a confirmation
    sql: list[str]
    confirmation: str | None = None  # the list + question to show, when the turn is paused

class App:
    def ask(self, question: str) -> TurnResult
    def confirm(self, answer: str) -> TurnResult   # resumes the paused turn with the typed answer
```

```python
# cli.py: after app.ask(...)
while result.confirmation:
    result = app.confirm(input(result.confirmation + " "))
```

- [x] `reports.py`: `ReportStore.delete(owner, ids) -> int`.
- [x] `tools.py`: `make_delete_reports(store, owner)` returns `delete_reports` (`return_direct=True`): find, interrupt with the list and ids, then delete those ids on `yes` or cancel.
- [x] `app.py`: `TurnResult.confirmation`; `ask` and `confirm` both turn a paused turn into a `TurnResult` with the confirmation text; `assemble` adds the tool.
- [x] `cli.py`: the confirmation loop; Ctrl-D or Ctrl-C at the prompt cancels.
- [x] `prompts/system.md`: one line: to delete reports, call `delete_reports` with the same filters; the code asks the user, so don't ask yourself.
- [x] Offline tests (`tests/test_reports.py`):
  - the brief's two examples: by text and by this conversation, each shows the exact list and deletes only after `yes`;
  - `no`, `y`, an empty answer each cancel and leave every report;
  - user A can't delete user B's reports, also when both mention the text; `delete(owner, ids)` with B's id deletes nothing;
  - nothing matched: no confirmation, nothing deleted;
  - on `yes`, only the ids that were shown are deleted, even if a matching report was saved in between;
  - after a cancel or a delete, the next question works in the same conversation.
- [x] Manual check on Gemini: save two reports, "delete all the reports we made in this conversation", answer `no` (both stay), then again with `yes` (both gone); "delete all reports mentioning Q1" as another user finds nothing.
- [x] Docs: step 5 in `docs/plan.md` marked done, the HLD's reports section; say which explainers are stale.

**Built:** as planned, with one fix from review. When one reply calls `delete_reports` twice (e.g. "delete the Client X and Client Y reports"), two interrupts are pending, and resuming with a single value crashed. `App.confirm` now resumes the first pause by its id, so each delete gets its own question, one at a time. `App.ask` and `App.confirm` share a private `_run`. Live check on Gemini as the CEO, with two seeded "Client X" reports: `no` cancelled and kept both, `yes` deleted exactly those two, "show my reports" showed the rest; the Levi's manager's "delete reports mentioning Q1" found nothing. The "this conversation" example is covered by an offline test, not the live run (it would have cost two more saved reports of free-tier calls). Not guarded: a caller that uses `ask` instead of `confirm` while a turn waits; the CLI's loop prevents it.
