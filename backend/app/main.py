from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .db import apply_schema_sql, get_connection, get_schema_text, init_db
from .sql_rules import is_select_only
from .tutor import detect_intent, explain_sql, generate_sql_with_guardrails


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    sql: Optional[str] = None
    session_id: str = "default"


class ExecuteRequest(BaseModel):
    sql: str = Field(min_length=1)
    session_id: str = "default"


class SchemaApplyRequest(BaseModel):
    session_id: str = Field(min_length=1)
    schema_sql: str = Field(min_length=1)
    seed_sql: str = ""


app = FastAPI(title="SQL Tutor API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/schema")
def schema(session_id: str = Query(default="default")) -> Dict[str, str]:
    return {"schema": get_schema_text(session_id)}


@app.post("/api/schema/apply")
def apply_schema(req: SchemaApplyRequest) -> Dict[str, str]:
    try:
        apply_schema_sql(req.session_id, req.schema_sql, req.seed_sql)
        return {"status": "ok", "message": f"Schema applied for session '{req.session_id}'."}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Schema apply error: {exc}") from exc


@app.post("/api/execute")
def execute(req: ExecuteRequest) -> Dict[str, Any]:
    safe, reason = is_select_only(req.sql)
    if not safe:
        raise HTTPException(status_code=400, detail=reason)

    conn = get_connection(req.session_id)
    cur = conn.cursor()
    try:
        cur.execute(req.sql)
        rows = [dict(r) for r in cur.fetchmany(200)]
        columns = list(rows[0].keys()) if rows else []
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Execution error: {exc}") from exc
    finally:
        conn.close()


@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict[str, str]:
    intent = detect_intent(req.message)

    if req.sql:
        return {"intent": "explain_sql", "response": explain_sql(req.sql, req.level), "sql": req.sql}

    if intent == "generate_sql":
        sql, err = generate_sql_with_guardrails(req.message, req.session_id)
        if err:
            fallback = "SELECT c.name, COUNT(o.id) AS order_count FROM customers c LEFT JOIN orders o ON c.id = o.customer_id GROUP BY c.id, c.name ORDER BY order_count DESC LIMIT 100;"
            response = (
                f"I could not call local SQLCoder right now. {err} "
                "I generated a fallback practice query so you can continue."
            )
            return {"intent": intent, "response": response, "sql": fallback}
        return {
            "intent": intent,
            "response": "I generated SQL. Run it to see results, then ask me to explain each clause.",
            "sql": sql,
        }

    guidance = (
        "Ask me to generate SQL from natural language, or paste your SQL and I will explain it "
        "step by step with mistake-aware feedback."
    )
    return {"intent": intent, "response": guidance, "sql": ""}
