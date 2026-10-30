# 05: Fail fast on a wrong schema file or missing settings

**What to build:** if `schema.yaml` names a table or column that doesn't exist in the real database, or a required setting is missing, `retail-agent` says exactly what's wrong and exits before the first question. This replaces confusing SQL errors in the middle of a chat. The check costs nothing: it reads table metadata and scans no data.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 01 (Walking skeleton)

**Status:** ready-for-agent

- [ ] The `Database` protocol gains `describe_tables(names)`, which returns the live columns and types for the named tables.
- [ ] The BigQuery adapter implements `describe_tables` through the table-metadata API: no query job, no bytes scanned.
- [ ] The fake database reports configured table descriptions.
- [ ] **assemble** checks every table and column listed in `schema.yaml` against `describe_tables`. If any are missing, startup fails with an error that names each missing item.
- [ ] Live columns that `schema.yaml` doesn't list are allowed; leaving a column out of the file is a deliberate choice.
- [ ] A missing required setting fails startup with a message naming the setting.
- [ ] The CLI prints startup errors as a clear message (no traceback) and exits with a non-zero status.
- [ ] Offline tests through **assemble** pass:
  - a table missing from the fake database fails startup and names it;
  - a missing column fails startup and names it;
  - extra live columns don't cause a failure.
- [ ] Manual check: misspelling a column in `schema.yaml` makes `retail-agent` exit non-zero with a message naming that column, and the BigQuery job history shows no query job for the check.
