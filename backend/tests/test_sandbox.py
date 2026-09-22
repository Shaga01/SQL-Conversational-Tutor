"""Adversarial tests for the SQL sandbox. Each attack must be stopped by *some* layer."""

from __future__ import annotations

import time

import pytest
from app.catalog import create_session_db, resolve_db
from app.sandbox import QueryFailed, QueryRejected, QueryTimeout, SandboxError, run_query
from app.sql_guard import check_read_only


@pytest.fixture(scope="module")
def shop():
    return resolve_db("shop")


# --- things that must work -------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "SELECT name FROM products WHERE name = 'update pending'",  # keyword inside a string literal
    "SELECT first_name AS created, last_name AS deleted FROM customers",
    "WITH big AS (SELECT * FROM orders) SELECT COUNT(*) FROM big",
    "SELECT id FROM customers UNION SELECT customer_id FROM orders",
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 10) SELECT SUM(x) FROM n",
    "select count(*) from customers;",
])
def test_legitimate_queries_run(shop, sql):
    result = run_query(shop, sql)
    assert result.columns


def test_columns_reported_even_for_empty_result(shop):
    result = run_query(shop, "SELECT id, city FROM customers WHERE 1 = 0")
    assert result.columns == ["id", "city"] and result.rows == []


def test_row_cap_sets_truncated_flag(shop):
    result = run_query(shop, "SELECT * FROM order_items", max_rows=10)
    assert result.row_count == 10 and result.truncated


# --- attacks ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DELETE FROM customers",
    "DROP TABLE customers",
    "UPDATE products SET price = 0",
    "INSERT INTO customers (first_name) VALUES ('x')",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "PRAGMA writable_schema = 1",
    "SELECT 1; DROP TABLE customers",
    "CREATE TABLE t AS SELECT * FROM customers",
    "VACUUM INTO '/tmp/copy.db'",
])
def test_writes_and_escapes_are_rejected(shop, sql):
    with pytest.raises(SandboxError):
        run_query(shop, sql)


def test_infinite_recursive_cte_is_stopped(shop):
    started = time.monotonic()
    with pytest.raises(QueryTimeout):
        run_query(shop, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
                  timeout_s=0.5)
    assert time.monotonic() - started < 3


def test_cartesian_explosion_is_stopped(shop):
    with pytest.raises(QueryTimeout):
        run_query(shop, "SELECT COUNT(*) FROM order_items a, order_items b, order_items c", timeout_s=0.5)


def test_memory_bomb_is_stopped(shop):
    with pytest.raises(SandboxError):
        run_query(shop, "SELECT zeroblob(500000000)")


def test_authorizer_blocks_even_when_static_guard_is_skipped(shop):
    """Layer 3 must hold on its own: bypass layer 1 and try to write."""
    with pytest.raises((QueryRejected, QueryFailed)):
        run_query(shop, "DELETE FROM customers", validate=False)
    assert run_query(shop, "SELECT COUNT(*) FROM customers").rows[0][0] == 160


def test_guard_reports_syntax_errors():
    result = check_read_only("SELEC name FROM customers")
    assert not result.ok
    assert result.reason == 'Syntax error: near "SELEC": syntax error'


# --- learner-created databases --------------------------------------------------------

def test_custom_schema_can_be_created_and_queried(tmp_path, monkeypatch):
    db_id = create_session_db("pytest_ok", "CREATE TABLE pets (id INTEGER PRIMARY KEY, name TEXT);",
                              "INSERT INTO pets (name) VALUES ('Rex'), ('Tom');")
    assert run_query(resolve_db(db_id), "SELECT COUNT(*) FROM pets").rows == [[2]]


@pytest.mark.parametrize("script", [
    "ATTACH DATABASE '/tmp/pwn.db' AS pwn; CREATE TABLE pwn.t (x);",
    "PRAGMA journal_mode = OFF; CREATE TABLE t (x);",
    "CREATE TABLE t (x); WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) INSERT INTO t SELECT x FROM c;",
])
def test_custom_schema_attacks_are_rejected(script):
    with pytest.raises(QueryFailed):
        create_session_db("pytest_bad", script)
