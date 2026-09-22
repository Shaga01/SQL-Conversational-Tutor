"""Deterministic generator for the built-in "shop" teaching database.

The data is synthetic (no licensing issues) and is deliberately shaped so that
classic SQL misconceptions produce *visibly different* results:

* customers with no orders / products never ordered  -> INNER vs LEFT JOIN
* NULL emails, phones, managers, review comments     -> ``= NULL`` vs ``IS NULL``,
                                                        COUNT(*) vs COUNT(col)
* cancelled orders                                   -> forgetting a WHERE filter
* repeated first names across cities                 -> DISTINCT, grouping by key
* employee -> manager and category -> parent chains  -> self joins, recursive CTEs
* unit_price on order_items differs from list price  -> choosing the right column
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 7

SCHEMA = """
CREATE TABLE categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    parent_id   INTEGER REFERENCES categories(id)
);

CREATE TABLE products (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    category_id   INTEGER NOT NULL REFERENCES categories(id),
    price         REAL NOT NULL,
    stock         INTEGER NOT NULL,
    discontinued  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE customers (
    id           INTEGER PRIMARY KEY,
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    email        TEXT,
    phone        TEXT,
    city         TEXT NOT NULL,
    country      TEXT NOT NULL,
    signup_date  TEXT NOT NULL
);

CREATE TABLE employees (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    title       TEXT NOT NULL,
    department  TEXT NOT NULL,
    manager_id  INTEGER REFERENCES employees(id),
    hire_date   TEXT NOT NULL,
    salary      INTEGER NOT NULL
);

CREATE TABLE orders (
    id              INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(id),
    employee_id     INTEGER REFERENCES employees(id),
    order_date      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled')),
    shipping_city   TEXT NOT NULL
);

CREATE TABLE order_items (
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL,
    unit_price  REAL NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE reviews (
    id           INTEGER PRIMARY KEY,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_date  TEXT NOT NULL,
    comment      TEXT
);
"""

DESCRIPTIONS: dict[str, dict[str, str]] = {
    "categories": {
        "_table": "Product categories; parent_id links a sub-category to its parent (NULL for top level).",
    },
    "products": {
        "_table": "Items for sale.",
        "price": "Current list price in USD.",
        "stock": "Units currently in the warehouse.",
        "discontinued": "1 if the product is no longer sold, else 0.",
    },
    "customers": {
        "_table": "People who signed up. Some never placed an order.",
        "email": "May be NULL.",
        "phone": "May be NULL.",
        "signup_date": "ISO date YYYY-MM-DD.",
    },
    "employees": {
        "_table": "Staff. manager_id points to another employee (NULL for the CEO).",
        "salary": "Yearly salary in USD.",
    },
    "orders": {
        "_table": "One row per order.",
        "employee_id": "Sales rep who handled the order; NULL for web orders.",
        "status": "One of pending, shipped, delivered, cancelled.",
        "order_date": "ISO date YYYY-MM-DD.",
    },
    "order_items": {
        "_table": "Line items. Revenue of a line = quantity * unit_price.",
        "unit_price": "Price actually charged (may differ from products.price because of discounts).",
    },
    "reviews": {
        "_table": "Product ratings from customers.",
        "rating": "Integer 1-5.",
        "comment": "Optional free text; may be NULL.",
    },
}

_FIRST = ["Alice", "Bob", "Carol", "Dan", "Eve", "Frank", "Grace", "Hector", "Ivy", "Jamal",
          "Keiko", "Liam", "Maya", "Noah", "Olga", "Priya", "Quinn", "Rosa", "Sam", "Tara",
          "Umar", "Vera", "Wei", "Ximena", "Yusuf", "Zoe"]
_LAST = ["Smith", "Garcia", "Chen", "Patel", "Nguyen", "Kim", "Brown", "Lopez", "Singh", "Muller",
         "Rossi", "Tanaka", "Okafor", "Silva", "Novak", "Haddad", "Johnson", "Ivanova"]
_CITIES = [("Boise", "USA"), ("Seattle", "USA"), ("Portland", "USA"), ("Austin", "USA"),
           ("Denver", "USA"), ("Toronto", "Canada"), ("Vancouver", "Canada"), ("London", "UK"),
           ("Manchester", "UK"), ("Berlin", "Germany"), ("Munich", "Germany"), ("Mumbai", "India"),
           ("Delhi", "India"), ("Tokyo", "Japan")]
_CATEGORY_TREE = {
    "Electronics": ["Computers", "Audio", "Phones"],
    "Home": ["Kitchen", "Furniture"],
    "Office": ["Stationery", "Desk Accessories"],
    "Outdoors": ["Camping", "Cycling"],
}
_PRODUCTS = {
    "Computers": [("Laptop Pro 14", 1299.0), ("Ultrabook Air", 999.0), ("Mechanical Keyboard", 89.0),
                  ("Wireless Mouse", 29.0), ("27in Monitor", 279.0)],
    "Audio": [("Noise-Cancel Headphones", 249.0), ("Bluetooth Speaker", 59.0), ("Earbuds", 79.0)],
    "Phones": [("Phone X", 899.0), ("Phone Lite", 399.0), ("USB-C Charger", 19.0), ("Phone Case", 15.0)],
    "Kitchen": [("Espresso Machine", 349.0), ("Chef Knife", 69.0), ("Cast Iron Pan", 45.0), ("Kettle", 35.0)],
    "Furniture": [("Standing Desk", 499.0), ("Ergonomic Chair", 329.0), ("Bookshelf", 120.0)],
    "Stationery": [("Notebook", 5.5), ("Gel Pen Pack", 4.0), ("Sticky Notes", 3.25), ("Planner 2025", 18.0)],
    "Desk Accessories": [("Desk Lamp", 39.0), ("Monitor Arm", 99.0), ("Cable Organizer", 12.0)],
    "Camping": [("2-Person Tent", 189.0), ("Sleeping Bag", 99.0), ("Headlamp", 25.0), ("Camp Stove", 65.0)],
    "Cycling": [("Road Helmet", 110.0), ("Bike Light Set", 35.0), ("Water Bottle", 9.0)],
}
_DEPARTMENTS = ["Sales", "Support", "Engineering", "Marketing"]


def _iso(d: date) -> str:
    return d.isoformat()


def build(db_path: Path) -> None:
    rng = random.Random(SEED)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA)

    # categories
    cat_id: dict[str, int] = {}
    next_id = 1
    for parent, children in _CATEGORY_TREE.items():
        cat_id[parent] = next_id
        conn.execute("INSERT INTO categories VALUES (?, ?, NULL)", (next_id, parent))
        next_id += 1
        for child in children:
            cat_id[child] = next_id
            conn.execute("INSERT INTO categories VALUES (?, ?, ?)", (next_id, child, cat_id[parent]))
            next_id += 1

    # products
    products: list[tuple[int, float]] = []
    pid = 1
    for category, items in _PRODUCTS.items():
        for name, price in items:
            discontinued = 1 if name in {"Planner 2025", "Phone Lite"} else 0
            stock = 0 if discontinued else rng.randint(0, 250)
            conn.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)",
                         (pid, name, cat_id[category], price, stock, discontinued))
            products.append((pid, price))
            pid += 1
    never_ordered = {products[-1][0], products[-5][0]}  # guaranteed LEFT JOIN teaching cases

    # employees: CEO -> department heads -> reps
    employees = [(1, "Morgan Blake", "CEO", "Executive", None, "2015-03-01", 250_000)]
    heads: dict[str, int] = {}
    eid = 2
    for dept in _DEPARTMENTS:
        heads[dept] = eid
        employees.append((eid, f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", f"Head of {dept}", dept, 1,
                          _iso(date(2016, 1, 1) + timedelta(days=rng.randint(0, 900))), rng.randint(140, 190) * 1000))
        eid += 1
    for _ in range(20):
        dept = rng.choice(_DEPARTMENTS)
        title = {"Sales": "Sales Rep", "Support": "Support Agent", "Engineering": "Engineer",
                 "Marketing": "Marketer"}[dept]
        employees.append((eid, f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", title, dept, heads[dept],
                          _iso(date(2018, 1, 1) + timedelta(days=rng.randint(0, 2400))), rng.randint(55, 130) * 1000))
        eid += 1
    conn.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)", employees)
    sales_reps = [e[0] for e in employees if e[3] == "Sales"]

    # customers
    customers = []
    for cid in range(1, 161):
        first, last = rng.choice(_FIRST), rng.choice(_LAST)
        city, country = rng.choice(_CITIES)
        email = None if rng.random() < 0.12 else f"{first}.{last}{cid}@example.com".lower()
        phone = None if rng.random() < 0.3 else f"+1-555-{rng.randint(1000, 9999)}"
        signup = date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))
        customers.append((cid, first, last, email, phone, city, country, _iso(signup)))
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?)", customers)
    buyers = [c for c in customers if rng.random() > 0.15]  # ~15% never order

    # orders + items
    oid = 1
    orderable = [p for p in products if p[0] not in never_ordered]
    for cust in buyers:
        signup = date.fromisoformat(cust[7])
        for _ in range(rng.choice([1, 1, 2, 3, 4, 5, 8])):
            order_date = signup + timedelta(days=rng.randint(0, 700))
            if order_date > date(2025, 12, 31):
                order_date = date(2025, 12, 31) - timedelta(days=rng.randint(0, 60))
            status = rng.choices(["delivered", "shipped", "pending", "cancelled"], [70, 12, 8, 10])[0]
            employee = rng.choice(sales_reps) if rng.random() < 0.6 else None
            ship_city = cust[5] if rng.random() < 0.9 else rng.choice(_CITIES)[0]
            conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                         (oid, cust[0], employee, _iso(order_date), status, ship_city))
            for prod_id, price in rng.sample(orderable, rng.randint(1, 4)):
                discount = rng.choice([1.0, 1.0, 1.0, 0.9, 0.85])
                conn.execute("INSERT INTO order_items VALUES (?, ?, ?, ?)",
                             (oid, prod_id, rng.choice([1, 1, 1, 2, 3, 5]), round(price * discount, 2)))
            oid += 1

    # reviews
    comments = [None, None, "Great value.", "Would buy again.", "Stopped working after a month.",
                "Exactly as described.", "Shipping was slow.", "Five stars!"]
    for rid in range(1, 351):
        conn.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?)", (
            rid, rng.choice(orderable)[0], rng.choice(buyers)[0],
            rng.choices([1, 2, 3, 4, 5], [5, 8, 17, 35, 35])[0],
            _iso(date(2024, 1, 1) + timedelta(days=rng.randint(0, 720))), rng.choice(comments)))

    conn.commit()
    conn.close()
    tmp.replace(db_path)
