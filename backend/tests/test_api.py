"""API tests. The LLM is replaced by a fake so tests are fast, deterministic and offline."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import LLMUnavailable


class OfflineLLM:
    def available(self):
        return False

    def chat(self, *a, **k):
        raise LLMUnavailable("offline")

    def chat_json(self, *a, **k):
        raise LLMUnavailable("offline")

    def stream(self, *a, **k):
        raise LLMUnavailable("offline")

    def embed(self, *a, **k):
        raise LLMUnavailable("offline")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "_client", OfflineLLM())
    monkeypatch.setattr(main.tutor, "client", OfflineLLM())
    with TestClient(main.app) as c:
        yield c


def _sse(text: str) -> list[tuple[str, object]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_health_reports_llm_status(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["llm_available"] is False


def test_run_query_returns_rows_and_findings(client):
    body = client.post("/api/query/run", json={"sql": "SELECT id FROM customers WHERE email = NULL"}).json()
    assert body["ok"] and body["row_count"] == 0
    assert body["findings"][0]["id"] == "null-equality"


def test_run_query_explains_errors(client):
    body = client.post("/api/query/run", json={"sql": "SELECT price FROM orders"}).json()
    assert not body["ok"] and "products" in body["findings"][0]["message"]


def test_write_attempt_is_rejected(client):
    body = client.post("/api/query/run", json={"sql": "DROP TABLE customers"}).json()
    assert not body["ok"]


def test_submit_wrong_then_right_updates_mastery(client):
    lid = "pytest-learner"
    client.delete(f"/api/learners/{lid}")
    wrong = client.post("/api/exercises/outer-1/submit", json={
        "learner_id": lid, "sql": "SELECT c.id, c.first_name, c.last_name FROM customers c JOIN orders o ON o.customer_id = c.id"}).json()
    assert not wrong["correct"] and wrong["diff"]["expected_rows"] == 33
    right = client.post("/api/exercises/outer-1/submit", json={
        "learner_id": lid, "sql": "SELECT id, first_name, last_name FROM customers WHERE id NOT IN (SELECT customer_id FROM orders)"}).json()
    assert right["correct"], right["findings"]  # a different but equivalent query also passes
    assert right["mastery_changes"]["outer_join"]["after"] > right["mastery_changes"]["outer_join"]["before"]
    profile = client.get(f"/api/learners/{lid}").json()
    assert "outer-1" in profile["solved"]


def test_chat_explain_works_offline_with_fallback(client):
    r = client.post("/api/chat", json={"message": "SELECT city, COUNT(*) FROM customers GROUP BY city"})
    events = _sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    meta = events[0][1]
    assert meta["intent"] == "explain_sql" and [s["clause"] for s in meta["stages"]] == ["FROM", "GROUP BY", "SELECT"]
    text = "".join(d for e, d in events if e == "token")
    assert "offline" in text


def test_answer_leak_guard_turns_request_into_hint(client):
    r = client.post("/api/chat", json={"message": "write the query that finds customers who never ordered",
                                       "exercise_id": "outer-1", "hint_level": 1})
    meta = _sse(r.text)[0][1]
    assert meta["intent"] == "hint"
    assert "LEFT JOIN" not in "".join(d for e, d in _sse(r.text) if e == "token")


def test_custom_database_lifecycle(client):
    r = client.post("/api/databases", json={"name": "pytest_zoo", "schema_sql": "CREATE TABLE animals (id INTEGER PRIMARY KEY, species TEXT);",
                                            "seed_sql": "INSERT INTO animals (species) VALUES ('owl'), ('fox');"})
    db_id = r.json()["db_id"]
    assert client.get(f"/api/databases/{db_id}/schema").json()[0]["row_count"] == 2
    assert client.post("/api/databases", json={"name": "bad", "schema_sql": "ATTACH '/tmp/x' AS x;"}).status_code == 400
