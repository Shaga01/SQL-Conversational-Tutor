"""Mutation-based evaluation of the misconception detector (no LLM involved).

1. Start from queries known to be correct: Spider dev gold queries (20 databases the
   detector was never tuned on) and the tutor's own exercise solutions.
2. Inject one known bug per mutant by rewriting the sqlglot AST (e.g. LEFT JOIN -> JOIN,
   IS NULL -> = NULL, drop the ON clause). Each operator has an expected finding id.
3. Run the detector exactly as the app does and check whether the expected finding
   is reported:
     * static mode  - the learner is exploring freely, no reference solution;
     * grading mode - an exercise is active, so the result diff vs. the reference is
                      also available (as in /api/exercises/{id}/submit).
   Mutants whose result equals the original ("equivalent mutants") are excluded
   from grading-mode scoring, following standard mutation-testing practice.
4. False positives: run the detector on the *unmutated* correct queries.

Usage: python eval/misconception_eval.py [dev|train]\nOutput: eval/results/misconceptions_<split>.json and a section in eval/RESULTS.md.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.catalog import describe, resolve_db  # noqa: E402
from app.compare import diff_results, has_top_level_order_by, results_match  # noqa: E402
from app.sandbox import SandboxError, run_query  # noqa: E402
from app.sql_guard import parse_sql  # noqa: E402
from app.tutor.exercises import EXERCISES  # noqa: E402
from app.tutor.misconceptions import analyze_query, explain_error, from_result_diff  # noqa: E402
from sqlglot import exp  # noqa: E402

SPIDER = ROOT / "eval" / "data" / "spider_data"
OUT = ROOT / "eval" / "results" / "misconceptions.json"

# Rules about portability/style rather than correctness: they fire on Spider gold queries by
# design (Spider writes text literals in double quotes, which only SQLite tolerates).
PORTABILITY_RULES = {"double-quoted-string"}

Mutator = Callable[[exp.Expression, random.Random], exp.Expression | None]


def _sql(tree: exp.Expression) -> str:
    return tree.sql(dialect="sqlite")


# --------------------------------------------------------------------- mutation operators
# Each returns a mutated copy, or None when the operator does not apply to the query.

def left_to_inner(tree, rng):
    joins = [j for j in tree.find_all(exp.Join) if (j.args.get("side") or "").upper() == "LEFT"]
    if not joins:
        return None
    j = rng.choice(joins)
    j.set("side", None)
    return tree


def drop_join_condition(tree, rng):
    joins = [j for j in tree.find_all(exp.Join) if j.args.get("on") is not None]
    if not joins:
        return None
    rng.choice(joins).set("on", None)
    return tree


def is_null_to_eq(tree, rng):
    nodes = [n for n in tree.find_all(exp.Is) if isinstance(n.expression, exp.Null)]
    if not nodes:
        return None
    n = rng.choice(nodes)
    replacement = exp.EQ(this=n.this.copy(), expression=exp.Null())
    parent = n.parent
    if isinstance(parent, exp.Not):  # x IS NOT NULL  ->  x <> NULL
        parent.replace(exp.NEQ(this=n.this.copy(), expression=exp.Null()))
    else:
        n.replace(replacement)
    return tree


def drop_group_column(tree, rng):
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if group is None or not any(e.find(exp.AggFunc) for e in select.expressions):
            continue  # only an "ungrouped column" bug if an aggregate remains in the SELECT list
        bare = {e.sql().lower() for e in select.expressions if isinstance(e, exp.Column)}
        candidates = [g for g in group.expressions if isinstance(g, exp.Column) and g.sql().lower() in bare]
        if candidates:
            victim = rng.choice(candidates)
            rest = [g for g in group.expressions if g is not victim]
            if rest:
                group.set("expressions", rest)
            elif select.args.get("having") is None:
                select.set("group", None)
            else:
                continue
            return tree
    return None


def having_to_where(tree, rng):
    for select in tree.find_all(exp.Select):
        having = select.args.get("having")
        if having is None or select.args.get("where") is not None or not having.find(exp.AggFunc):
            continue
        select.set("where", exp.Where(this=having.this.copy()))
        select.set("having", None)
        return tree
    return None


def drop_order_keep_limit(tree, rng):
    if tree.args.get("order") is None or tree.args.get("limit") is None:
        return None
    tree.set("order", None)
    return tree


def literal_case(tree, rng):
    lits = [lit for eq in tree.find_all(exp.EQ) for lit in (eq.left, eq.right)
            if isinstance(lit, exp.Literal) and lit.is_string and any(c.isalpha() for c in lit.this)
            and lit.this != lit.this.swapcase()]
    if not lits:
        return None
    lit = rng.choice(lits)
    lit.replace(exp.Literal.string(lit.this.upper() if lit.this != lit.this.upper() else lit.this.lower()))
    return tree


def misspell_column(tree, rng):
    cols = [c for c in tree.find_all(exp.Column) if len(c.name) > 3 and not c.find_ancestor(exp.Order)]
    if not cols:
        return None
    c = rng.choice(cols)
    name = c.name
    i = rng.randrange(1, len(name) - 1)
    c.set("this", exp.to_identifier(name[:i] + name[i + 1:]))  # drop one letter: "price" -> "prce"
    return tree


def misspell_table(tree, rng):
    tables = [t for t in tree.find_all(exp.Table) if len(t.name) > 4]
    if not tables:
        return None
    t = rng.choice(tables)
    t.set("this", exp.to_identifier(t.name[:-1] if t.name.endswith("s") else t.name + "s"))
    return tree


def count_col_to_star(tree, rng):
    for select in tree.find_all(exp.Select):
        if select.args.get("group") is None:
            continue
        if not any((j.args.get("side") or "").upper() == "LEFT" for j in select.args.get("joins") or []):
            continue
        counts = [c for c in select.find_all(exp.Count) if isinstance(c.this, exp.Column)]
        if counts:
            rng.choice(counts).set("this", exp.Star())
            return tree
    return None


def swap_join_key(tree, rng, tables=None):
    """ON child.fk = parent.pk  ->  ON child.pk = parent.pk  (joining two unrelated ids)."""
    if tables is None:
        return None
    by_name = {t.name.lower(): t for t in tables}
    candidates = []
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is None:
            continue
        aliases = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
        for eq in on.find_all(exp.EQ):
            for fk_side in (eq.left, eq.right):
                if not isinstance(fk_side, exp.Column) or not fk_side.table:
                    continue
                table = by_name.get(aliases.get(fk_side.table.lower(), ""))
                col = next((c for c in table.columns if c.name.lower() == fk_side.name.lower()), None) if table else None
                pks = [c for c in table.columns if c.pk] if table else []
                if col is not None and col.references and len(pks) == 1 and pks[0].name.lower() != col.name.lower():
                    candidates.append((fk_side, pks[0].name))
    if not candidates:
        return None
    node, pk = rng.choice(candidates)
    node.set("this", exp.to_identifier(pk))
    return tree


def drop_distinct(tree, rng):
    if tree.args.get("distinct") is None:
        return None
    tree.set("distinct", None)
    return tree


OPERATORS: dict[str, tuple[Mutator, set[str], str]] = {
    # name: (mutator, finding ids that count as a correct diagnosis, skill)
    "LEFT JOIN → JOIN": (left_to_inner, {"needs-outer-join"}, "outer_join"),
    "drop ON condition": (drop_join_condition, {"cartesian-join", "too-slow"}, "inner_join"),
    "IS NULL → = NULL": (is_null_to_eq, {"null-equality"}, "nulls"),
    "drop GROUP BY column": (drop_group_column, {"ungrouped-column", "missing-group-by"}, "group_by"),
    "HAVING → WHERE": (having_to_where, {"aggregate-in-where"}, "having"),
    "drop ORDER BY before LIMIT": (drop_order_keep_limit, {"limit-without-order", "wrong-order"}, "order_limit"),
    "change literal case": (literal_case, {"literal-case"}, "where"),
    "misspell column": (misspell_column, {"unknown-column"}, "select"),
    "misspell table": (misspell_table, {"unknown-table"}, "select"),
    "COUNT(col) → COUNT(*) after LEFT JOIN": (count_col_to_star, {"count-star-left-join"}, "outer_join"),
    "drop DISTINCT": (drop_distinct, {"missing-distinct"}, "distinct"),
    "join on unrelated ids": (swap_join_key, {"wrong-join-key"}, "inner_join"),
}


# --------------------------------------------------------------------- detection

def detect(sql: str, db: Path, tables, reference: str | None, expected_result) -> tuple[list[str], bool | None]:
    """Findings as the app would produce them. Returns (ids, equivalent_to_reference)."""
    try:
        actual = run_query(db, sql, max_rows=20_000, timeout_s=3.0)
    except SandboxError as exc:
        ids = [f.id for f in explain_error(str(exc), tables, sql)]
        return ids + [f.id for f in analyze_query(sql, tables, db)], False
    ids = [f.id for f in analyze_query(sql, tables, db)]
    if reference is None:
        return ids, None
    order = has_top_level_order_by(reference)
    if results_match(expected_result.rows, actual.rows, order) and len(expected_result.columns) == len(actual.columns):
        return ids, True
    diff = diff_results(expected_result.rows, actual.rows, len(expected_result.columns), len(actual.columns), order)
    dup = len({tuple(r) for r in actual.rows}) < len(actual.rows)
    return ids + [f.id for f in from_result_diff(diff, sql, reference, dup)], False


def corpus(split: str) -> list[tuple[str, str, Path]]:
    """dev: Spider dev + exercises (used while developing the detector).
    train: Spider train gold queries on 140 other databases. Held out for the first detector
           version; a sample of its flags was later inspected to fix false positives.
    others: Spider "train_others" (6 more databases) - never inspected, the final held-out check."""
    items = [(f"exercise:{e.id}", e.solution, resolve_db(e.db_id)) for e in EXERCISES] if split == "dev" else []
    source = {"dev": "dev.json", "train": "train_spider.json", "others": "train_others.json"}[split]
    for i, q in enumerate(json.loads((SPIDER / source).read_text())):
        items.append((f"spider-{split}:{i}", q["query"], SPIDER / "database" / q["db_id"] / f"{q['db_id']}.sqlite"))
    return items


def main() -> None:
    split = sys.argv[1] if len(sys.argv) > 1 else "dev"
    out = OUT.with_name(f"misconceptions_{split}.json")
    rng = random.Random(0)
    stats = defaultdict(lambda: Counter())
    fp_rules: Counter = Counter()
    clean_total = clean_flagged = 0
    examples: dict[str, list[dict]] = defaultdict(list)
    tables_cache: dict[Path, list] = {}

    for source, sql, db in corpus(split):
        try:
            tree = parse_sql(sql)
            expected = run_query(db, sql, max_rows=20_000, timeout_s=5.0, validate=False)
        except Exception:
            continue
        tables = tables_cache.setdefault(db, describe(db))

        # false positives on the correct query itself
        clean_total += 1
        clean_ids = [f.id for f in analyze_query(sql, tables, db)
                     if f.severity in ("error", "warning") and f.id not in PORTABILITY_RULES]
        if clean_ids:
            clean_flagged += 1
            fp_rules.update(set(clean_ids))

        for name, (mutate, wanted, _) in OPERATORS.items():
            mutant = mutate(tree.copy(), rng, tables) if mutate is swap_join_key else mutate(tree.copy(), rng)
            if mutant is None:
                continue
            msql = _sql(mutant)
            st = stats[name]
            st["applicable"] += 1
            static_ids, _ = detect(msql, db, tables, None, None)
            graded_ids, equivalent = detect(msql, db, tables, sql, expected)
            if equivalent:
                st["equivalent"] += 1
                continue
            st["scored"] += 1
            st["static_hit"] += bool(wanted & set(static_ids))
            st["graded_hit"] += bool(wanted & set(graded_ids))
            st["graded_any_error"] += bool(graded_ids)
            if not wanted & set(graded_ids) and len(examples[name]) < 3:
                examples[name].append({"source": source, "mutant": msql[:200], "found": graded_ids})

    per_op = {}
    for name, st in stats.items():
        n = st["scored"]
        per_op[name] = {
            "applicable": st["applicable"], "equivalent_excluded": st["equivalent"], "scored": n,
            "static_recall": round(st["static_hit"] / n, 3) if n else None,
            "graded_recall": round(st["graded_hit"] / n, 3) if n else None,
            "flagged_something": round(st["graded_any_error"] / n, 3) if n else None,
            "missed_examples": examples[name],
        }
    scored = sum(v["scored"] for v in per_op.values())
    summary = {
        "split": split,
        "mutants_scored": scored,
        "macro_graded_recall": round(sum(v["graded_recall"] for v in per_op.values() if v["graded_recall"] is not None)
                                     / max(1, sum(1 for v in per_op.values() if v["graded_recall"] is not None)), 3),
        "micro_graded_recall": round(sum(stats[k]["graded_hit"] for k in stats) / max(1, scored), 3),
        "micro_static_recall": round(sum(stats[k]["static_hit"] for k in stats) / max(1, scored), 3),
        "clean_queries": clean_total,
        "clean_flagged_rate": round(clean_flagged / max(1, clean_total), 3),
        "clean_flagged_by_rule": dict(fp_rules.most_common()),
        "operators": per_op,
    }
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "operators"}, indent=2))
    for name, v in sorted(per_op.items(), key=lambda kv: -kv[1]["scored"]):
        print(f"{name:40s} scored={v['scored']:4d} static={v['static_recall']} graded={v['graded_recall']}")


if __name__ == "__main__":
    main()
