"""Knowledge components (skills) and their prerequisite graph."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    summary: str
    prereqs: tuple[str, ...] = ()


SKILLS: dict[str, Skill] = {s.id: s for s in [
    Skill("select", "SELECT basics", "Choosing columns, aliases and computed expressions."),
    Skill("where", "Filtering with WHERE", "Keeping only rows that satisfy a condition (AND, OR, IN, BETWEEN, LIKE).", ("select",)),
    Skill("nulls", "NULL handling", "IS NULL, COALESCE, and how NULL behaves in comparisons and COUNT.", ("where",)),
    Skill("order_limit", "Sorting and LIMIT", "ORDER BY, ASC/DESC, and taking the top N rows.", ("select",)),
    Skill("distinct", "DISTINCT", "Removing duplicate rows.", ("select",)),
    Skill("aggregate", "Aggregate functions", "COUNT, SUM, AVG, MIN, MAX over a whole table.", ("where",)),
    Skill("group_by", "GROUP BY", "Computing aggregates per group.", ("aggregate",)),
    Skill("having", "HAVING", "Filtering groups after aggregation.", ("group_by",)),
    Skill("inner_join", "INNER JOIN", "Combining rows from related tables through keys.", ("where",)),
    Skill("outer_join", "LEFT JOIN", "Keeping unmatched rows; finding rows with no match.", ("inner_join", "nulls")),
    Skill("self_join", "Self join", "Joining a table to itself (e.g. employee -> manager).", ("inner_join",)),
    Skill("subquery", "Subqueries", "Queries inside WHERE/FROM/SELECT, IN and EXISTS.", ("aggregate", "where")),
    Skill("case", "CASE expressions", "Conditional logic inside a query.", ("select", "where")),
    Skill("dates", "Dates and strings", "strftime, date ranges, string functions.", ("where",)),
    Skill("cte", "CTEs (WITH)", "Naming intermediate results to structure a query.", ("subquery",)),
    Skill("window", "Window functions", "RANK, ROW_NUMBER, running totals with OVER (...).", ("group_by", "order_limit")),
]}


def topo_order() -> list[str]:
    order: list[str] = []
    seen: set[str] = set()

    def visit(sid: str) -> None:
        if sid in seen:
            return
        seen.add(sid)
        for p in SKILLS[sid].prereqs:
            visit(p)
        order.append(sid)

    for sid in SKILLS:
        visit(sid)
    return order
