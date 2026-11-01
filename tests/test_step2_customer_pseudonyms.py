from types import SimpleNamespace
from typing import Any

import pytest
import sqlglot
from fakes import FakeDatabase, ScriptedChatModel, run_sql_call
from harness import ORDER_ITEMS, ORDERS, PRODUCTS, USERS, assemble_app
from langchain_core.messages import AIMessage
from sqlglot import exp

import retail_agent.db.bigquery as bigquery_adapter
from retail_agent.config import Settings
from retail_agent.db.base import QueryResult
from retail_agent.db.bigquery import BigQueryDatabase
from retail_agent.query_safety.step1_guard import is_with_name
from retail_agent.query_safety.step2_personal_data.customer_pseudonyms import (
    PSEUDONYM_PREFIX,
    SALT_PARAMETER,
    CustomerPseudonyms,
)
from retail_agent.query_safety.step3_brand_scope.brand_scope import SCOPED_PREFIX, BrandScope
from retail_agent.users import User

DATASET = "bigquery-public-data.thelook_ecommerce"
PSEUDONYMS = CustomerPseudonyms("bigquery", DATASET)
LEVIS = BrandScope("bigquery", DATASET, {"Levi's"})
CUSTOMER_TABLES = {"users", "orders", "order_items"}


def customer_ids_read_unhashed(sql: str) -> list[str]:
    """Customer tables the query reads outside our own WITH definitions, so with their real ids.

    A real customer table may be read only inside one of our definitions, and so may a brand-filtered one,
    since it still holds real ids. Everything else must read the `_pseudonymized_` copies.
    """
    statement = sqlglot.parse_one(sql, read="bigquery")
    leaks = []
    for table in statement.find_all(exp.Table):
        real = not is_with_name(table, statement) and table.name in CUSTOMER_TABLES
        brand_filtered = table.name.removeprefix(SCOPED_PREFIX) in CUSTOMER_TABLES and table.name.startswith(SCOPED_PREFIX)
        if not (real or brand_filtered):
            continue
        cte = table.find_ancestor(exp.CTE)
        if cte is None or not cte.alias_or_name.startswith((PSEUDONYM_PREFIX, SCOPED_PREFIX)):
            leaks.append(table.sql("bigquery"))
    return leaks


def test_the_customer_id_is_replaced_by_a_salted_hash():
    sql = f"SELECT id, country FROM {USERS}"

    assert PSEUDONYMS.apply(sql) == (
        "WITH _pseudonymized_users AS (SELECT * REPLACE "
        "(CONCAT('cust_', SUBSTR(TO_HEX(SHA256(CONCAT(@pseudonym_salt, CAST(id AS STRING)))), 1, 12)) AS id) "
        f"FROM {USERS} AS users) "
        "SELECT id, country FROM _pseudonymized_users AS users"
    )


def test_only_the_customer_tables_the_query_reads_get_a_hashed_copy():
    sql = f"SELECT oi.user_id, p.brand FROM {ORDER_ITEMS} oi JOIN {PRODUCTS} p ON p.id = oi.product_id"

    rewritten = PSEUDONYMS.apply(sql)

    names = [cte.alias for cte in sqlglot.parse_one(rewritten, read="bigquery").find_all(exp.CTE)]
    assert names == ["_pseudonymized_order_items"]
    assert f"JOIN {PRODUCTS} AS p" in rewritten


def test_a_query_without_customer_tables_is_unchanged():
    sql = f"SELECT brand, COUNT(*) FROM {PRODUCTS} GROUP BY brand"

    assert PSEUDONYMS.apply(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT id FROM {USERS}",
        f"SELECT MAX(user_id) FROM {ORDERS}",
        f"SELECT u.id, COUNT(*) FROM {USERS} u JOIN {ORDERS} o ON o.user_id = u.id GROUP BY u.id",
        f"SELECT user_id FROM {ORDER_ITEMS} WHERE product_id IN (SELECT id FROM {PRODUCTS})",
        f"WITH t AS (SELECT user_id FROM {ORDERS}) SELECT COUNT(DISTINCT user_id) FROM t",
        f"WITH users AS (SELECT id FROM {USERS}) SELECT id FROM users",
        f"SELECT id FROM {USERS} UNION ALL SELECT user_id FROM {ORDER_ITEMS}",
        f"SELECT u.country, (SELECT COUNT(*) FROM {ORDERS} o WHERE o.user_id = u.id) FROM {USERS} u",
    ],
)
@pytest.mark.parametrize("scope", [BrandScope("bigquery", DATASET, None), LEVIS], ids=["ceo", "levis_manager"])
def test_no_query_reads_a_real_customer_id(sql, scope):
    assert customer_ids_read_unhashed(scope.apply(PSEUDONYMS.apply(sql))) == []


