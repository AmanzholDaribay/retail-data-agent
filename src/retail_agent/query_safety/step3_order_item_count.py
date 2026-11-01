"""Keeps `orders.num_of_item` from counting other brands' items, for a user who sees only some brands.

`orders_columns_with_scoped_item_count(...)` returns the SELECT list for the brand-filtered orders table: every
column as it is, except `num_of_item`, which becomes the number of the user's own brands' items in that order.
"""


def orders_columns_with_scoped_item_count(scoped_order_items_table: str) -> str:
    """E.g. an order with 1 Levi's item and 2 Nike items shows num_of_item = 1 to a Levi's manager, not 3.

    `scoped_order_items_table` is the brand-filtered order_items table; the orders table must be named `orders`.
    """
    return (
        "* REPLACE ("
        f"(SELECT COUNT(*) FROM {scoped_order_items_table} AS items WHERE items.order_id = orders.order_id)"
        " AS num_of_item)"
    )
