"""Result-set comparison, shared by the benchmark (execution accuracy) and the tutor
(grading a learner's query against a reference solution).

Rules follow the Spider test-suite evaluation:
* row order matters only if the reference query has a top-level ORDER BY;
  otherwise results are compared as multisets (duplicates count);
* column order does not matter (``SELECT a, b`` == ``SELECT b, a``);
* numbers are normalised so that ``1 == 1.0`` and floats compare after rounding.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import permutations
from typing import Any, Sequence

from sqlglot import exp

from .sql_guard import parse_sql

Row = Sequence[Any]


def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        f = float(value)
        return round(f, 4) if f != int(f) else int(f)
    return value


def _rows(rows: Sequence[Row], order: Sequence[int] | None = None) -> list[tuple]:
    if order is None:
        return [tuple(_norm(v) for v in r) for r in rows]
    return [tuple(_norm(r[i]) for i in order) for r in rows]


def has_top_level_order_by(sql: str) -> bool:
    try:
        tree = parse_sql(sql)
    except Exception:
        return "order by" in sql.lower()
    return isinstance(tree, exp.Query) and tree.args.get("order") is not None


def _column_orders(n: int, max_cols: int = 6):
    yield tuple(range(n))
    if n <= max_cols:
        for perm in permutations(range(n)):
            if perm != tuple(range(n)):
                yield perm


def results_match(expected: Sequence[Row], actual: Sequence[Row], order_matters: bool) -> bool:
    exp_rows = _rows(expected)
    if len(expected) != len(actual):
        return False
    if not expected:
        return True
    width = len(exp_rows[0])
    if any(len(r) != width for r in actual):
        return False
    target = exp_rows if order_matters else Counter(exp_rows)
    for order in _column_orders(width):
        candidate = _rows(actual, order)
        if (candidate if order_matters else Counter(candidate)) == target:
            return True
    return False


@dataclass
class ResultDiff:
    """A learner-facing description of how two result sets differ."""

    match: bool
    expected_rows: int
    actual_rows: int
    expected_cols: int
    actual_cols: int
    missing: list[tuple] = field(default_factory=list)  # in expected, not in actual
    extra: list[tuple] = field(default_factory=list)  # in actual, not in expected
    order_only: bool = False  # same rows, wrong order
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def diff_results(expected: Sequence[Row], actual: Sequence[Row], expected_cols: int, actual_cols: int,
                 order_matters: bool, sample: int = 5) -> ResultDiff:
    match = results_match(expected, actual, order_matters) if expected_cols == actual_cols else False
    d = ResultDiff(match, len(expected), len(actual), expected_cols, actual_cols)
    if match:
        d.summary = "Your result matches the expected result."
        return d
    if expected_cols != actual_cols:
        d.summary = f"Your query returns {actual_cols} column(s) but {expected_cols} are expected."
        return d

    exp_c, act_c = Counter(_rows(expected)), Counter(_rows(actual))
    d.missing = list((exp_c - act_c).elements())[:sample]
    d.extra = list((act_c - exp_c).elements())[:sample]
    if not d.missing and not d.extra and order_matters:
        d.order_only = True
        d.summary = "You have the right rows, but in the wrong order."
    elif len(actual) > len(expected) and not d.missing:
        d.summary = (f"Your result has {len(actual) - len(expected)} extra row(s) - a filter or join condition may be "
                     "missing, or duplicates were not removed.")
    elif len(actual) < len(expected) and not d.extra:
        d.summary = (f"Your result is missing {len(expected) - len(actual)} row(s) - a filter may be too strict, or an "
                     "INNER JOIN dropped unmatched rows.")
    elif len(actual) == len(expected):
        d.summary = "Same number of rows, but some values differ - check your calculations, grouping, or selected columns."
    else:
        d.summary = f"Expected {len(expected)} row(s) but got {len(actual)}, and the values differ."
    return d
