"""Agentic text-to-SQL pipeline.

Every stage can be toggled through ``PipelineConfig`` so that its contribution can
be measured in an ablation study (see ``eval/``). The same code serves the web app.

    question
       |  (1) schema representation   raw DDL  vs  rich schema (descriptions + sample values)
       |  (2) schema linking          embedding-ranked tables + FK bridge tables
       |  (3) value hints             DB values matching words in the question ('France' vs 'france')
       |  (4) few-shot retrieval      most similar solved examples (RAG)
       v
    LLM -> candidate SQL(s)
       |  (5) execution-guided self-correction: run in the sandbox, feed errors back
       |  (6) self-consistency: sample N candidates, vote by execution result
       v
    final SQL + trace of every step
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..catalog import Table, describe, schema_prompt
from ..compare import _rows
from ..llm import OllamaClient, get_client
from ..sandbox import QueryResult, SandboxError, run_query
from .examples import ExampleStore
from .linking import link_schema, value_hints


@dataclass
class PipelineConfig:
    model: str = "qwen2.5-coder:7b"
    rich_schema: bool = True
    schema_linking: bool = False
    value_hints: bool = True
    few_shot: int = 3
    self_correct_rounds: int = 2
    n_samples: int = 1
    sample_temperature: float = 0.8
    max_tokens: int = 400

    @classmethod
    def baseline(cls, model: str = "qwen2.5-coder:7b") -> "PipelineConfig":
        """Single-shot prompting with raw DDL - what most tutorials do."""
        return cls(model=model, rich_schema=False, schema_linking=False, value_hints=False,
                   few_shot=0, self_correct_rounds=0, n_samples=1)


@dataclass
class Step:
    stage: str
    detail: str
    ms: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    sql: str
    ok: bool
    error: str = ""
    result: QueryResult | None = None
    candidates: list[str] = field(default_factory=list)
    trace: list[Step] = field(default_factory=list)

    def trace_dicts(self) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.trace]


SYSTEM = (
    "You are an expert SQLite analyst. Translate the user's question into ONE correct SQLite query.\n"
    "Rules:\n"
    "- Use only tables and columns that exist in the schema.\n"
    "- Select only the columns the question asks for, in the order it asks for them.\n"
    "- Use exact literal values as they appear in the database.\n"
    "- Return only the SQL inside a ```sql code block, with no explanation."
)

_FENCE = re.compile(r"```(?:sql|sqlite)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_BARE = re.compile(r"\b(WITH|SELECT)\b.*", re.IGNORECASE | re.DOTALL)


def extract_sql(text: str) -> str:
    if m := _FENCE.search(text):
        sql = m.group(1)
    elif m := _BARE.search(text):
        sql = m.group(0)
    else:
        return ""
    sql = sql.strip()
    # keep only the first statement
    return sql.split(";")[0].strip() if ";" in sql.rstrip(";") else sql.rstrip(";").strip()


def raw_ddl(db_path: Path, tables: list[Table]) -> str:
    import sqlite3

    wanted = {t.name for t in tables}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL").fetchall()
    finally:
        conn.close()
    return "\n\n".join(sql for name, sql in rows if name in wanted)


class Text2SQL:
    def __init__(self, config: PipelineConfig, client: OllamaClient | None = None,
                 examples: ExampleStore | None = None) -> None:
        self.config = config
        self.client = client or get_client()
        self.examples = examples

    # ------------------------------------------------------------------ prompt building

    def build_prompt(self, question: str, db_path: Path, trace: list[Step]) -> list[dict[str, str]]:
        cfg = self.config
        tables = describe(db_path)

        if cfg.schema_linking and len(tables) > 4:
            t0 = time.perf_counter()
            kept = link_schema(question, tables, self.client)
            trace.append(Step("schema_linking", f"kept {len(kept)}/{len(tables)} tables: {', '.join(t.name for t in kept)}",
                              _ms(t0), {"tables": [t.name for t in kept]}))
            tables = kept

        schema = schema_prompt(tables) if cfg.rich_schema else raw_ddl(db_path, tables)
        parts = [f"### Database schema\n{schema}"]

        if cfg.value_hints:
            t0 = time.perf_counter()
            hints = value_hints(question, db_path, tables)
            if hints:
                parts.append("### Values in the database that match words in the question\n" + "\n".join(hints))
            trace.append(Step("value_hints", f"{len(hints)} matching value(s)", _ms(t0), {"hints": hints}))

        messages = [{"role": "system", "content": SYSTEM}]
        if cfg.few_shot and self.examples is not None:
            t0 = time.perf_counter()
            shots = self.examples.nearest(question, cfg.few_shot, exclude_db=self.examples.exclude_db_for(db_path))
            for ex in shots:
                messages.append({"role": "user", "content": f"Question: {ex.question}"})
                messages.append({"role": "assistant", "content": f"```sql\n{ex.sql}\n```"})
            trace.append(Step("few_shot", f"retrieved {len(shots)} similar solved example(s)", _ms(t0),
                              {"examples": [e.question for e in shots]}))

        parts.append(f"### Question\n{question}")
        messages.append({"role": "user", "content": "\n\n".join(parts)})
        return messages

    # ------------------------------------------------------------------ generation

    def _generate(self, messages: list[dict[str, str]], temperature: float, seed: int) -> str:
        cfg = self.config
        return extract_sql(self.client.chat(messages, model=cfg.model, temperature=temperature,
                                            seed=seed, max_tokens=cfg.max_tokens))

    def _execute(self, db_path: Path, sql: str) -> tuple[QueryResult | None, str]:
        if not sql:
            return None, "The model did not return any SQL."
        try:
            return run_query(db_path, sql, max_rows=10_000, timeout_s=5.0), ""
        except SandboxError as exc:
            return None, str(exc)

    def _self_correct(self, messages, db_path, sql, result, error, trace, label):
        for round_ in range(1, self.config.self_correct_rounds + 1):
            if not error:
                break
            t0 = time.perf_counter()
            fix_messages = messages + [
                {"role": "assistant", "content": f"```sql\n{sql}\n```"},
                {"role": "user", "content": f"Running that query failed with this SQLite error:\n{error}\n\n"
                                            "Fix the query. Return only the corrected SQL in a ```sql block."},
            ]
            new_sql = self._generate(fix_messages, 0.0, round_)
            new_result, new_error = self._execute(db_path, new_sql)
            trace.append(Step("self_correct", f"{label} round {round_}: {'fixed' if not new_error else new_error[:120]}",
                              _ms(t0), {"before": sql, "after": new_sql, "error": error}))
            sql, result, error = new_sql, new_result, new_error
        return sql, result, error

    def run(self, question: str, db_path: Path) -> PipelineResult:
        cfg = self.config
        trace: list[Step] = []
        messages = self.build_prompt(question, db_path, trace)

        candidates: list[tuple[str, QueryResult | None, str]] = []
        for i in range(max(1, cfg.n_samples)):
            t0 = time.perf_counter()
            temperature = 0.0 if i == 0 else cfg.sample_temperature
            sql = self._generate(messages, temperature, seed=i)
            result, error = self._execute(db_path, sql)
            trace.append(Step("generate", f"candidate {i + 1}: {'ok' if not error else error[:120]}", _ms(t0),
                              {"sql": sql, "temperature": temperature}))
            if error and cfg.self_correct_rounds:
                sql, result, error = self._self_correct(messages, db_path, sql, result, error, trace, f"candidate {i + 1}")
            candidates.append((sql, result, error))

        sql, result, error = self._vote(candidates, trace) if len(candidates) > 1 else candidates[0]
        return PipelineResult(sql=sql, ok=not error, error=error, result=result,
                              candidates=[c[0] for c in candidates], trace=trace)

    @staticmethod
    def _vote(candidates, trace):
        t0 = time.perf_counter()
        groups: dict[Any, list[int]] = defaultdict(list)
        for i, (_, result, error) in enumerate(candidates):
            if error or result is None:
                continue
            signature = frozenset(Counter(_rows(result.rows)).items())
            groups[signature].append(i)
        if not groups:
            trace.append(Step("vote", "all candidates failed; keeping the greedy one", _ms(t0)))
            return candidates[0]
        # biggest cluster wins; ties go to the cluster containing the earliest (greedy) candidate
        best = max(groups.values(), key=lambda idx: (len(idx), -min(idx)))
        trace.append(Step("vote", f"{len(groups)} distinct result(s); winner agreed by {len(best)}/{len(candidates)}",
                          _ms(t0), {"cluster_sizes": sorted((len(v) for v in groups.values()), reverse=True)}))
        return candidates[min(best)]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
