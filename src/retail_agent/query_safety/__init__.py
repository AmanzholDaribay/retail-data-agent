"""Every SQL query the model writes passes through here before it reaches the database.

Start with `gateway.py`: it runs the steps in order, and each step either passes the query on or stops it.

  Step 1  step1_guard.py             one SELECT, allowed tables only
  Step 2  step2_personal_data.py     only columns from schema.yaml, so no personal data
  Step 3  step3_brand_scope.py       rewrite the SQL so the user sees only their brands
          step3_order_item_count.py    (part of step 3: recount orders.num_of_item)
  Step 4  gateway.py                 stop a query that would scan too many bytes

Steps 1-3 use sqlglot, which turns the SQL text into a tree of labeled pieces ("Table", "Column", ...).
The code then does one of three things with the tree:
  parse   sqlglot.parse_one(sql)           text -> tree
  find    statement.find_all(exp.Table)    every piece with that label
  change  table.replace(...), then .sql()  swap a piece, then tree -> text again (step 3 only)
"""
