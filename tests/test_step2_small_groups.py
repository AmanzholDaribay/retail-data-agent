import pytest
from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import ORDER_ITEMS, ORDERS, PRODUCTS, ROOT, USERS, assemble_app, tool_output_seen_by_model
from langchain_core.messages import AIMessage

from retail_agent.db.base import QueryResult
from retail_agent.query_safety.step1_guard import QueryRejected
from retail_agent.query_safety.step2_personal_data.small_groups import SmallGroups
from retail_agent.schema import load_schema
from retail_agent.users import User

SCHEMA = load_schema(ROOT / "schema.yaml")
SMALL_GROUPS = SmallGroups("bigquery", SCHEMA, min_group_size=5)


def test_schema_yaml_tags_the_demographic_columns():
    tagged = {
        (table.name, column.name) for table in SCHEMA.tables for column in table.columns if column.aggregate_only
    }

    assert tagged == {
        ("users", "age"),
        ("users", "gender"),
        ("users", "city"),
        ("users", "state"),
        ("users", "country"),
        ("users", "traffic_source"),
        ("orders", "gender"),
    }


def test_the_system_prompt_says_which_columns_work_only_in_totals():
    assert "- city STRING (only in totals)" in SCHEMA.render()


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT id, city FROM {USERS}",
        f"SELECT DISTINCT country FROM {USERS}",
        f"SELECT o.order_id FROM {ORDERS} o WHERE o.gender = 'F'",
        f"WITH people AS (SELECT id, city FROM {USERS}) SELECT city, COUNT(*) FROM people GROUP BY city",
        f"SELECT order_id FROM {ORDERS} WHERE user_id IN (SELECT id FROM {USERS} WHERE country = 'Iceland')",
        f"SELECT city, COUNT(*) OVER (PARTITION BY city) FROM {USERS}",
        f"SELECT u.id FROM {USERS} AS u WHERE EXISTS (SELECT 1 FROM {PRODUCTS} AS p WHERE u.country = 'Iceland')",
        f"SELECT u.id, (SELECT u.city) AS city FROM {USERS} AS u",
    ],
)
def test_a_tagged_column_outside_a_total_is_rejected(sql):
    with pytest.raises(QueryRejected, match="only inside a total"):
        SMALL_GROUPS.apply(sql)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT ARRAY_AGG(STRUCT(id, city)) FROM {USERS}",
        f"SELECT country, STRING_AGG(id) FROM {USERS} GROUP BY country",
        f"SELECT country, ARRAY_CONCAT_AGG([age]) FROM {USERS} GROUP BY country",
    ],
)
def test_an_aggregate_that_keeps_every_row_is_rejected(sql):
    with pytest.raises(QueryRejected, match="ARRAY_AGG or STRING_AGG"):
        SMALL_GROUPS.apply(sql)


def test_two_identical_totals_each_get_the_having():
    total = f"SELECT city, COUNT(*) AS customers FROM {USERS} GROUP BY city"

    rewritten = SMALL_GROUPS.apply(f"{total} UNION ALL {total}")

    assert rewritten.count("HAVING COUNT(DISTINCT users.id) >= 5") == 2
    assert "AND COUNT(DISTINCT" not in rewritten


def test_an_alias_that_needs_quoting_is_quoted_in_the_having():
    sql = f"SELECT `order`.country, COUNT(*) FROM {USERS} AS `order` GROUP BY `order`.country"

    assert SMALL_GROUPS.apply(sql).endswith("HAVING COUNT(DISTINCT `order`.id) >= 5")


def test_a_total_by_a_tagged_column_hides_groups_under_the_minimum():
    sql = f"SELECT city, AVG(age) AS avg_age FROM {USERS} GROUP BY city"

    assert SMALL_GROUPS.apply(sql) == (
        f"SELECT city, AVG(age) AS avg_age FROM {USERS} GROUP BY city HAVING COUNT(DISTINCT users.id) >= 5"
    )


def test_orders_gender_counts_the_orders_customer_id():
    sql = f"SELECT o.gender, COUNT(*) AS orders FROM {ORDERS} AS o GROUP BY o.gender"

    assert SMALL_GROUPS.apply(sql).endswith("HAVING COUNT(DISTINCT o.user_id) >= 5")


def test_a_joined_table_is_counted_by_its_alias():
    sql = (
        f"SELECT u.country, SUM(oi.sale_price) AS revenue FROM {ORDER_ITEMS} AS oi "
        f"JOIN {USERS} AS u ON u.id = oi.user_id GROUP BY u.country ORDER BY revenue DESC LIMIT 3"
    )

    assert "GROUP BY u.country HAVING COUNT(DISTINCT u.id) >= 5 ORDER BY revenue DESC LIMIT 3" in SMALL_GROUPS.apply(sql)


