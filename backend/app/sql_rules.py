from __future__ import annotations

import re

import sqlglot


FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "truncate", "attach", "pragma")


def is_select_only(sql: str) -> tuple[bool, str]:
    compact = sql.strip().lower()
    if not compact:
        return False, "Query is empty."
    if ";" in compact[:-1]:
        return False, "Only one SQL statement is allowed."

    if any(re.search(rf"\b{kw}\b", compact) for kw in FORBIDDEN):
        return False, "Only read-only SELECT queries are allowed."

    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception as exc:  # pragma: no cover
        return False, f"SQL syntax error: {exc}"

    if parsed.key.upper() != "SELECT":
        return False, "Only SELECT queries are allowed for safety."
    return True, ""


def explain_common_mistakes(sql: str) -> list[str]:
    tips: list[str] = []
    lowered = sql.lower()

    if "count(" in lowered and "group by" not in lowered and " where " in lowered:
        tips.append("If you want counts per category, you may need GROUP BY.")
    if "join" in lowered and " on " not in lowered:
        tips.append("JOIN appears without an ON condition. Check join keys.")
    if "select *" in lowered:
        tips.append("Prefer selecting explicit columns to improve readability.")
    if "order by" not in lowered:
        tips.append("Consider ORDER BY for deterministic output ordering.")

    return tips
