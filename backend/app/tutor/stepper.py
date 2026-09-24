"""Step-by-step execution visualiser.

SQL is *written* SELECT-FROM-WHERE-... but *evaluated* in a different logical order:

    FROM -> JOIN -> WHERE -> GROUP BY -> HAVING -> SELECT -> DISTINCT -> ORDER BY -> LIMIT

This module rebuilds the learner's query clause by clause (via the sqlglot AST),
executes every partial query in the sandbox and reports how many rows survive each
stage, with a small preview. No LLM is involved - the explanation is exact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlglot import exp

from ..sandbox import SandboxError, run_query
from ..sql_guard import parse_sql

PREVIEW_ROWS = 5


@dataclass
class Stage:
    clause: str
    sql: str
    explanation: str
    row_count: int | None = None
    columns: list[str] = field(default_factory=list)
    preview: list[list[Any]] = field(default_factory=list)
    error: str = ""


def _sql(node: exp.Expression) -> str:
    return node.sql(dialect="sqlite", comments=False)


def _strip(select: exp.Select, keep: set[str], joins: int | None = None) -> exp.Select:
    s = select.copy()
    for arg in ("joins", "where", "group", "having", "qualify", "order", "limit", "offset", "distinct"):
        if arg not in keep:
            s.set(arg, None)
    if joins is not None and select.args.get("joins"):
        s.set("joins", [j.copy() for j in select.args["joins"][:joins]])
    return s


def _star(select: exp.Select) -> exp.Select:
    s = select.copy()
    s.set("expressions", [exp.Star()])
    return s


def _run(db_path: Path, stage: Stage, count_sql: str | None = None) -> Stage:
    try:
        preview = run_query(db_path, stage.sql, max_rows=PREVIEW_ROWS, timeout_s=2.0)
        stage.columns, stage.preview = preview.columns, preview.rows
        if preview.truncated:
            total = run_query(db_path, count_sql or f"SELECT COUNT(*) FROM ({stage.sql})", max_rows=1, timeout_s=2.0)
            stage.row_count = int(total.rows[0][0])
        else:
            stage.row_count = preview.row_count
    except SandboxError as exc:
        stage.error = str(exc)
    return stage


def trace_query(sql: str, db_path: Path) -> list[dict[str, Any]]:
    try:
        tree = parse_sql(sql)
    except Exception as exc:
        return [asdict(Stage("PARSE", sql, f"The query could not be parsed: {exc}"))]

    with_ = tree.args.get("with")
    select = tree.copy()
    if with_ is not None:
        select.set("with", None)
    if not isinstance(select, exp.Select) or select.args.get("from_") is None and select.args.get("from") is None:
        stage = Stage("QUERY", sql, "This query combines several SELECTs (UNION/INTERSECT/EXCEPT) or has no FROM; "
                                    "it is shown as a single step.")
        return [asdict(_run(db_path, stage))]

    def attach(q: exp.Select) -> str:
        if with_ is not None:
            q = q.copy()
            q.set("with", with_.copy())
        return _sql(q)

    stages: list[Stage] = []
    from_ = select.args.get("from_") or select.args.get("from")
    base = from_.this
    stages.append(Stage("FROM", attach(_star(_strip(select, set(), joins=0))),
                        f"Start with every row of {_sql(base)}."))

    for i, j in enumerate(select.args.get("joins") or [], 1):
        side = (j.args.get("side") or "").upper()
        kind = (j.args.get("kind") or "").upper()
        target = _sql(j.this)
        on = j.args.get("on")
        cond = f" where {_sql(on)}" if on is not None and not isinstance(on, exp.Boolean) else ""
        if side == "LEFT":
            text = (f"LEFT JOIN {target}: pair each row with matching rows{cond}; rows with no match are kept, "
                    "with NULLs in the new columns.")
        elif kind == "CROSS" or not cond:
            text = f"JOIN {target} without a condition: every row is paired with every row of {target}."
        else:
            text = f"JOIN {target}: pair each row with the rows of {target}{cond}; rows with no match are dropped."
        stages.append(Stage(f"JOIN {i}", attach(_star(_strip(select, {"joins"}, joins=i))), text))

    keep = {"joins"}
    if select.args.get("where") is not None:
        keep.add("where")
        stages.append(Stage("WHERE", attach(_star(_strip(select, keep))),
                            f"Keep only rows where {_sql(select.args['where'].this)} is true (NULL counts as not true)."))

    group = select.args.get("group")
    if group is not None:
        keep.add("group")
        g = _strip(select, keep)
        g.set("expressions", [e.copy() for e in group.expressions] + [exp.alias_(exp.Count(this=exp.Star()), "rows_in_group")])
        stages.append(Stage("GROUP BY", attach(g),
                            f"Collapse rows into one group per distinct {', '.join(_sql(e) for e in group.expressions)}. "
                            "Aggregates like COUNT/SUM are computed per group."))
        if select.args.get("having") is not None:
            keep.add("having")
            h = _strip(select, keep)
            h.set("expressions", g.expressions)
            stages.append(Stage("HAVING", attach(h),
                                f"Keep only groups where {_sql(select.args['having'].this)} is true."))

    keep |= {"having", "qualify"}
    stages.append(Stage("SELECT", attach(_strip(select, keep)),
                        "Compute the output columns: " + ", ".join(_sql(e) for e in select.expressions)[:200] + "."))
    if select.args.get("distinct") is not None:
        keep.add("distinct")
        stages.append(Stage("DISTINCT", attach(_strip(select, keep)), "Remove duplicate output rows."))
    if select.args.get("order") is not None:
        keep.add("order")
        stages.append(Stage("ORDER BY", attach(_strip(select, keep)),
                            f"Sort by {', '.join(_sql(e) for e in select.args['order'].expressions)}. Row count does not change."))
    if select.args.get("limit") is not None:
        keep |= {"limit", "offset"}
        stages.append(Stage("LIMIT", attach(_strip(select, keep)),
                            f"Keep only the first {_sql(select.args['limit'].expression)} row(s)."))

    return [asdict(_run(db_path, s)) for s in stages]
