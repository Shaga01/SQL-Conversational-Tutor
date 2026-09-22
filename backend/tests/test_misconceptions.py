from __future__ import annotations

import pytest
from app.catalog import describe, resolve_db
from app.sandbox import SandboxError, run_query
from app.tutor.misconceptions import analyze_query, explain_error


@pytest.fixture(scope="module")
def shop():
    path = resolve_db("shop")
    return path, describe(path)


BUGGY = [
    ("SELECT id FROM customers WHERE email = NULL", "null-equality"),
    ("SELECT id FROM customers WHERE email != NULL", "null-equality"),
    ("SELECT c.first_name, o.id FROM customers c JOIN orders o", "cartesian-join"),
    ("SELECT c.first_name, o.id FROM customers c, orders o", "cartesian-join"),
    ("SELECT c.last_name, o.id FROM orders o JOIN customers c ON o.id = c.id", "wrong-join-key"),
    ("SELECT c.id FROM customers c JOIN orders o ON o.employee_id = c.id", "wrong-join-key"),
    ("SELECT country, city, COUNT(*) FROM customers GROUP BY country", "ungrouped-column"),
    ("SELECT first_name, COUNT(*) FROM customers", "ungrouped-column"),
    ("SELECT city FROM customers WHERE COUNT(*) > 5 GROUP BY city", "aggregate-in-where"),
    ("SELECT city, COUNT(*) FROM customers GROUP BY city HAVING city = 'Boise'", "having-no-aggregate"),
    ("SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status = 'delivered'", "left-join-nullified"),
    ("SELECT c.id, COUNT(*) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id", "count-star-left-join"),
    ("SELECT name FROM products LIMIT 5", "limit-without-order"),
    ("SELECT SUM(quantity) / COUNT(*) FROM order_items", "integer-division"),
    ('SELECT id FROM customers WHERE city = "Boise"', "double-quoted-string"),
    ("SELECT name FROM products WHERE name LIKE 'Laptop'", "like-without-wildcard"),
    ("SELECT id FROM customers WHERE city = 'boise'", "literal-case"),
    ("SELECT id FROM customers WHERE city = 'Paris'", "literal-missing"),
    ("SELECT name, FROM products", "trailing-comma"),
    # a double-quoted string parses as a column; it must not count as a join link
    ('SELECT o.id FROM orders o, customers c WHERE c.city = "Boise"', "cartesian-join"),
    ("SELECT name FROM products ORDER BY price WHERE price > 10", "clause-order"),
]


@pytest.mark.parametrize("sql,expected", BUGGY)
def test_detects_misconception(shop, sql, expected):
    path, tables = shop
    ids = [f.id for f in analyze_query(sql, tables, path)]
    assert expected in ids, ids


CLEAN = [
    "SELECT name, price FROM products ORDER BY price DESC LIMIT 5",
    "SELECT c.id, COUNT(o.id) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
    "SELECT c.id, c.first_name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL",
    "SELECT country, COUNT(*) FROM customers GROUP BY country HAVING COUNT(*) > 10",
    "SELECT c.id, c.first_name, COUNT(o.id) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
    "SELECT e.name, m.name FROM employees e JOIN employees m ON m.id = e.manager_id",
    # functional dependency through the join: o.customer_id = c.id determines every c column
    "SELECT c.first_name, COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY o.customer_id",
    "SELECT 1.0 * SUM(quantity) / COUNT(*) FROM order_items",
    # both join conditions in the last ON clause (common Spider style)
    "SELECT p.name FROM order_items oi JOIN orders o JOIN products p ON oi.order_id = o.id AND oi.product_id = p.id",
    # transitive dependency: order_items key -> product_id -> products row
    "SELECT p.name, oi.quantity FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY oi.order_id, oi.product_id",
    # a legitimate join on a non-key relationship (not the declared FK)
    "SELECT o.id FROM orders o JOIN customers c ON o.shipping_city = c.city",
    # OR inside ON still links the two tables
    "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id OR o.shipping_city = c.city",
    # a column fixed to a constant is not ambiguous
    "SELECT city, COUNT(*) FROM customers WHERE city = 'Boise'",
    "SELECT name FROM products WHERE price > (SELECT AVG(price) FROM products)",
    "SELECT name, RANK() OVER (PARTITION BY department ORDER BY salary DESC) FROM employees",
]


@pytest.mark.parametrize("sql", CLEAN)
def test_no_false_positives_on_correct_queries(shop, sql):
    path, tables = shop
    findings = [f for f in analyze_query(sql, tables, path) if f.severity != "info"]
    assert findings == [], [f.id for f in findings]


@pytest.mark.parametrize("sql,expected", [
    ("SELECT price FROM orders", "unknown-column"),
    ("SELECT nme FROM products", "unknown-column"),
    ("SELECT * FROM customer", "unknown-table"),
    ("SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id", "ambiguous-column"),
    ("SELECT city FROM customers WHERE COUNT(*) > 3", "aggregate-in-where"),
    ("SELECT city FROM customers ORDER BY COUNT(*) DESC LIMIT 1", "missing-group-by"),
])
def test_runtime_errors_are_explained(shop, sql, expected):
    path, tables = shop
    with pytest.raises(SandboxError) as info:
        run_query(path, sql)
    findings = explain_error(str(info.value), tables, sql)
    assert findings[0].id == expected


def test_unknown_column_points_to_owning_table(shop):
    _, tables = shop
    [f] = explain_error("no such column: price", tables)
    assert "products" in f.message


def test_typo_gets_did_you_mean(shop):
    _, tables = shop
    [f] = explain_error("no such table: custmers", tables)
    assert "customers" in f.message
