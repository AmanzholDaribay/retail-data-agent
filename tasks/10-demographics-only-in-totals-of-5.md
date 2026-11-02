# 10: Demographics only in totals of 5 or more customers

**What to build:** demographic columns (age, gender, city, state, country, traffic source) reach the model only inside a total, and only when that total covers at least `MIN_GROUP_SIZE` customers (default 5). A query that lists them row by row is rejected. A query that totals them gets `HAVING COUNT(DISTINCT <customer id>) >= 5` added, so BigQuery drops the small groups itself. This is the third PII slice of step 3, and it settles the plan's open decision "measuring group size".

Source: [plan, step 3](../docs/plan.md). Explainer: [small-groups.pdf](../docs/pdf/small-groups.pdf).

**Blocked by:** nothing (tickets 08 and 09 are done)

**Status:** built 2026-11-01

**Decisions (2026-11-01, proposed):**
- **One ticket for both rules.** Each closes a hole the other leaves open: without the first, `SELECT id, city FROM users` lists everyone's city; without the second, a "total" over 1 customer is that customer.
- **A tag in `schema.yaml`:** `aggregate_only: true` on `users` age, gender, city, state, country, traffic_source and on `orders.gender`. Config, not code: protecting another column is a one-line edit.
- **Checked per SELECT, in the SELECT that reads the table.** Every SELECT in the query (main query, subqueries, WITH steps) that reads a tagged column straight from a real table must be a total: it has a GROUP BY or an aggregate function outside a window (`COUNT`, `SUM`, `AVG` …). If not, it's rejected. If so, the `HAVING` goes on that same SELECT, ANDed with any `HAVING` the model wrote. A WITH step that only passes `city` through for a later step to total is rejected; the model is told to total it in the step that reads the table. Strict, but simple to follow and to test.
- **Which id to count:** the customer id of the table the tagged column comes from (`users.id`, `orders.user_id`), by the alias the query uses. It runs before pseudonyms, and counting distinct pseudonyms counts the same customers.
- **`HAVING` and not dropping rows afterwards:** SQL applies `HAVING` before `LIMIT`, so "top 3 countries" still returns 3 safe rows. BigQuery accepts `HAVING` without `GROUP BY`, so a single total ("average age of buyers in Tromsø") is covered too.
- **The number goes into the SQL as a literal** (`>= 5`), not a query parameter: it isn't a secret, and the log then shows exactly what ran. (The explainer said `@min_group_size`; it gets fixed.)
- **Where it runs:** after the column check, before pseudonyms. It reads `qualify`'s output on a copy of the query to find which table each column comes from, and edits the original query, so the model's column names (no `_col_1`) reach the results unchanged.
- **Known limit, design doc only:** subtracting two allowed totals ("all countries" minus "all but Iceland") still reveals one customer. Real protection needs noise (differential privacy).

```
model SQL → guard → column check → small groups (new) → customer pseudonyms → brand scope → byte check → BigQuery
```

- [x] `schema.py`: `Column.aggregate_only: bool = False`, read from `schema.yaml`; the system prompt shows it, e.g. `- city STRING (only in totals)`.
- [x] `schema.yaml`: tag the 7 columns.
- [x] `query_safety/step2_personal_data/small_groups.py`: `SmallGroups.apply(sql) -> str` raises `QueryRejected` for a listing, returns the query with `HAVING` added for a total, and the same string when no tagged column is read.
- [x] The gateway calls it after `PersonalDataCheck.check` and before `CustomerPseudonyms.apply`.
- [x] `Settings.min_group_size` (`MIN_GROUP_SIZE`, default 5, at least 1); `.env.example` explains it.
- [x] `prompts/system.md`: one line saying tagged columns work only inside totals and groups under 5 customers are never shown.
- [x] Offline tests (`tests/test_step2_small_groups.py`):
  - rejected: `SELECT id, city FROM users`; `SELECT DISTINCT country FROM users`; a WITH step that passes `city` through; a subquery `WHERE user_id IN (SELECT id FROM users WHERE country = 'Iceland')`; a window `COUNT(*) OVER (PARTITION BY city)`;
  - rewritten: `GROUP BY city` gets `HAVING COUNT(DISTINCT users.id) >= 5`; `orders.gender` counts `user_id`; a joined alias (`u.country`) counts `u.id`; the model's own `HAVING` is kept and ANDed; a total with no `GROUP BY`; a total inside a WITH step gets the `HAVING` on that step;
  - unchanged: revenue per brand; top customers by pseudonym; `MIN_GROUP_SIZE` comes from settings;
  - gateway: a rejected query never reaches the fake database; for a brand manager the final SQL has both the `HAVING` and the brand filter.
- [x] Manual check on real BigQuery: "average age of customers per city" as the CEO comes back without groups under 5; "list 5 customers with their city" ends in a refusal or a total.
- [x] Docs: update the step 3 bullet and the open decision in `docs/plan.md`; mark `small-groups.html` as built; say which other explainers are stale.

**Built:** as planned, plus one rule found in review: ARRAY_AGG, STRING_AGG and ARRAY_CONCAT_AGG are rejected in a total of a tagged column, because they return one row per group that still holds every customer's value. The HAVING is matched to its SELECT by identity, so a UNION of two identical totals gets it on both branches. The table → customer id map now lives once, as `ID_COLUMNS` in `customer_pseudonyms.py`. One older test listed `age, city` row by row and now reads untagged columns. Manual check ran through the real gateway and BigQuery without the LLM: "smallest cities by customers" returned groups of exactly 5 for the CEO and the Levi's manager, and `SELECT u.id, u.city` was rejected.
