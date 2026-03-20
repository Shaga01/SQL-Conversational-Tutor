from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "tutor.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """
    )

    cur.execute("SELECT COUNT(*) FROM customers")
    if cur.fetchone()[0] == 0:
        cur.executescript(
            """
            INSERT INTO customers (name, city) VALUES
                ('Alice', 'Boise'),
                ('Bob', 'Seattle'),
                ('Carol', 'Boise'),
                ('Dan', 'Portland');

            INSERT INTO products (name, category, price) VALUES
                ('Keyboard', 'Electronics', 49.99),
                ('Mouse', 'Electronics', 24.99),
                ('Notebook', 'Stationery', 5.49),
                ('Pen Pack', 'Stationery', 3.99);

            INSERT INTO orders (customer_id, order_date) VALUES
                (1, '2026-01-10'),
                (1, '2026-02-01'),
                (2, '2026-02-11'),
                (3, '2026-03-04');

            INSERT INTO order_items (order_id, product_id, quantity) VALUES
                (1, 1, 1),
                (1, 2, 2),
                (2, 3, 5),
                (3, 1, 1),
                (3, 4, 3),
                (4, 2, 1),
                (4, 3, 2);
            """
        )

    conn.commit()
    conn.close()


def get_schema_text() -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    lines: list[str] = []
    for row in cur.fetchall():
        lines.append(f"{row['name']}: {row['sql']}")
    conn.close()
    return "\n".join(lines)
