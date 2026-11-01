# 09: Hash customer ids

**What to build:** the model never sees a real customer id. `users.id`, `orders.user_id` and `order_items.user_id` reach it only as a pseudonym such as `cust_72ac84091163`. The pseudonym is the same every time for the same customer, so joins, `COUNT(DISTINCT user_id)` and follow-up questions ("what did that customer buy?") still work. This is the second PII slice of step 3.

Source: [plan, step 3](../docs/plan.md). Client: "Individual customers can be referenced by `user_id` or a stable pseudonym (e.g. a salted hash) so follow-up questions work."

**Blocked by:** nothing (ticket 08 is done)

**Status:** built 2026-11-01

**Why not the raw id:** `thelook_ecommerce` is a public dataset. Anyone with a raw `user_id` can look up that customer's name and email in BigQuery. A salted hash can't be turned back into the id without the salt.

**Decisions (2026-11-01, proposed):**
- **Hash in the tables, not in the results.** A new rewrite step swaps `users`, `orders` and `order_items` for copies whose customer id column is already hashed, the same "swap the table" idea as brand scoping. The model can't reach a raw id by any route (`MAX(id)`, `id + 0`, a renamed column), because there isn't one left in what it reads. Joins still match, because both sides are hashed the same way. A follow-up that filters on `user_id = 'cust_…'` works with no lookup table to keep.
- **The hash:** `CONCAT('cust_', SUBSTR(TO_HEX(SHA256(CONCAT(@pseudonym_salt, CAST(id AS STRING)))), 1, 12))`. 12 hex characters keep tokens low. For about 100,000 customers, the chance that any two share a pseudonym is about 1 in 50,000.
- **The salt is a secret, like an API key.** It lives in `.env` as `PSEUDONYM_SALT`. When it isn't set (or is empty), a random salt is made at startup: pseudonyms then stay the same for the whole run but change between runs. It reaches BigQuery as a query parameter (`@pseudonym_salt`), never inside the SQL text, so it isn't in the log file.
- **The model can't read query parameters.** Found while probing: without a new rule, `SELECT @pseudonym_salt` passes every current check and would hand the model the salt. The guard rejects parameters and system variables (`@name`, `?`, `@@name`) in the model's SQL.
- **It runs for everyone, before brand scoping** (changed at build time from "after"). The CEO's SQL is now rewritten too. For a brand manager, brand scope then swaps the real tables inside the hashed copies for brand-filtered ones, so the brand filters still compare real ids with real ids, and the hashed copies are made from brand-filtered rows. This keeps every personal-data piece in step 2 (`query_safety/step2_personal_data/`).

```
model SQL → guard → personal-data check → customer pseudonyms (everyone) → brand scope (managers) → byte check → BigQuery
```

- [x] `query_safety/step2_personal_data/customer_pseudonyms.py`: `CustomerPseudonyms.apply(sql)` returns the query with `users`, `orders` and `order_items` swapped for `_pseudonymized_…` versions, defined in a WITH clause put first. Only the tables the query reads get a copy. `products` is left alone. WITH names are not swapped.
- [x] The gateway calls it before `BrandScope.apply`; the byte check and the database get the SQL after both rewrites.
- [x] Guard: reject query parameters and system variables in the model's SQL, with a message the model can act on.
- [x] `Settings.pseudonym_salt` (`PSEUDONYM_SALT`), random when unset or empty; `.env.example` explains it.
- [x] `BigQueryDatabase` takes query parameters and sends them with every query and dry run. `app.py` passes `{"pseudonym_salt": settings.pseudonym_salt}`.
- [x] `schema.yaml`: the three id columns become `STRING` with a note, e.g. "Customer pseudonym such as cust_72ac84091163; the real id is never shown".
- [x] Offline tests (`tests/test_step2_customer_pseudonyms.py`):
  - the CEO's query reads `users`, `orders` and `order_items` only through the hashed versions; `products` is unchanged;
  - a brand manager's query keeps both layers, and no real `users`, `orders` or `order_items` table is read outside a `_pseudonymized_` definition;
  - the model's own WITH queries still work;
  - `SELECT @pseudonym_salt`, `SELECT ?` and `SELECT @@project_id` are rejected before they reach the database;
  - an empty `PSEUDONYM_SALT` gives a random salt, not an empty one.
- [x] Manual check on real BigQuery: as the CEO, "top 3 customers by number of orders" shows `cust_…` ids; the follow-up "what did the first one buy?" filters on that pseudonym and answers. The same for a brand manager.
- [x] Docs: update the step 3 bullet in `docs/plan.md`, and say which `docs/*.html` explainers are now stale (brand scoping and architecture say the CEO's SQL runs unchanged).

**Built:** as planned, with three changes. Pseudonyms run before brand scope (see above). Only the copies a query reads are added, so the SQL in the log stays short. The salt is a `SecretStr`, so it doesn't show if settings are printed. The manual check ran through the real gateway and BigQuery without the LLM: as the CEO the top 3 customers came back as `cust_…` and a follow-up filtering on the first one listed what that customer bought; as the Levi's manager the same, with only Levi's items. Four older tests compared the exact SQL sent to the database; they now check that the query reached it, since every query reading a customer table is rewritten.

**Later, not this ticket:** `orders.order_id` and `order_items.id` are also linkable to a customer through the public dataset. Hash them too, or argue in the design doc why not. Groups smaller than `MIN_GROUP_SIZE` and demographics outside aggregates are their own slices.
