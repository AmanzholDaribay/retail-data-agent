"""Part of step 3: keeps `orders.num_of_item` from counting other brands' items, for a user who sees only some brands.

`num_of_item` is stored on the order and counts every item in it. Showing it as stored would tell a Levi's manager
that an order also holds Nike items:

    stored:     order 42 -> num_of_item = 3   (1 Levi's + 2 Nike)
    rewritten:  order 42 -> num_of_item = 1   (only the Levi's manager's own items)

`orders_columns_with_scoped_item_count(...)` returns the SELECT list for the brand-filtered orders table:
`* REPLACE (...)`, meaning "every column as it is, but replace num_of_item with a fresh count of my items".
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
