import pytest
import sqlglot
from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import ORDER_ITEMS, PRODUCTS, assemble_app
from langchain_core.messages import AIMessage
from sqlglot import exp

from retail_agent.db.base import QueryResult
from retail_agent.query_safety.step1_guard import QueryRejected, is_with_name
from retail_agent.query_safety.step3_brand_scope import SCOPED_PREFIX, BrandScope
from retail_agent.users import User

DATASET = "bigquery-public-data.thelook_ecommerce"
ORDERS = f"`{DATASET}.orders`"
USERS = f"`{DATASET}.users`"
LEVIS = BrandScope("bigquery", DATASET, {"Levi's"})


def real_tables_read_without_brand_filter(sql: str) -> list[str]:
    """Real tables the query reads directly, not through one of the brand-filtered tables."""
    statement = sqlglot.parse_one(sql, read="bigquery")
    leaks = []
    for table in statement.find_all(exp.Table):
        if is_with_name(table, statement):
            continue
        cte = table.find_ancestor(exp.CTE)
        if cte is None or not cte.alias_or_name.startswith(SCOPED_PREFIX):
            leaks.append(table.sql("bigquery"))
    return leaks


def test_every_table_is_swapped_for_its_filtered_version():
    sql = f"SELECT p.category, SUM(oi.sale_price) AS revenue FROM {ORDER_ITEMS} oi JOIN {PRODUCTS} p ON p.id = oi.product_id GROUP BY p.category"

    assert LEVIS.apply(sql) == (
        f"WITH _brand_scoped_products AS (SELECT * FROM `{DATASET}.products` AS products WHERE brand IN ('Levi\\'s')), "
        f"_brand_scoped_order_items AS (SELECT * FROM `{DATASET}.order_items` AS order_items "
        "WHERE product_id IN (SELECT id FROM _brand_scoped_products)), "
        "_brand_scoped_orders AS (SELECT * REPLACE ((SELECT COUNT(*) FROM _brand_scoped_order_items AS items "
        "WHERE items.order_id = orders.order_id) AS num_of_item) "
        f"FROM `{DATASET}.orders` AS orders "
        "WHERE order_id IN (SELECT order_id FROM _brand_scoped_order_items)), "
        f"_brand_scoped_users AS (SELECT * FROM `{DATASET}.users` AS users "
        "WHERE id IN (SELECT user_id FROM _brand_scoped_order_items)) "
        "SELECT p.category, SUM(oi.sale_price) AS revenue FROM _brand_scoped_order_items AS oi "
        "JOIN _brand_scoped_products AS p ON p.id = oi.product_id GROUP BY p.category"
    )


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM {PRODUCTS}",
        f"SELECT brand, COUNT(*) FROM {PRODUCTS} WHERE brand != 'Nike' GROUP BY brand",
        f"SELECT COUNT(*) FROM {USERS}",
        f"SELECT status, COUNT(*) FROM {ORDERS} GROUP BY status",
        f"SELECT * FROM {ORDER_ITEMS} WHERE product_id IN (SELECT id FROM {PRODUCTS} WHERE brand = 'Nike')",
        f"WITH everything AS (SELECT * FROM {PRODUCTS}) SELECT * FROM everything",
        f"WITH products AS (SELECT * FROM {PRODUCTS}) SELECT * FROM products",
        f"SELECT id FROM {PRODUCTS} UNION ALL SELECT id FROM {USERS}",
        f"SELECT u.country, (SELECT COUNT(*) FROM {ORDERS} o WHERE o.user_id = u.id) FROM {USERS} u",
    ],
)
def test_no_query_reads_a_real_table_outside_the_brand_filter(sql):
    assert real_tables_read_without_brand_filter(LEVIS.apply(sql)) == []


def test_a_query_without_an_alias_still_names_columns_by_table():
    scoped = LEVIS.apply(f"SELECT products.brand FROM {PRODUCTS}")

    assert scoped.endswith("SELECT products.brand FROM _brand_scoped_products AS products")


def test_the_scoped_tables_come_before_the_models_own_with_queries():
    scoped = LEVIS.apply(f"WITH t AS (SELECT * FROM {USERS}) SELECT COUNT(*) FROM t")

    names = [cte.alias for cte in sqlglot.parse_one(scoped, read="bigquery").find_all(exp.CTE)]
    assert names == [
        "_brand_scoped_products",
        "_brand_scoped_order_items",
        "_brand_scoped_orders",
        "_brand_scoped_users",
        "t",
    ]


def test_a_user_with_every_brand_gets_the_query_unchanged():
    sql = f"SELECT * FROM {PRODUCTS}"

    assert BrandScope("bigquery", DATASET, None).apply(sql) == sql


def test_a_table_without_a_brand_rule_is_rejected():
    with pytest.raises(QueryRejected, match="can't be limited to your brands"):
        LEVIS.apply(f"SELECT * FROM `{DATASET}.inventory_items`")


def test_the_gateway_runs_the_scoped_query():
    sql = f"SELECT COUNT(*) FROM {PRODUCTS}"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("3 products.")])
    database = FakeDatabase([QueryResult(columns=["n"], rows=[(3,)])])

    assemble_app(model, database, user=User(name="levis_manager", brands=frozenset({"Levi's"}))).ask("How many products?")

    assert database.executed == [LEVIS.apply(sql)]
