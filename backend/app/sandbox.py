"""Secure SQLite execution.

Defense in depth - each layer works on its own, so bypassing one is not enough:

1. ``sql_guard`` rejects anything that is not a single read-only statement (AST check).
2. Read-only connections are opened with ``mode=ro``, so SQLite itself refuses writes.
3. An *authorizer* callback is consulted by SQLite for every operation while the
   statement is compiled; only reads/selects/functions are permitted.
4. A *progress handler* aborts statements that exceed a wall-clock budget, and
   ``setlimit`` caps string/blob sizes so a query cannot exhaust memory.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings
from .sql_guard import check_read_only

# sqlite3 does not export every action code; values are from sqlite3.h.
SQLITE_RECURSIVE = 33

_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, SQLITE_RECURSIVE}
_ALWAYS_DENIED = {
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_CREATE_VTABLE,
    sqlite3.SQLITE_DROP_VTABLE,
}
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "fts3_tokenizer"}

_MAX_VALUE_BYTES = 1_000_000


class SandboxError(Exception):
    """Base class for errors that are safe to show to the learner."""


class QueryRejected(SandboxError):
    pass


class QueryTimeout(SandboxError):
    pass


class QueryFailed(SandboxError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    elapsed_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


def _read_authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


def _write_authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    if action in _ALWAYS_DENIED:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _install_limits(conn: sqlite3.Connection, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    # Called every N virtual-machine instructions; a non-zero return aborts the statement.
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 5_000)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_VALUE_BYTES)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1_000_000)
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)


def open_readonly(db_path: Path, timeout_s: float | None = None) -> sqlite3.Connection:
    if not db_path.exists():
        raise QueryFailed(f"Database not found: {db_path.name}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    conn.enable_load_extension(False)
    _install_limits(conn, timeout_s if timeout_s is not None else settings.query_timeout_s)
    conn.set_authorizer(_read_authorizer)
    return conn


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    return value


def run_query(
    db_path: Path,
    sql: str,
    *,
    max_rows: int | None = None,
    timeout_s: float | None = None,
    validate: bool = True,
) -> QueryResult:
    """Execute one read-only statement and return at most ``max_rows`` rows."""
    if validate:
        guard = check_read_only(sql)
        if not guard.ok:
            raise QueryRejected(guard.reason)

    limit = max_rows if max_rows is not None else settings.max_rows
    conn = open_readonly(db_path, timeout_s)
    started = time.perf_counter()
    try:
        cur = conn.execute(sql)
        fetched = cur.fetchmany(limit + 1)
        columns = [d[0] for d in cur.description or []]
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            budget = timeout_s if timeout_s is not None else settings.query_timeout_s
            raise QueryTimeout(f"Query exceeded the {budget:g}s time limit and was stopped.") from exc
        if "not authorized" in str(exc).lower():
            raise QueryRejected("This query tries an operation that is not allowed in the sandbox.") from exc
        raise QueryFailed(str(exc)) from exc
    except sqlite3.Error as exc:
        raise QueryFailed(str(exc)) from exc
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        conn.close()

    truncated = len(fetched) > limit
    rows = [[_jsonable(v) for v in row] for row in fetched[:limit]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated, elapsed_ms=elapsed)


def build_database(db_path: Path, schema_sql: str, seed_sql: str = "", timeout_s: float = 5.0) -> None:
    """Create a fresh database from learner-supplied DDL/DML.

    Writes are allowed here, but ATTACH/PRAGMA/virtual tables/extension loading are
    denied, the file is created from scratch (never touching shared datasets), and
    the script is time-limited. On failure the partial file is removed.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".building")
    tmp_path.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp_path)
    try:
        conn.enable_load_extension(False)
        _install_limits(conn, timeout_s)
        conn.set_authorizer(_write_authorizer)
        conn.executescript(schema_sql)
        if seed_sql.strip():
            conn.executescript(seed_sql)
        conn.commit()
    except sqlite3.Error as exc:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        message = str(exc)
        if "interrupted" in message.lower():
            message = f"Schema script exceeded the {timeout_s:g}s time limit."
        elif "not authorized" in message.lower():
            message = "Schema script uses a statement that is not allowed (ATTACH, PRAGMA, ...)."
        raise QueryFailed(message) from exc
    conn.close()
    tmp_path.replace(db_path)
