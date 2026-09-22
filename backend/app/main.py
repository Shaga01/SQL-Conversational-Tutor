"""HTTP API for the SQL tutor."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .catalog import UnknownDatabase, create_session_db, describe, ensure_builtin_datasets, list_databases, resolve_db, schema_json
from .compare import diff_results, has_top_level_order_by
from .config import settings
from .llm import get_client
from .sandbox import SandboxError, run_query
from .sql_guard import strip_comments
from .text2sql.examples import Example, ExampleStore
from .text2sql.pipeline import PipelineConfig, Text2SQL
from .tutor.conversation import Tutor, TutorContext
from .tutor.exercises import BY_ID, EXERCISES
from .tutor.learner import LearnerStore, infer_level, recommend, unlocked
from .tutor.misconceptions import analyze_query, explain_error, from_result_diff
from .tutor.skills import SKILLS, topo_order
from .tutor.stepper import trace_query

Level = Literal["auto", "beginner", "intermediate", "advanced"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_builtin_datasets()
    yield


app = FastAPI(title="SQL Tutor API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

store = LearnerStore()
_client = get_client()
_examples = ExampleStore([Example(e.prompt, e.solution, e.db_id) for e in EXERCISES], _client)
APP_PIPELINE = PipelineConfig(model=settings.sql_model, n_samples=1, self_correct_rounds=2, few_shot=3)
tutor = Tutor(_client, APP_PIPELINE, _examples)


def _db(db_id: str):
    try:
        return resolve_db(db_id)
    except UnknownDatabase as exc:
        raise HTTPException(404, str(exc)) from exc


# ------------------------------------------------------------------------------ meta

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "llm_available": _client.available(), "sql_model": settings.sql_model,
            "tutor_model": settings.tutor_model, "embed_model": settings.embed_model}


# ------------------------------------------------------------------------------ databases

class CreateDatabase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    schema_sql: str = Field(min_length=1, max_length=200_000)
    seed_sql: str = Field(default="", max_length=2_000_000)


@app.get("/api/databases")
def databases() -> list[dict[str, str]]:
    return list_databases()


@app.post("/api/databases")
def create_database(req: CreateDatabase) -> dict[str, str]:
    try:
        return {"db_id": create_session_db(req.name, req.schema_sql, req.seed_sql)}
    except (UnknownDatabase, SandboxError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/databases/{db_id}/schema")
def schema(db_id: str) -> list[dict]:
    return schema_json(describe(_db(db_id)))


# ------------------------------------------------------------------------------ queries

class QueryRequest(BaseModel):
    db_id: str = "shop"
    sql: str = Field(min_length=1, max_length=settings.max_sql_chars)


@app.post("/api/query/run")
def run(req: QueryRequest) -> dict[str, Any]:
    path = _db(req.db_id)
    tables = describe(path)
    try:
        result = run_query(path, req.sql)
    except SandboxError as exc:
        findings = explain_error(str(exc), tables, req.sql)
        return {"ok": False, "error": str(exc), "findings": [f.to_dict() for f in findings]}
    findings = analyze_query(req.sql, tables, path)
    return {"ok": True, **result.to_dict(), "findings": [f.to_dict() for f in findings]}


@app.post("/api/query/trace")
def trace(req: QueryRequest) -> dict[str, Any]:
    return {"stages": trace_query(req.sql, _db(req.db_id))}


class Text2SQLRequest(BaseModel):
    db_id: str = "shop"
    question: str = Field(min_length=1, max_length=2000)


@app.post("/api/text2sql")
def text2sql(req: Text2SQLRequest) -> dict[str, Any]:
    res = Text2SQL(APP_PIPELINE, client=_client, examples=_examples).run(req.question, _db(req.db_id))
    return {"sql": res.sql, "ok": res.ok, "error": res.error, "trace": res.trace_dicts(),
            "result": res.result.to_dict() if res.result else None}


# ------------------------------------------------------------------------------ exercises & learner

@app.get("/api/skills")
def skills() -> list[dict[str, Any]]:
    return [{"id": s, "name": SKILLS[s].name, "summary": SKILLS[s].summary, "prereqs": list(SKILLS[s].prereqs)}
            for s in topo_order()]


@app.get("/api/exercises")
def exercises(learner_id: str = "anonymous") -> list[dict[str, Any]]:
    solved = store.solved(learner_id)
    return [{**e.public(), "solved": e.id in solved} for e in EXERCISES]


class Submission(BaseModel):
    learner_id: str = Field(min_length=1, max_length=64)
    sql: str = Field(min_length=1, max_length=settings.max_sql_chars)
    hints_used: int = Field(default=0, ge=0, le=4)


@app.post("/api/exercises/{exercise_id}/submit")
def submit(exercise_id: str, req: Submission) -> dict[str, Any]:
    ex = BY_ID.get(exercise_id)
    if ex is None:
        raise HTTPException(404, "Unknown exercise")
    if not strip_comments(req.sql).strip().rstrip(";").strip():
        raise HTTPException(400, "Write a query before submitting.")  # not graded, mastery untouched
    path = resolve_db(ex.db_id)
    tables = describe(path)
    expected = run_query(path, ex.solution, max_rows=10_000)
    diff = None
    result = None
    try:
        actual = run_query(path, req.sql, max_rows=10_000)
        result = {**actual.to_dict(), "rows": actual.rows[:50]}
        diff = diff_results(expected.rows, actual.rows, len(expected.columns), len(actual.columns),
                            has_top_level_order_by(ex.solution))
        duplicates = len({tuple(r) for r in actual.rows}) < len(actual.rows)
        findings = analyze_query(req.sql, tables, path) + from_result_diff(diff, req.sql, ex.solution, duplicates)
        correct = diff.match
    except SandboxError as exc:
        findings = explain_error(str(exc), tables, req.sql)
        correct = False
    if correct:  # a correct answer can still get style advice, but no "errors"
        findings = [f for f in findings if f.severity != "error"]
    finding_dicts = [f.to_dict() for f in findings]
    changes = store.record_attempt(req.learner_id, ex, req.sql, correct, req.hints_used, finding_dicts)
    mastery = store.mastery(req.learner_id)
    nxt, reason = recommend(mastery, store.solved(req.learner_id))
    return {
        "correct": correct, "findings": finding_dicts, "diff": diff.to_dict() if diff else None, "result": result,
        "mastery_changes": {k: {"before": b, "after": a} for k, (b, a) in changes.items()},
        "next": {**nxt.public(), "reason": reason} if nxt else None,
        "solution": ex.solution if correct else None,
    }


@app.get("/api/learners/{learner_id}")
def learner(learner_id: str) -> dict[str, Any]:
    mastery = store.mastery(learner_id)
    nxt, reason = recommend(mastery, store.solved(learner_id))
    return {
        "learner_id": learner_id, "level": infer_level(mastery),
        "mastery": {s: round(p, 3) for s, p in mastery.items()}, "unlocked": unlocked(mastery),
        "solved": sorted(store.solved(learner_id)), "misconceptions": store.misconception_counts(learner_id),
        "history": store.history(learner_id), "next": {**nxt.public(), "reason": reason} if nxt else None,
    }


@app.delete("/api/learners/{learner_id}")
def reset_learner(learner_id: str) -> dict[str, str]:
    store.reset(learner_id)
    return {"status": "reset"}


# ------------------------------------------------------------------------------ chat (SSE)

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatRequest(BaseModel):
    learner_id: str = "anonymous"
    db_id: str = "shop"
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    level: Level = "auto"
    exercise_id: str | None = None
    current_sql: str = Field(default="", max_length=settings.max_sql_chars)
    hint_level: int = Field(default=0, ge=0, le=4)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    path = _db(req.db_id)
    level = infer_level(store.mastery(req.learner_id)) if req.level == "auto" else req.level
    ctx = TutorContext(db_path=path, level=level, exercise=BY_ID.get(req.exercise_id or ""),
                       current_sql=req.current_sql, history=[m.model_dump() for m in req.history])

    def events() -> Iterator[str]:
        try:
            turn = tutor.build_turn(req.message, ctx, req.hint_level)
            yield _sse("meta", {**turn.meta(), "level": level})
            for piece in tutor.stream_reply(req.message, ctx, turn):
                yield _sse("token", piece)
        except Exception as exc:  # never leave the client hanging mid-stream
            yield _sse("error", f"{type(exc).__name__}: {exc}")
        yield _sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
