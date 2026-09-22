"""Spider hardness levels (easy / medium / hard / extra), reimplemented on sqlglot.

Follows the component-counting rules of the official Spider ``evaluation.py``
(Yu et al., 2018). The official script parses SQL with its own grammar; this port
uses sqlglot, so a handful of borderline queries may be bucketed differently.
"""

from __future__ import annotations

from sqlglot import exp

from app.sql_guard import parse_sql

_AGGS = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)


def sql_features(sql: str) -> dict[str, bool]:
    try:
        tree = parse_sql(sql)
    except Exception:
        return {}
    selects = list(tree.find_all(exp.Select))
    return {
        "join": any(s.args.get("joins") for s in selects),
        "group_by": tree.find(exp.Group) is not None,
        "order_by": tree.find(exp.Order) is not None,
        "subquery": len(selects) > 1 and not isinstance(tree, exp.SetOperation),
        "set_op": tree.find(exp.SetOperation) is not None,
        "aggregate": tree.find(*_AGGS) is not None,
    }


def _count_where_conds(where: exp.Expression | None) -> int:
    if where is None:
        return 0
    return len(list(where.find_all(exp.And, exp.Or))) + 1


def hardness(sql: str) -> str:
    try:
        tree = parse_sql(sql)
    except Exception:
        return "unknown"

    root = tree
    while isinstance(root, exp.SetOperation):
        root = root.left
    if not isinstance(root, exp.Select):
        return "unknown"

    where = root.args.get("where")
    group = root.args.get("group")

    having = root.args.get("having")
    conds = [c for c in (where, having) if c is not None]
    comp1 = (
        (where is not None)
        + (group is not None)
        + (root.args.get("order") is not None or tree.args.get("order") is not None)
        + (root.args.get("limit") is not None or tree.args.get("limit") is not None)
        + len(root.args.get("joins") or [])
        + sum(len(list(c.find_all(exp.Or))) for c in conds)
        + sum(len(list(c.find_all(exp.Like))) for c in conds)
    )
    # nested queries inside the (left-most) SELECT, plus each set operation as one unit
    comp2 = sum(1 for s in root.find_all(exp.Select) if s is not root)
    node = tree
    while isinstance(node, exp.SetOperation):
        comp2 += 1
        node = node.left
    others = sum([
        sum(len(list(e.find_all(*_AGGS))) for e in root.expressions) > 1,
        len(root.expressions) > 1,
        _count_where_conds(where.this if where is not None else None) > 1,
        group is not None and len(group.expressions) > 1,
    ])

    if comp1 <= 1 and others == 0 and comp2 == 0:
        return "easy"
    if (others <= 2 and comp1 <= 1 and comp2 == 0) or (comp1 <= 2 and others < 2 and comp2 == 0):
        return "medium"
    if ((others > 2 and comp1 <= 2 and comp2 == 0) or (2 < comp1 <= 3 and others <= 2 and comp2 == 0)
            or (comp1 <= 1 and others == 0 and comp2 <= 1)):
        return "hard"
    return "extra"
