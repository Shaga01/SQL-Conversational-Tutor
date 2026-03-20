from __future__ import annotations

import re
from typing import Literal

import requests
import sqlglot

from .db import get_schema_text
from .sql_rules import explain_common_mistakes


OLLAMA_URL = "http://localhost:11434/api/generate"
SQLCODER_MODEL = "sqlcoder:latest"


def detect_intent(message: str) -> Literal["generate_sql", "explain_sql", "other"]:
    text = message.lower()
    if any(w in text for w in ("write sql", "generate", "query for", "find", "show me")):
        return "generate_sql"
    if any(w in text for w in ("explain", "why", "what does", "review sql", "is this query")):
        return "explain_sql"
    return "other"


def call_sqlcoder(question: str) -> tuple[str, str]:
    schema = get_schema_text()
    prompt = f"""You are SQLCoder. Generate one SQLite SELECT query only.

Schema:
{schema}

Question:
{question}

Rules:
- Return only SQL.
- Use explicit JOIN conditions.
- Limit output to 100 rows when possible.
"""
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": SQLCODER_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()
        sql = extract_sql(raw)
        if not sql:
            return "", "SQLCoder returned empty output."
        return sql, ""
    except Exception as exc:
        return "", f"Could not reach local SQLCoder model via Ollama: {exc}"


def extract_sql(raw_text: str) -> str:
    fence = re.search(r"```sql(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return fence.group(1).strip()
    if raw_text.lower().startswith("select"):
        return raw_text
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    for line in lines:
        if line.lower().startswith("select"):
            return line
    return raw_text.strip()


def explain_sql(sql: str, level: str) -> str:
    try:
        ast = sqlglot.parse_one(sql, read="sqlite")
    except Exception as exc:
        return f"I found a syntax issue in your SQL: {exc}"

    parts: list[str] = []
    select_exprs = ast.expressions or []
    parts.append(f"Your query selects {len(select_exprs)} expression(s).")

    from_clause = ast.args.get("from")
    if from_clause:
        parts.append("It reads data from the table(s) in the FROM clause.")
    if ast.args.get("joins"):
        parts.append("It combines tables using JOIN conditions.")
    if ast.args.get("where"):
        parts.append("It filters rows with WHERE conditions before aggregation.")
    if ast.args.get("group"):
        parts.append("It groups rows using GROUP BY.")
    if ast.args.get("having"):
        parts.append("It applies HAVING after grouping.")
    if ast.args.get("order"):
        parts.append("It sorts results with ORDER BY.")
    if ast.args.get("limit"):
        parts.append("It restricts output size with LIMIT.")

    tips = explain_common_mistakes(sql)
    if tips:
        parts.append("Possible improvements: " + " ".join(tips))

    if level == "beginner":
        parts.insert(0, "Beginner view: think of SQL as filtering and reshaping a table step by step.")
    elif level == "advanced":
        parts.append("Advanced note: verify predicate pushdown and join cardinality assumptions.")

    return " ".join(parts)