def test_for_a_brand_manager_the_hashed_copies_read_the_brand_filtered_tables():
    rewritten = LEVIS.apply(PSEUDONYMS.apply(f"SELECT id FROM {USERS}"))

    statement = sqlglot.parse_one(rewritten, read="bigquery")
    copy = next(cte for cte in statement.find_all(exp.CTE) if cte.alias == "_pseudonymized_users")
    assert [table.name for table in copy.find_all(exp.Table)] == ["_brand_scoped_users"]


def test_the_models_own_with_queries_come_after_the_hashed_copies():
    rewritten = PSEUDONYMS.apply(f"WITH t AS (SELECT user_id FROM {ORDERS}) SELECT COUNT(DISTINCT user_id) FROM t")

    names = [cte.alias for cte in sqlglot.parse_one(rewritten, read="bigquery").find_all(exp.CTE)]
    assert names == ["_pseudonymized_orders", "t"]


def test_the_gateway_hashes_customer_ids_for_the_ceo_too():
    sql = f"SELECT id FROM {USERS} LIMIT 1"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("cust_72ac84091163.")])
    database = FakeDatabase([QueryResult(columns=["id"], rows=[("cust_72ac84091163",)])])

    assemble_app(model, database).ask("One customer?")

    assert database.executed == [PSEUDONYMS.apply(sql)]


def test_the_gateway_hashes_before_the_brand_scope():
    sql = f"SELECT id FROM {USERS} LIMIT 1"
    model = ScriptedChatModel(script=[run_sql_call(sql), AIMessage("cust_72ac84091163.")])
    database = FakeDatabase([QueryResult(columns=["id"], rows=[("cust_72ac84091163",)])])
    manager = User(name="levis_manager", brands=frozenset({"Levi's"}))

    assemble_app(model, database, user=manager).ask("One customer?")

    assert database.executed == [LEVIS.apply(PSEUDONYMS.apply(sql))]


@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_or_empty_salt_becomes_a_random_one(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("PSEUDONYM_SALT", raising=False)
    else:
        monkeypatch.setenv("PSEUDONYM_SALT", value)

    first = Settings(gcp_project="test-project").pseudonym_salt.get_secret_value()
    second = Settings(gcp_project="test-project").pseudonym_salt.get_secret_value()

    assert len(first) >= 32
    assert first != second


def test_a_salt_from_the_environment_is_kept(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_SALT", "kept-secret")

    assert Settings(gcp_project="test-project").pseudonym_salt.get_secret_value() == "kept-secret"


class NoRows(list):
    schema: list = []
    total_rows = 0


class RecordingClient:
    """Stands in for bigquery.Client: records each query and its job config, and returns no rows."""

    def __init__(self, project: str) -> None:
        self.job_configs: dict[str, Any] = {}

    def query(self, sql: str, job_config: Any) -> Any:
        assert "kept-secret" not in sql
        self.job_configs["dry run" if job_config.dry_run else "query"] = job_config
        return SimpleNamespace(total_bytes_processed=0, result=lambda max_results: NoRows())


def test_the_salt_reaches_bigquery_as_a_query_parameter_with_every_query_and_dry_run(monkeypatch):
    monkeypatch.setattr(bigquery_adapter.bigquery, "Client", RecordingClient)
    database = BigQueryDatabase("test-project", 1_000, query_parameters={SALT_PARAMETER: "kept-secret"})
    sql = PSEUDONYMS.apply(f"SELECT id FROM {USERS}")

    database.estimate_bytes(sql)
    database.execute(sql, max_rows=10)

    for job_config in database._client.job_configs.values():
        assert [(p.name, p.type_, p.value) for p in job_config.query_parameters] == [
            ("pseudonym_salt", "STRING", "kept-secret")
        ]
    assert set(database._client.job_configs) == {"dry run", "query"}
