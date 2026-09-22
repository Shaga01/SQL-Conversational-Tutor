"""Database registry and schema introspection.

A database is addressed by a ``db_id``:

* ``shop``             - built-in teaching dataset (read-only, generated on first use)
* ``session:<name>``   - a learner's own schema, created via ``/api/databases``
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import settings
from .datasets import shop
from .sandbox import QueryFailed, build_database

BUILTIN = {"shop": (shop.build, shop.DESCRIPTIONS)}
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UnknownDatabase(Exception):
    pass


@dataclass
class Column:
    name: str
    type: str
    pk: bool = False
    not_null: bool = False
    references: str | None = None  # "table.column"
    description: str = ""
    samples: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str
    columns: list[Column]
    row_count: int
    description: str = ""


def ensure_builtin_datasets() -> None:
    for name, (builder, _) in BUILTIN.items():
        path = settings.datasets_dir / f"{name}.db"
        if not path.exists():
            builder(path)


def resolve_db(db_id: str) -> Path:
    if db_id in BUILTIN:
        path = settings.datasets_dir / f"{db_id}.db"
        if not path.exists():
            BUILTIN[db_id][0](path)
        return path
    if db_id.startswith("session:"):
        name = db_id.split(":", 1)[1]
        if not _SESSION_RE.match(name):
            raise UnknownDatabase("Session names may only contain letters, digits, '_' and '-'.")
        path = settings.sessions_dir / f"{name}.db"
        if not path.exists():
            raise UnknownDatabase(f"No custom database named '{name}'. Create it first.")
        return path
    raise UnknownDatabase(f"Unknown database '{db_id}'.")


def create_session_db(name: str, schema_sql: str, seed_sql: str = "") -> str:
    if not _SESSION_RE.match(name):
        raise UnknownDatabase("Session names may only contain letters, digits, '_' and '-'.")
    build_database(settings.sessions_dir / f"{name}.db", schema_sql, seed_sql)
    describe.cache_clear()
    return f"session:{name}"


def list_databases() -> list[dict[str, str]]:
    items = [{"db_id": name, "kind": "builtin"} for name in BUILTIN]
    if settings.sessions_dir.exists():
        items += [{"db_id": f"session:{p.stem}", "kind": "custom"} for p in sorted(settings.sessions_dir.glob("*.db"))]
    return items


def _descriptions_for(db_path: Path) -> dict[str, dict[str, str]]:
    for name, (_, desc) in BUILTIN.items():
        if db_path == settings.datasets_dir / f"{name}.db":
            return desc
    return {}


@lru_cache(maxsize=256)
def _describe_cached(db_path_str: str, mtime: float) -> tuple[Table, ...]:
    db_path = Path(db_path_str)
    descriptions = _descriptions_for(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = []
        for t in names:
            q = t.replace('"', '""')
            fks = {row[3]: f"{row[2]}.{row[4] or 'rowid'}" for row in conn.execute(f'PRAGMA foreign_key_list("{q}")')}
            tdesc = descriptions.get(t, {})
            cols = []
            for _, cname, ctype, notnull, _default, pk in conn.execute(f'PRAGMA table_info("{q}")'):
                cq = cname.replace('"', '""')
                samples = [str(r[0])[:40] for r in conn.execute(
                    f'SELECT DISTINCT "{cq}" FROM "{q}" WHERE "{cq}" IS NOT NULL LIMIT 3')]
                cols.append(Column(cname, ctype or "ANY", bool(pk), bool(notnull), fks.get(cname),
                                   tdesc.get(cname, ""), samples))
            count = conn.execute(f'SELECT COUNT(*) FROM "{q}"').fetchone()[0]
            tables.append(Table(t, cols, count, tdesc.get("_table", "")))
        return tuple(tables)
    except sqlite3.Error as exc:
        raise QueryFailed(f"Could not read schema: {exc}") from exc
    finally:
        conn.close()


def describe(db_path: Path) -> list[Table]:
    return list(_describe_cached(str(db_path), db_path.stat().st_mtime))


describe.cache_clear = _describe_cached.cache_clear  # type: ignore[attr-defined]


def schema_prompt(tables: list[Table], *, with_samples: bool = True) -> str:
    """Compact, LLM-friendly schema text (types, keys, FKs, descriptions, sample values)."""
    lines: list[str] = []
    for t in tables:
        header = f"# Table: {t.name}"
        if t.description:
            header += f"  -- {t.description}"
        lines.append(header)
        for c in t.columns:
            parts = [f"  {c.name} {c.type}"]
            if c.pk:
                parts.append("PRIMARY KEY")
            if c.references:
                parts.append(f"-> {c.references}")
            notes = []
            if c.description:
                notes.append(c.description)
            if with_samples and c.samples and not (c.pk or c.references):
                notes.append("e.g. " + ", ".join(repr(s) for s in c.samples))
            if notes:
                parts.append("-- " + " ".join(notes))
            lines.append(" ".join(parts))
        lines.append("")
    return "\n".join(lines).strip()


def schema_json(tables: list[Table]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "row_count": t.row_count,
            "columns": [
                {"name": c.name, "type": c.type, "pk": c.pk, "references": c.references,
                 "description": c.description, "samples": c.samples}
                for c in t.columns
            ],
        }
        for t in tables
    ]
