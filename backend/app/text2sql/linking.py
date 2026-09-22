"""Schema linking and value retrieval."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np

from ..catalog import Table
from ..llm import OllamaClient

_STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "what", "which",
         "who", "how", "many", "much", "list", "show", "find", "give", "me", "all", "each", "every", "with", "that",
         "than", "more", "less", "by", "from", "their", "its", "it", "do", "does", "did", "have", "has", "number",
         "name", "names", "return", "whose", "there", "at", "as", "be", "average", "total", "count", "most", "least"}


def _table_doc(t: Table) -> str:
    cols = ", ".join(c.name.replace("_", " ") for c in t.columns)
    return f"table {t.name.replace('_', ' ')}: {t.description} columns: {cols}"


def link_schema(question: str, tables: list[Table], client: OllamaClient, top_k: int = 4) -> list[Table]:
    """Keep the ``top_k`` most question-relevant tables plus FK 'bridge' tables that connect them."""
    docs = [_table_doc(t) for t in tables]
    vectors = client.embed([f"search_query: {question}"] + [f"search_document: {d}" for d in docs])
    scores = vectors[1:] @ vectors[0]

    q_words = set(re.findall(r"[a-z0-9]+", question.lower()))
    for i, t in enumerate(tables):  # lexical boost: table/column names mentioned verbatim
        names = {t.name.lower(), t.name.lower().rstrip("s")} | {c.name.lower() for c in t.columns}
        if names & q_words:
            scores[i] += 0.1

    keep = {tables[i].name for i in np.argsort(-scores)[:top_k]}
    by_name = {t.name: t for t in tables}
    # add tables that reference two kept tables (junction tables for many-to-many joins)
    for t in tables:
        targets = {c.references.split(".")[0] for c in t.columns if c.references}
        if t.name not in keep and len(targets & keep) >= 2:
            keep.add(t.name)
    return [by_name[n] for n in by_name if n in keep]


def _phrases(question: str) -> list[str]:
    quoted = re.findall(r"['\"]([^'\"]{2,40})['\"]", question)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-\.']*", question) if w.lower() not in _STOP and len(w) > 2]
    capitalised = re.findall(r"(?:[A-Z][\w\-']+(?:\s+[A-Z][\w\-']+)*)", question)
    seen, out = set(), []
    for p in quoted + capitalised + words:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out[:12]


def value_hints(question: str, db_path: Path, tables: list[Table], limit: int = 12) -> list[str]:
    """Find text values in the database equal (case-insensitively) to phrases in the question."""
    phrases = _phrases(question)
    if not phrases:
        return []
    hints: list[str] = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for t in tables:
            if t.row_count > 200_000:
                continue
            for c in t.columns:
                if "char" not in c.type.lower() and "text" not in c.type.lower() and c.type.upper() not in ("", "ANY"):
                    continue
                qt, qc = t.name.replace('"', '""'), c.name.replace('"', '""')
                marks = ",".join("?" * len(phrases))
                try:
                    rows = conn.execute(
                        f'SELECT DISTINCT "{qc}" FROM "{qt}" WHERE LOWER(TRIM("{qc}")) IN ({marks}) LIMIT 3',
                        [p.lower() for p in phrases]).fetchall()
                except sqlite3.Error:
                    continue
                for (value,) in rows:
                    hints.append(f"{t.name}.{c.name} = {value!r}")
                    if len(hints) >= limit:
                        return hints
    finally:
        conn.close()
    return hints