def test_the_models_own_having_is_kept():
    sql = f"SELECT country, COUNT(*) AS customers FROM {USERS} GROUP BY country HAVING COUNT(*) > 100"

    assert SMALL_GROUPS.apply(sql).endswith("HAVING COUNT(*) > 100 AND COUNT(DISTINCT users.id) >= 5")


def test_a_single_total_without_group_by_is_checked_too():
    sql = f"SELECT AVG(age) AS avg_age FROM {USERS} WHERE city = 'Tromsø'"

    assert SMALL_GROUPS.apply(sql).endswith("WHERE city = 'Tromsø' HAVING COUNT(DISTINCT users.id) >= 5")


def test_a_total_inside_a_with_step_gets_the_having_on_that_step():
    sql = (
        f"WITH by_city AS (SELECT city, COUNT(*) AS customers FROM {USERS} GROUP BY city) "
        "SELECT city, customers FROM by_city ORDER BY customers DESC"
    )

    assert SMALL_GROUPS.apply(sql) == (
        f"WITH by_city AS (SELECT city, COUNT(*) AS customers FROM {USERS} GROUP BY city "
        "HAVING COUNT(DISTINCT users.id) >= 5) "
        "SELECT city, customers FROM by_city ORDER BY customers DESC"
    )


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT brand, COUNT(*) FROM {PRODUCTS} GROUP BY brand",
        f"SELECT user_id, SUM(sale_price) AS spend FROM {ORDER_ITEMS} GROUP BY user_id ORDER BY spend DESC LIMIT 3",
        f"SELECT id, created_at FROM {USERS} LIMIT 5",
    ],
)
def test_a_query_without_tagged_columns_is_unchanged(sql):
    assert SMALL_GROUPS.apply(sql) == sql


def test_the_minimum_comes_from_settings():
    sql = f"SELECT country, COUNT(*) FROM {USERS} GROUP BY country"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Done.")])
    database = FakeDatabase([QueryResult(columns=["country", "count"], rows=[("Brazil", 5210)])])

    assemble_app(model, database, min_group_size=20).ask("Customers per country?")

    assert "HAVING COUNT(DISTINCT users.id) >= 20" in database.executed[0]


def test_the_gateway_never_runs_a_listing_of_a_tagged_column():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT id, city FROM {USERS}"), AIMessage("Not available.")])
    database = FakeDatabase([])

    assemble_app(model, database).ask("Where do my customers live?")

    assert database.executed == []
    assert "only inside a total" in tool_output_seen_by_model(model)


@pytest.mark.parametrize(
    "rows, note",
    [
        ([], "hidden for privacy, so there may be data that can't be shown"),
        ([("Berlin", 252)], "hidden for privacy, so these rows may not add up to the full total"),
    ],
)
def test_the_model_is_told_when_small_groups_were_hidden(rows, note):
    sql = f"SELECT city, COUNT(*) AS customers FROM {USERS} WHERE country = 'Germany' GROUP BY city"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Done.")])
    database = FakeDatabase([QueryResult(columns=["city", "customers"], rows=rows)])

    assemble_app(model, database).ask("Customers per city in Germany?")

    assert f"Note: groups with fewer than 5 customers are {note}" in tool_output_seen_by_model(model)


def test_a_query_without_tagged_columns_gets_no_privacy_note():
    model = ScriptedChatModel(script=[run_sql_call(f"SELECT brand, COUNT(*) FROM {PRODUCTS} GROUP BY brand"), AIMessage("Done.")])
    database = FakeDatabase([QueryResult(columns=["brand", "count"], rows=[("Levi's", 3)])])

    assemble_app(model, database).ask("Products per brand?")

    assert "hidden for privacy" not in tool_output_seen_by_model(model)


def test_for_a_brand_manager_the_having_and_the_brand_filter_both_apply():
    sql = f"SELECT country, COUNT(*) AS customers FROM {USERS} GROUP BY country"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("Done.")])
    database = FakeDatabase([QueryResult(columns=["country", "customers"], rows=[("Brazil", 40)])])
    manager = User(name="levis_manager", brands=frozenset({"Levi's"}))

    assemble_app(model, database, user=manager).ask("Customers per country?")

    executed = database.executed[0]
    assert "HAVING COUNT(DISTINCT users.id) >= 5" in executed
    assert "_brand_scoped_products" in executed
