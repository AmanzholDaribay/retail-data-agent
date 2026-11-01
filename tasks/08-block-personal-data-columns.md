# 08: Block personal data columns

**What to build:** a query that reads a column not listed in `schema.yaml` is rejected before it runs. Email, names, street address, postal code and latitude/longitude are already left out of `schema.yaml`, so they can't reach the model or the output. This is the first PII slice of step 3. Pseudonymous `user_id`, aggregate-only demographics and the minimum group size come later, each as its own slice.

Source: [plan, step 3](../docs/plan.md).

**Blocked by:** nothing (steps 1, 2 and brand scoping are done)

**Status:** done 2026-10-31 (not committed yet)

**Decision (2026-10-31):** an **allow-list**, not a block-list. A column must be listed in `schema.yaml` to be read, the same way a table must be one of the 4 allowed tables. A personal column nobody remembered to tag is blocked anyway (fail closed). The check lives in a new file, `pii.py`, so the guard keeps checking tables and `pii.py` checks columns.

- [x] `pii.py`: a class with one `check(sql)` method that raises `QueryRejected` with a message the model can act on, e.g. "Column `users.email` is not available. Use only the columns in the schema."
- [x] Columns are resolved from the parsed query (sqlglot), not matched by name in the text, so aliases, joins, subqueries and WITH names are followed back to the real table. A column that can't be resolved to a table is rejected (fail closed).
- [x] `SELECT *` (or `t.*`) over a real table is rejected with "name the columns you need". `COUNT(*)` stays allowed.
- [x] Using a whole row as a value is rejected: `SELECT u FROM users u`, `TO_JSON_STRING(u)`.
- [x] The gateway calls the check right after the guard and before brand scoping, so it checks the model's SQL, not our rewritten SQL. `app.py` builds it from `schema.yaml`. Before/after of the gateway line is shown before writing it.
- [x] Offline tests (`tests/test_pii.py`):
  - allowed: a plain select of schema columns; a join with aliases; a WITH query; `COUNT(*)`; a column that exists in two tables, qualified by alias;
  - rejected: `email`, `first_name`, `u.street_address`, `latitude` inside a function (`ROUND(latitude, 2)`), a PII column inside a subquery or WITH, `SELECT *`, `SELECT u.*`, `SELECT u FROM users u`, `TO_JSON_STRING(u)`;
  - a gateway test: a rejected query never reaches the fake database.
- [x] Manual check on real BigQuery: "list 5 customers with their emails" ends in a refusal or an answer without emails, and the log shows `SQL rejected: …` if the model tried. Passed 2026-10-31 on `gemini-3.6-flash`: it answered that names and contact details aren't available, without calling `run_sql`. "3 rows from users with every column" named the 8 schema columns instead of `*`. The model never tried a personal column, so the live run exercised the prompt, and the offline tests are what show the gateway blocks it.
- [x] Docs: say which `docs/*.html` explainers are now stale, and update the step 3 bullet in `docs/plan.md`.

**Built:** `PersonalDataCheck` in `src/retail_agent/pii.py` runs sqlglot's `qualify` against the `schema.yaml` columns with star expansion off (BigQuery runs `*` as written, so expanding it would hide the problem), then rejects any `*` outside `COUNT(*)` and any whole-row reference (`exp.TableColumn`). The gateway calls it right after the guard. The prompt now says to name columns instead of `*`, and that customer names and contact details aren't available, so the model rarely spends an LLM call on a rejection. Four older tests used `SELECT *` or made-up columns and were updated to valid SQL.

**Later slices (not this task):** a safety-net check on returned rows; `user_id` replaced by a salted-hash pseudonym; demographic columns only in aggregated queries; hiding groups below `MIN_GROUP_SIZE`.
