"""Layer 1 of the sandbox: static validation on the parsed syntax tree.

The old implementation searched the raw text for words like "update", which both
rejected harmless queries (``WHERE status = 'update pending'``) and could never
understand structure. Here we parse with sqlglot and inspect the AST instead.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .config import settings

# Node types that must never appear anywhere in a learner's query.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # catch-all for statements sqlglot does not model (ATTACH, VACUUM, ...)
    exp.Pragma,
    exp.Transaction,
)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""
    tree: exp.Expression | None = None


def parse_sql(sql: str) -> exp.Expression:
    """Parse a single SQLite statement, raising ParseError with a readable message."""
    statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    if len(statements) != 1:
        raise ParseError(f"Expected exactly one SQL statement, found {len(statements)}.")
    return statements[0]


def check_read_only(sql: str) -> GuardResult:
    if not sql or not sql.strip():
        return GuardResult(False, "Query is empty.")
    if len(sql) > settings.max_sql_chars:
        return GuardResult(False, f"Query is longer than {settings.max_sql_chars} characters.")

    try:
        tree = parse_sql(sql)
    except ParseError as exc:
        return GuardResult(False, f"Syntax error: {friendly_syntax_error(sql) or _first_line(str(exc))}")

    if not isinstance(tree, exp.Query):
        return GuardResult(False, f"Only read-only queries (SELECT / WITH) are allowed, got {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return GuardResult(False, f"{node.key.upper()} is not allowed inside a read-only query.")

    return GuardResult(True, tree=tree)


def friendly_syntax_error(sql: str) -> str:
    """Ask SQLite to compile the statement; its parser messages are far clearer for learners.

    Compilation happens against an empty in-memory database, and SQLite reports syntax
    errors before name-resolution errors, so "no such table" means the syntax is fine.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"EXPLAIN {sql}")
    except sqlite3.Error as exc:
        message = str(exc)
        if "syntax error" in message or "incomplete input" in message:
            return message
    finally:
        conn.close()
    return ""


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text
