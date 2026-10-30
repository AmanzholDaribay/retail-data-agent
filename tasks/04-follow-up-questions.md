# 04: Follow-up questions within a run

**What to build:** a manager can ask a follow-up that builds on the previous answer, such as "…and the month before?", without repeating the context. Each new run of `retail-agent` starts with a fresh conversation.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 01 (Walking skeleton)

**Status:** ready-for-agent

- [ ] The agent keeps conversation state in an in-memory LangGraph checkpointer.
- [ ] Each app instance (one CLI run) uses a single conversation thread.
- [ ] Offline tests through **assemble** pass:
  - a second question to the same app instance includes the first turn's messages in what the model receives;
  - a new app instance starts with no earlier messages.
- [ ] Manual check: in the CLI, "What were the top 5 brands by revenue last month?" followed by "and the month before?" gives a correct follow-up answer.
