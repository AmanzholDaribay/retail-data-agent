import sqlglot
from harness import ORDER_ITEMS

from retail_agent.query_safety.step3_brand_scope.brand_scope import BrandScope
from retail_agent.query_safety.step3_brand_scope.order_item_count import orders_columns_with_scoped_item_count

DATASET = "bigquery-public-data.thelook_ecommerce"


def filtered_orders_table(sql: str) -> str:
    """The SQL of the brand-filtered orders table inside a scoped query."""
    statement = sqlglot.parse_one(sql, read="bigquery")
    cte = next(cte for cte in statement.find_all(sqlglot.exp.CTE) if cte.alias == "_brand_scoped_orders")
    return cte.this.sql("bigquery")


def test_num_of_item_counts_only_the_users_own_items():
    assert orders_columns_with_scoped_item_count("_brand_scoped_order_items") == (
        "* REPLACE ((SELECT COUNT(*) FROM _brand_scoped_order_items AS items "
        "WHERE items.order_id = orders.order_id) AS num_of_item)"
    )


def test_a_brand_manager_gets_orders_with_the_recounted_num_of_item():
    scoped = BrandScope("bigquery", DATASET, {"Levi's"}).apply(f"SELECT order_id FROM {ORDER_ITEMS}")

    assert filtered_orders_table(scoped).startswith(
        "SELECT * REPLACE ((SELECT COUNT(*) FROM _brand_scoped_order_items AS items "
        "WHERE items.order_id = orders.order_id) AS num_of_item) "
        f"FROM `{DATASET}.orders` AS orders"
    )
