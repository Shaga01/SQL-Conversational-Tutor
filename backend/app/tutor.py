from __future__ import annotations

import json
import re
from typing import Literal

import requests
import sqlglot

from .db import get_connection, get_schema_catalog, get_schema_text
from .sql_rules import explain_common_mistakes, is_select_only


OLLAMA_URL = "http://localhost:11434/api/generate"
SQLCODER_MODEL = "sqlcoder:latest"


def detect_intent(message: str) -> Literal["generate_sql", "explain_sql", "other"]:
    text = message.lower()
    if any(w in text for w in ("write sql", "generate", "query for", "find", "show me")):
        return "generate_sql"
    if any(w in text for w in ("explain", "why", "what does", "review sql", "is this query")):
        return "explain_sql"
    return "other"


def _call_model(prompt: str) -> tuple[str, str]:
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": SQLCODER_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip(), ""
    except Exception as exc:
        return "", f"Could not reach local SQLCoder model via Ollama: {exc}"


def _planner_prompt(question: str, schema_text: str, schema_catalog: dict) -> str:
    return f"""You are a SQL planning assistant.
Return ONLY valid JSON with keys:
- intent
- tables (array)
- columns (array)
- joins (array of strings)
- filters (array of strings)
- aggregations (array of strings)
- order_by (array of strings)
- limit (number or null)

Schema:
{schema_text}

Catalog:
{json.dumps(schema_catalog)}

User question:
{question}
"""


def _sql_prompt(question: str, plan_json: dict, schema_text: str) -> str:
    return f"""You are SQLCoder. Generate one SQLite SELECT query only.

Schema:
{schema_text}

Question (natural language):
{question}

Plan JSON:
{json.dumps(plan_json)}

Rules:
- Return only SQL.
- Use ONLY tables/columns from the schema.
- Use explicit JOIN conditions.
- Limit output to 100 rows when possible.
"""


def _repair_prompt(question: str, bad_sql: str, error_text: str, schema_text: str) -> str:
    return f"""Fix this SQLite SQL query based on the error.
Return ONLY corrected SQL.

Schema:
{schema_text}

Question:
{question}

Broken SQL:
{bad_sql}

Error:
{error_text}
"""


def _parse_planner_json(raw_text: str) -> tuple[dict, str]:
    candidate = raw_text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    if not candidate:
        return {}, "Planner returned empty output."
    # Try extracting the first JSON object from mixed text.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = candidate[start : end + 1]
    try:
        data = json.loads(candidate)
        return data, ""
    except Exception as exc:
        return {}, f"Planner JSON parse failed: {exc}"


def _deterministic_plan(question: str, schema_catalog: dict) -> dict:
    q = question.lower()
    tables = []
    all_tables = list(schema_catalog.get("tables", {}).keys())
    for t in all_tables:
        if t.lower() in q:
            tables.append(t)
    if not tables and all_tables:
        tables.append(all_tables[0])

    aggregations = []
    if any(k in q for k in ("count", "how many", "number of", "total")):
        aggregations.append("count")
    if any(k in q for k in ("average", "avg", "mean")):
        aggregations.append("avg")
    if any(k in q for k in ("sum", "total amount")):
        aggregations.append("sum")

    order_by = []
    if "top" in q or "highest" in q or "most" in q:
        order_by.append("desc")
    elif "lowest" in q or "least" in q:
        order_by.append("asc")

    return {
        "intent": "generate_sql",
        "tables": tables,
        "columns": [],
        "joins": [],
        "filters": [],
        "aggregations": aggregations,
        "order_by": order_by,
        "limit": 100,
    }


def _validate_sql(sql: str, session_id: str) -> tuple[bool, str]:
    safe, reason = is_select_only(sql)
    if not safe:
        return False, reason

    conn = get_connection(session_id)
    cur = conn.cursor()
    try:
        cur.execute(f"EXPLAIN QUERY PLAN {sql}")
    except Exception as exc:
        return False, f"Validation failed: {exc}"
    finally:
        conn.close()
    return True, ""


def _deterministic_sql(question: str, schema_catalog: dict) -> str:
    plan = _deterministic_plan(question, schema_catalog)
    tables = plan.get("tables", [])
    table = tables[0] if tables else ""
    if not table:
        all_tables = list(schema_catalog.get("tables", {}).keys())
        table = all_tables[0] if all_tables else ""
    if not table:
        return ""

    q = question.lower()
    if any(k in q for k in ("count", "how many", "number of", "total")):
        return f"SELECT COUNT(*) AS total_count FROM {table};"
    return f"SELECT * FROM {table} LIMIT 100;"


def generate_sql_with_guardrails(question: str, session_id: str = "default") -> tuple[str, str]:
    schema_text = get_schema_text(session_id)
    schema_catalog = get_schema_catalog(session_id)

    planner_raw, planner_err = _call_model(_planner_prompt(question, schema_text, schema_catalog))
    if planner_err:
        plan_json = _deterministic_plan(question, schema_catalog)
    else:
        plan_json, parse_err = _parse_planner_json(planner_raw)
        if parse_err:
            plan_json = _deterministic_plan(question, schema_catalog)

    sql_raw, sql_err = _call_model(_sql_prompt(question, plan_json, schema_text))
    if sql_err:
        deterministic = _deterministic_sql(question, schema_catalog)
        if deterministic:
            return deterministic, ""
        return "", sql_err
    sql = extract_sql(sql_raw)
    if not sql:
        deterministic = _deterministic_sql(question, schema_catalog)
        if deterministic:
            return deterministic, ""
        return "", "Model returned empty SQL."

    valid, why = _validate_sql(sql, session_id)
    if valid:
        return sql, ""

    repair_raw, repair_err = _call_model(_repair_prompt(question, sql, why, schema_text))
    if repair_err:
        return "", repair_err
    repaired_sql = extract_sql(repair_raw)
    if not repaired_sql:
        return "", f"Repair failed. Original validation error: {why}"

    repaired_valid, repaired_why = _validate_sql(repaired_sql, session_id)
    if repaired_valid:
        return repaired_sql, ""
    deterministic = _deterministic_sql(question, schema_catalog)
    if deterministic:
        return deterministic, ""
    return "", f"SQL generation failed after repair attempt: {repaired_why}"


def extract_sql(raw_text: str) -> str:
    raw_text = re.sub(r"\x1b\[[0-9;]*m", "", raw_text)
    raw_text = raw_text.replace("\u001b", "")
    fence = re.search(r"```sql(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return fence.group(1).strip()
    if raw_text.lower().startswith("select"):
        return raw_text
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    for line in lines:
        if line.lower().startswith("select"):
            return line
    match = re.search(r"select\s+.*?(?:;|$)", raw_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(0).strip()
    return ""


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
