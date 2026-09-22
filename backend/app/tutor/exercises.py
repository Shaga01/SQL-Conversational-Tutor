"""Auto-graded exercises on the built-in ``shop`` database.

Grading is by *result equivalence* (see ``compare.py``), so any correct query passes,
not just one exact text. Each exercise carries a hint ladder used for Socratic tutoring:
nudge -> concept -> partial query. The full solution is revealed only as a last step.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exercise:
    id: str
    title: str
    prompt: str
    skills: tuple[str, ...]
    difficulty: int  # 1 (easy) .. 5 (hard)
    solution: str
    hints: tuple[str, str, str]
    db_id: str = "shop"

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "prompt": self.prompt, "skills": list(self.skills),
                "difficulty": self.difficulty, "db_id": self.db_id}


E = Exercise
EXERCISES: list[Exercise] = [
    # --- basics ---------------------------------------------------------------------------
    E("sel-1", "Product catalogue", "List the name and price of every product.", ("select",), 1,
      "SELECT name, price FROM products",
      ("Which table holds products?", "SELECT lists the columns you want, FROM names the table.",
       "SELECT name, ... FROM products")),
    E("sel-2", "Price with tax", "Show each product's name and its price including 8% tax, as a column named price_with_tax.",
      ("select",), 1, "SELECT name, price * 1.08 AS price_with_tax FROM products",
      ("You can compute new values in the SELECT list.", "Arithmetic works on columns; AS gives a result column a name.",
       "SELECT name, price * ... AS price_with_tax FROM products")),
    E("where-1", "Local customers", "Find the first and last names of customers who live in Boise.", ("where",), 1,
      "SELECT first_name, last_name FROM customers WHERE city = 'Boise'",
      ("Only some rows should survive - which clause filters rows?", "WHERE keeps rows where the condition is true. Text values go in single quotes.",
       "SELECT first_name, last_name FROM customers WHERE city = ...")),
    E("where-2", "Cheap electronics?", "List names and prices of products that cost between 20 and 100 dollars (inclusive) and are not discontinued.",
      ("where",), 2, "SELECT name, price FROM products WHERE price BETWEEN 20 AND 100 AND discontinued = 0",
      ("Two conditions must both hold.", "Combine conditions with AND. BETWEEN a AND b is inclusive on both ends.",
       "... WHERE price BETWEEN 20 AND 100 AND ...")),
    E("where-3", "Big cities", "List the ids and cities of customers living in Seattle, Portland or Denver.", ("where",), 1,
      "SELECT id, city FROM customers WHERE city IN ('Seattle', 'Portland', 'Denver')",
      ("Several OR conditions on the same column can be shortened.", "IN (a, b, c) is true when the value equals any item in the list.",
       "... WHERE city IN (...)")),
    E("null-1", "Missing emails", "Find the ids and names (first_name, last_name) of customers who have no email address.", ("nulls", "where"), 2,
      "SELECT id, first_name, last_name FROM customers WHERE email IS NULL",
      ("A missing value is stored as NULL. Does `= NULL` ever evaluate to true?",
       "NULL means 'unknown', so any comparison with = returns unknown, not true. SQL has a special operator for it.",
       "... WHERE email IS ...")),
    E("null-2", "Contact info", "For every customer show id and a contact column: their email, or their phone if the email is missing, or 'no contact' if both are missing.",
      ("nulls", "select"), 3, "SELECT id, COALESCE(email, phone, 'no contact') AS contact FROM customers",
      ("You need the first non-missing value from a list.", "COALESCE(a, b, c) returns the first argument that is not NULL.",
       "SELECT id, COALESCE(...) AS contact FROM customers")),
    E("order-1", "Most expensive", "Show the names and prices of the 5 most expensive products.", ("order_limit",), 1,
      "SELECT name, price FROM products ORDER BY price DESC LIMIT 5",
      ("'Top 5' needs two things: an order and a cut-off.", "ORDER BY ... DESC puts the largest first; LIMIT n keeps n rows. LIMIT without ORDER BY returns arbitrary rows.",
       "... ORDER BY price DESC LIMIT ...")),
    E("distinct-1", "Where are we?", "List each distinct country that has at least one customer.", ("distinct",), 1,
      "SELECT DISTINCT country FROM customers",
      ("Your result probably repeats countries.", "DISTINCT removes duplicate rows from the result.", "SELECT DISTINCT ... FROM customers")),
    # --- aggregation ----------------------------------------------------------------------
    E("agg-1", "Headcount", "How many customers are there, and how many of them have an email address? Return two numbers.",
      ("aggregate", "nulls"), 2, "SELECT COUNT(*), COUNT(email) FROM customers",
      ("There are two kinds of COUNT that treat NULL differently.", "COUNT(*) counts rows; COUNT(column) counts non-NULL values of that column.",
       "SELECT COUNT(*), COUNT(...) FROM customers")),
    E("agg-2", "Price statistics", "Show the minimum, maximum and average price of products that are not discontinued.",
      ("aggregate", "where"), 2, "SELECT MIN(price), MAX(price), AVG(price) FROM products WHERE discontinued = 0",
      ("Filter first, then summarise.", "WHERE runs before aggregate functions are computed.",
       "SELECT MIN(price), ... FROM products WHERE ...")),
    E("group-1", "Customers per country", "For each country, show the country and its number of customers.", ("group_by",), 2,
      "SELECT country, COUNT(*) FROM customers GROUP BY country",
      ("You need one output row per country.", "GROUP BY country makes one group per country; COUNT(*) is then computed inside each group.",
       "SELECT country, COUNT(*) FROM customers GROUP BY ...")),
    E("group-2", "Order status mix", "Count the orders in each status, most common status first.", ("group_by", "order_limit"), 2,
      "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC",
      ("Group, count, then sort by the count.", "You can ORDER BY an aggregate or by its alias.",
       "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY ...")),
    E("having-1", "Busy cities", "List cities with more than 12 customers, together with their customer count.", ("having",), 3,
      "SELECT city, COUNT(*) FROM customers GROUP BY city HAVING COUNT(*) > 12",
      ("The condition is about a group, not a single row.", "WHERE filters rows before grouping and cannot use aggregates; HAVING filters groups after aggregation.",
       "... GROUP BY city HAVING COUNT(*) > ...")),
    # --- joins ----------------------------------------------------------------------------
    E("join-1", "Who ordered?", "For every order show the order id, order date and the customer's last name.", ("inner_join",), 2,
      "SELECT o.id, o.order_date, c.last_name FROM orders o JOIN customers c ON c.id = o.customer_id",
      ("The customer's name lives in a different table than the order.", "JOIN ... ON matches rows whose key columns are equal: orders.customer_id refers to customers.id.",
       "... FROM orders o JOIN customers c ON c.id = o.customer_id")),
    E("join-2", "Category names", "List every product name with the name of its category, as columns product and category.", ("inner_join",), 2,
      "SELECT p.name AS product, c.name AS category FROM products p JOIN categories c ON c.id = p.category_id",
      ("Both tables have a column called name.", "Use table aliases and qualify columns (p.name, c.name) to avoid ambiguity.",
       "SELECT p.name AS product, c.name AS category FROM products p JOIN categories c ON ...")),
    E("join-3", "Revenue per product", "Show each product name with its total revenue from delivered orders (quantity * unit_price), highest first.",
      ("inner_join", "group_by", "where"), 4,
      "SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi "
      "JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id "
      "WHERE o.status = 'delivered' GROUP BY p.id, p.name ORDER BY revenue DESC",
      ("You need three tables: items (money), orders (status) and products (name).",
       "Join order_items to orders and products, filter status, then GROUP BY product and SUM.",
       "... FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN products p ON ... WHERE ... GROUP BY p.id, p.name")),
    E("outer-1", "Never ordered", "Find the ids and names (first_name, last_name) of customers who have never placed an order.", ("outer_join", "nulls"), 3,
      "SELECT c.id, c.first_name, c.last_name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL",
      ("An INNER JOIN only returns customers that DO have orders.", "LEFT JOIN keeps every customer; where no order matches, the order columns are NULL.",
       "... FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS ...")),
    E("outer-2", "Orders per customer", "For every customer (including those with no orders) show id and number of orders.", ("outer_join", "group_by"), 4,
      "SELECT c.id, COUNT(o.id) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
      ("Customers with zero orders must still appear, with 0.", "LEFT JOIN keeps them; COUNT(*) would count the NULL row as 1, COUNT(o.id) counts 0.",
       "SELECT c.id, COUNT(o.id) FROM customers c LEFT JOIN orders o ON ... GROUP BY c.id")),
    E("outer-3", "Unsold products", "List the names of products that appear in no order at all.", ("outer_join", "nulls"), 3,
      "SELECT p.name FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id WHERE oi.order_id IS NULL",
      ("Think 'products without a matching order item'.", "LEFT JOIN from products, then keep rows where the item side is NULL (or use NOT EXISTS).",
       "... FROM products p LEFT JOIN order_items oi ON ... WHERE oi.order_id IS NULL")),
    E("self-1", "Who's the boss?", "List each employee's name together with their manager's name (columns employee, manager). Skip the CEO.", ("self_join",), 3,
      "SELECT e.name AS employee, m.name AS manager FROM employees e JOIN employees m ON m.id = e.manager_id",
      ("The manager is also a row in employees.", "Join employees to itself with two aliases: one for the employee, one for the manager.",
       "... FROM employees e JOIN employees m ON m.id = e.manager_id")),
    # --- subqueries / case / dates ----------------------------------------------------------
    E("sub-1", "Above average", "List the names and prices of products priced above the average product price.", ("subquery",), 3,
      "SELECT name, price FROM products WHERE price > (SELECT AVG(price) FROM products)",
      ("You need the average first, then compare against it.", "A scalar subquery (SELECT AVG(...) FROM ...) can be used like a number in WHERE.",
       "... WHERE price > (SELECT AVG(price) FROM products)")),
    E("sub-2", "Loyal reviewers", "Find ids of customers who wrote a review AND placed at least one delivered order.", ("subquery",), 3,
      "SELECT DISTINCT customer_id FROM reviews WHERE customer_id IN (SELECT customer_id FROM orders WHERE status = 'delivered')",
      ("One set of customers must be inside another set.", "Use IN (subquery) or EXISTS; remember to remove duplicates.",
       "SELECT DISTINCT customer_id FROM reviews WHERE customer_id IN (SELECT ...)")),
    E("case-1", "Price bands", "Show each product name and a band: 'budget' under 50, 'mid' from 50 to 300, 'premium' above 300.", ("case",), 3,
      "SELECT name, CASE WHEN price < 50 THEN 'budget' WHEN price <= 300 THEN 'mid' ELSE 'premium' END AS band FROM products",
      ("You need an if/else inside the SELECT list.", "CASE WHEN cond THEN value ... ELSE value END. Conditions are checked in order.",
       "SELECT name, CASE WHEN price < 50 THEN 'budget' WHEN ... END AS band FROM products")),
    E("date-1", "Orders by month", "Count orders per month in 2025, as columns month (YYYY-MM) and n, in month order.", ("dates", "group_by"), 3,
      "SELECT strftime('%Y-%m', order_date) AS month, COUNT(*) AS n FROM orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY month ORDER BY month",
      ("Dates are stored as text 'YYYY-MM-DD'.", "strftime('%Y-%m', order_date) extracts the month; filter the year with a date range.",
       "SELECT strftime('%Y-%m', order_date) AS month, COUNT(*) AS n FROM orders WHERE ... GROUP BY month ORDER BY month")),
    # --- advanced -------------------------------------------------------------------------
    E("cte-1", "Big spenders", "Using a CTE, find customers (id) whose total spend on non-cancelled orders exceeds 3000, with the total.", ("cte", "group_by", "inner_join"), 4,
      "WITH spend AS (SELECT o.customer_id, SUM(oi.quantity * oi.unit_price) AS total FROM orders o "
      "JOIN order_items oi ON oi.order_id = o.id WHERE o.status <> 'cancelled' GROUP BY o.customer_id) "
      "SELECT customer_id, total FROM spend WHERE total > 3000",
      ("Compute each customer's total first, then filter.", "WITH name AS (query) lets you query an intermediate result like a table.",
       "WITH spend AS (SELECT o.customer_id, SUM(...) AS total FROM ... GROUP BY o.customer_id) SELECT ... FROM spend WHERE ...")),
    E("win-1", "Salary rank", "Rank employees by salary within their department (highest = 1). Show name, department, salary and rank.", ("window",), 4,
      "SELECT name, department, salary, RANK() OVER (PARTITION BY department ORDER BY salary DESC) AS rnk FROM employees",
      ("GROUP BY would collapse rows - you want to keep every employee.", "Window functions compute over a set of rows without collapsing them: RANK() OVER (PARTITION BY ... ORDER BY ...).",
       "SELECT name, department, salary, RANK() OVER (PARTITION BY department ORDER BY ...) FROM employees")),
    E("win-2", "Best seller per category", "For each category name, show its best-selling product by units sold (all orders) and the units.", ("window", "inner_join", "cte"), 5,
      "WITH units AS (SELECT c.name AS category, p.name AS product, SUM(oi.quantity) AS units, "
      "RANK() OVER (PARTITION BY c.id ORDER BY SUM(oi.quantity) DESC) AS r FROM order_items oi "
      "JOIN products p ON p.id = oi.product_id JOIN categories c ON c.id = p.category_id GROUP BY c.id, p.id) "
      "SELECT category, product, units FROM units WHERE r = 1",
      ("First total units per product, then pick the top one per category.",
       "Aggregate per product inside a CTE and rank within each category with a window function; keep rank 1.",
       "WITH units AS (SELECT ..., SUM(oi.quantity) AS units, RANK() OVER (PARTITION BY c.id ORDER BY SUM(oi.quantity) DESC) AS r ...) SELECT ... WHERE r = 1")),
]

BY_ID = {e.id: e for e in EXERCISES}
