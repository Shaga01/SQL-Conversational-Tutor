"""Deterministic misconception detector.

The LLM never decides *what* is wrong with a learner's query - this module does,
from three sources of evidence, and hands the findings to the LLM to phrase:

1. SQLite error messages, translated and enriched with the schema (did-you-mean).
2. Static analysis of the sqlglot AST (silent bugs SQLite happily executes).
3. Database-aware checks (e.g. a literal that only matches with different casing).
4. When grading an exercise, the result-set diff against the reference solution.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlglot import exp

from ..catalog import Table
from ..compare import ResultDiff
from ..sql_guard import friendly_syntax_error, parse_sql


@dataclass
class Finding:
    id: str
    severity: str  # "error" | "warning" | "info"
    skill: str
    title: str
    message: str
    evidence: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------------ helpers

class _Scope:
    """Maps aliases to tables for one SELECT and resolves columns to their table."""

    def __init__(self, select: exp.Select, tables: dict[str, Table]) -> None:
        self.tables = tables
        self.alias: dict[str, str] = {}
        for t in select.find_all(exp.Table):
            if t.find_ancestor(exp.Select) is select and t.name.lower() in tables:
                self.alias[(t.alias_or_name).lower()] = t.name.lower()
        self.in_scope = set(self.alias.values())

    def table_of(self, col: exp.Column) -> str | None:
        if col.table:
            return self.alias.get(col.table.lower())
        owners = [t for t in self.in_scope if any(c.name.lower() == col.name.lower() for c in self.tables[t].columns)]
        return owners[0] if len(owners) == 1 else None

    def column_type(self, col: exp.Column) -> str:
        t = self.table_of(col)
        if not t:
            return ""
        return next((c.type.upper() for c in self.tables[t].columns if c.name.lower() == col.name.lower()), "")

    def is_pk(self, table: str, column: str) -> bool:
        return any(c.pk and c.name.lower() == column.lower() for c in self.tables[table].columns)


def _snippet(node: exp.Expression) -> str:
    return node.sql(dialect="sqlite")[:160]


def _outside_subqueries(node: exp.Expression, root: exp.Expression, kinds) -> list[exp.Expression]:
    """find_all, but ignoring nodes that belong to a nested SELECT."""
    out = []
    for n in node.find_all(kinds):
        if n.find_ancestor(exp.Select) is root:
            out.append(n)
    return out


# ------------------------------------------------------------------------------ 1. errors

def explain_error(error: str, tables: list[Table], sql: str = "") -> list[Finding]:
    by_name = {t.name.lower(): t for t in tables}
    all_cols = {c.name.lower() for t in tables for c in t.columns}
    low = error.lower()

    if m := re.search(r"no such column: ([\w\.\"]+)", error, re.I):
        raw = m.group(1).strip('"')
        name = raw.split(".")[-1].lower()
        owners = [t.name for t in tables if any(c.name.lower() == name for c in t.columns)]
        if "." in raw and owners:
            msg = (f"`{raw}` does not exist. The column `{name}` lives in table(s) {', '.join(owners)} - "
                   f"check that the alias before the dot refers to the right table.")
        elif owners:
            msg = (f"Column `{name}` exists in {', '.join(owners)}, but that table is not part of this query's FROM/JOIN. "
                   "Join it (or query it) before using its columns.")
        else:
            guess = difflib.get_close_matches(name, all_cols, n=2, cutoff=0.6)
            msg = f"There is no column called `{name}`." + (f" Did you mean {' or '.join(f'`{g}`' for g in guess)}?" if guess else "")
        return [Finding("unknown-column", "error", "select", "Unknown column", msg, raw)]

    if m := re.search(r"no such table: ([\w\.]+)", error, re.I):
        name = m.group(1).lower()
        guess = difflib.get_close_matches(name, list(by_name), n=2, cutoff=0.5)
        msg = f"There is no table called `{name}`." + (f" Did you mean {' or '.join(f'`{g}`' for g in guess)}?" if guess else "")
        return [Finding("unknown-table", "error", "select", "Unknown table", msg, name)]

    if m := re.search(r"ambiguous column name: ([\w\.]+)", error, re.I):
        name = m.group(1)
        owners = [t.name for t in tables if any(c.name.lower() == name.lower() for c in t.columns)]
        return [Finding("ambiguous-column", "error", "inner_join", "Ambiguous column",
                        f"`{name}` exists in more than one joined table ({', '.join(owners)}). Prefix it with a table "
                        f"name or alias, e.g. `{owners[0][0] if owners else 't'}.{name}`.", name)]

    if "misuse of aggregate" in low:
        return [Finding("aggregate-in-where", "error", "having", "Aggregate used in WHERE",
                        "Aggregate functions (COUNT, SUM, AVG...) are computed after rows are grouped, but WHERE runs "
                        "before grouping. Filter on aggregates with HAVING instead.", "")]

    if "group by clause is required before having" in low or "having clause on a non-aggregate query" in low:
        return [Finding("having-without-group", "error", "having", "HAVING without GROUP BY",
                        "HAVING filters groups, so the query needs a GROUP BY first. To filter rows, use WHERE.", "")]

    if "syntax error" in low or "incomplete input" in low:
        return [_syntax_finding(error, sql)]

    if "time limit" in low:
        return [Finding("too-slow", "error", "inner_join", "Query too slow",
                        "The query ran out of time. This usually means a join without a proper ON condition, which "
                        "pairs every row with every other row (a Cartesian product).", "")]

    return [Finding("sql-error", "error", "select", "SQL error", error, "")]


_CLAUSES = ["select", "from", "where", "group by", "having", "order by", "limit"]


def _syntax_finding(error: str, sql: str) -> Finding:
    text = re.sub(r"'[^']*'", "''", sql.lower())
    positions = [(text.find(c), c) for c in _CLAUSES if re.search(rf"\b{c}\b", text)]
    positions = [(p, c) for p, c in positions if p >= 0]
    appeared = [c for _, c in sorted(positions)]
    expected = [c for c in _CLAUSES if c in appeared]
    if appeared != expected:
        return Finding("clause-order", "error", "select", "Clauses in the wrong order",
                       f"SQL clauses must appear in this order: {' -> '.join(c.upper() for c in expected)}. "
                       f"You wrote: {' -> '.join(c.upper() for c in appeared)}.", "")
    if re.search(r",\s*from\b", text):
        return Finding("trailing-comma", "error", "select", "Extra comma",
                       "There is a comma right before FROM. The last column in the SELECT list must not be followed by a comma.", "")
    if m := re.search(r'near "(\w+)"', error):
        return Finding("syntax", "error", "select", "Syntax error",
                       f"SQLite could not understand the query near `{m.group(1)}`. Check for a missing comma between "
                       "columns, a misspelled keyword, or an unclosed quote/parenthesis.", m.group(1))
    return Finding("syntax", "error", "select", "Syntax error", error, "")


# ------------------------------------------------------------------------------ 2+3. static

def analyze_query(sql: str, tables: list[Table], db_path: Path | None = None) -> list[Finding]:
    # sqlglot is deliberately forgiving (it accepts "SELECT a, FROM t" and misplaced clauses),
    # so judge syntax with SQLite's own strict parser first.
    if syntax_error := friendly_syntax_error(sql):
        return [_syntax_finding(syntax_error, sql)]
    try:
        tree = parse_sql(sql)
    except Exception as exc:
        return [_syntax_finding(friendly_syntax_error(sql) or str(exc), sql)]

    by_name = {t.name.lower(): t for t in tables}
    findings: list[Finding] = []
    for select in tree.find_all(exp.Select):
        scope = _Scope(select, by_name)
        findings += _null_comparisons(select)
        findings += _join_problems(select, scope, sql)
        findings += _grouping_problems(select, scope)
        findings += _left_join_problems(select, scope)
        findings += _misc(select, scope, tree)
        if db_path is not None:
            findings += _literal_values(select, scope, db_path)
    # de-duplicate by (id, evidence)
    seen, unique = set(), []
    for f in findings:
        if (f.id, f.evidence) not in seen:
            seen.add((f.id, f.evidence))
            unique.append(f)
    return unique


def _null_comparisons(select: exp.Select) -> list[Finding]:
    out = []
    for cmp in _outside_subqueries(select, select, (exp.EQ, exp.NEQ)):
        if isinstance(cmp.left, exp.Null) or isinstance(cmp.right, exp.Null):
            op = "IS NOT NULL" if isinstance(cmp, exp.NEQ) else "IS NULL"
            out.append(Finding("null-equality", "error", "nulls", "Comparing with NULL using = or <>",
                               f"`{_snippet(cmp)}` is never true: NULL means 'unknown', so any comparison with it is "
                               f"unknown. Use `{op}` instead.", _snippet(cmp)))
    return out


def _join_problems(select: exp.Select, scope: _Scope, tree_sql: str) -> list[Finding]:
    out = []
    joins = select.args.get("joins") or []
    where = select.args.get("where")
    for j in joins:
        kind = (j.args.get("kind") or "").upper()
        on = j.args.get("on")
        has_condition = (on is not None and not (isinstance(on, exp.Boolean) and on.this is True)) or j.args.get("using")
        explicit_cross = kind == "CROSS" and re.search(r"\bcross\s+join\b", tree_sql, re.I)
        if has_condition or explicit_cross:
            continue
        right = j.this.alias_or_name.lower() if isinstance(j.this, exp.Table) else ""
        linked = False
        if where is not None:
            for eq in _outside_subqueries(where, select, exp.EQ):
                if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
                    if right in {(eq.left.table or "").lower(), (eq.right.table or "").lower()}:
                        linked = True
        if not linked:
            out.append(Finding("cartesian-join", "error", "inner_join", "Join without a condition",
                               f"`{_snippet(j)}` has no ON condition, so every row is paired with every row of the other "
                               "table (a Cartesian product). Add `ON <key> = <foreign key>`.", _snippet(j)))

    # wrong join key: equality between two tables that are FK-related, but not via the FK columns
    for j in joins:
        on = j.args.get("on")
        if on is None or isinstance(on, exp.Boolean):
            continue
        for eq in on.find_all(exp.EQ):
            if not (isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column)):
                continue
            ta, tb = scope.table_of(eq.left), scope.table_of(eq.right)
            if not ta or not tb or ta == tb:
                continue
            fk_pairs = _fk_pairs(scope.tables, ta, tb)
            if not fk_pairs:
                continue
            used = {(ta, eq.left.name.lower(), tb, eq.right.name.lower()), (tb, eq.right.name.lower(), ta, eq.left.name.lower())}
            if not used & fk_pairs:
                a, ac, b, bc = sorted(fk_pairs)[0]
                out.append(Finding("wrong-join-key", "warning", "inner_join", "Suspicious join key",
                                   f"`{_snippet(eq)}` compares columns that are not linked. The relationship between "
                                   f"{ta} and {tb} is `{a}.{ac} = {b}.{bc}`.", _snippet(eq)))
    return out


def _fk_pairs(tables: dict[str, Table], ta: str, tb: str) -> set[tuple[str, str, str, str]]:
    pairs = set()
    for src, dst in ((ta, tb), (tb, ta)):
        for c in tables[src].columns:
            if c.references and c.references.split(".")[0].lower() == dst:
                pairs.add((src, c.name.lower(), dst, c.references.split(".")[1].lower()))
    return pairs


def _grouping_problems(select: exp.Select, scope: _Scope) -> list[Finding]:
    out = []
    group = select.args.get("group")
    has_agg = any(_outside_subqueries(e, select, exp.AggFunc) for e in select.expressions)
    has_window = any(e.find(exp.Window) for e in select.expressions)
    if (group is not None or has_agg) and not has_window:
        grouped = {e.sql().lower() for e in (group.expressions if group else [])}
        grouped_names = {e.name.lower() for e in (group.expressions if group else []) if isinstance(e, exp.Column)}
        grouped_tables_by_pk = {scope.table_of(e) for e in (group.expressions if group else [])
                                if isinstance(e, exp.Column) and scope.table_of(e) and scope.is_pk(scope.table_of(e), e.name)}
        aliases = {e.alias.lower() for e in select.expressions if isinstance(e, exp.Alias)}
        for e in select.expressions:
            inner = e.this if isinstance(e, exp.Alias) else e
            if not isinstance(inner, exp.Column) or inner.find_ancestor(exp.AggFunc):
                continue
            if inner.sql().lower() in grouped or inner.name.lower() in grouped_names or inner.name.lower() in aliases & grouped_names:
                continue
            if scope.table_of(inner) in grouped_tables_by_pk:
                continue  # functionally dependent on a grouped primary key - fine
            out.append(Finding("ungrouped-column", "warning", "group_by", "Column not in GROUP BY",
                               f"`{_snippet(inner)}` is neither aggregated nor listed in GROUP BY. Most databases reject "
                               "this; SQLite silently picks a value from an arbitrary row of each group. Add it to "
                               "GROUP BY or wrap it in an aggregate.", _snippet(inner)))

    where = select.args.get("where")
    if where is not None and _outside_subqueries(where, select, exp.AggFunc):
        out.append(Finding("aggregate-in-where", "error", "having", "Aggregate used in WHERE",
                           "WHERE runs before grouping, so it cannot use COUNT/SUM/AVG... Move the condition to HAVING.",
                           _snippet(where)))
    having = select.args.get("having")
    if having is not None and not _outside_subqueries(having, select, exp.AggFunc):
        out.append(Finding("having-no-aggregate", "info", "having", "HAVING could be WHERE",
                           "This HAVING condition does not use an aggregate, so it could be a WHERE condition, which "
                           "filters earlier and is usually faster.", _snippet(having)))
    return out


def _left_join_problems(select: exp.Select, scope: _Scope) -> list[Finding]:
    out = []
    left_aliases = {j.this.alias_or_name.lower() for j in select.args.get("joins") or []
                    if (j.args.get("side") or "").upper() == "LEFT" and isinstance(j.this, exp.Table)}
    if not left_aliases:
        return out
    where = select.args.get("where")
    if where is not None:
        for pred in _outside_subqueries(where, select, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.In, exp.Between)):
            if pred.find_ancestor(exp.Or) and pred.find_ancestor(exp.Where) is where:
                continue
            cols = [c for c in pred.find_all(exp.Column) if (c.table or "").lower() in left_aliases]
            if cols:
                out.append(Finding("left-join-nullified", "warning", "outer_join", "WHERE undoes the LEFT JOIN",
                                   f"`{_snippet(pred)}` filters on the right-hand table of a LEFT JOIN. Unmatched rows have "
                                   "NULL there, the condition is not true for NULL, so those rows are dropped - the LEFT "
                                   "JOIN behaves like an INNER JOIN. Move this condition into the ON clause if you want to "
                                   "keep unmatched rows.", _snippet(pred)))
    for count in _outside_subqueries(select, select, exp.Count):
        if isinstance(count.this, exp.Star) and select.args.get("group") is not None:
            out.append(Finding("count-star-left-join", "warning", "outer_join", "COUNT(*) after LEFT JOIN",
                               "With a LEFT JOIN, an unmatched row still produces one row full of NULLs, so COUNT(*) "
                               "returns 1 instead of 0. Count a column from the right table, e.g. COUNT(o.id).",
                               _snippet(count)))
    return out


def _misc(select: exp.Select, scope: _Scope, tree: exp.Expression) -> list[Finding]:
    out = []
    owner = tree if isinstance(tree, exp.SetOperation) else select
    if owner.args.get("limit") is not None and owner.args.get("order") is None and select is (tree if isinstance(tree, exp.Select) else select):
        out.append(Finding("limit-without-order", "warning", "order_limit", "LIMIT without ORDER BY",
                           "Without ORDER BY the database may return rows in any order, so LIMIT picks arbitrary rows. "
                           "If you want the 'top' rows, sort first.", _snippet(owner.args["limit"])))
    for div in _outside_subqueries(select, select, exp.Div):
        if _is_integer(div.left, scope) and _is_integer(div.right, scope):
            out.append(Finding("integer-division", "warning", "select", "Integer division",
                               f"`{_snippet(div)}` divides two integers, and SQLite then drops the decimals (7/2 = 3). "
                               "Multiply by 1.0 first, e.g. `1.0 * a / b`.", _snippet(div)))
    for col in _outside_subqueries(select, select, exp.Column):
        ident = col.this
        if isinstance(ident, exp.Identifier) and ident.quoted and not col.table:
            known = {c.name.lower() for t in scope.in_scope for c in scope.tables[t].columns}
            aliases = {e.alias.lower() for e in select.expressions if isinstance(e, exp.Alias)}
            if col.name.lower() not in known | aliases:
                out.append(Finding("double-quoted-string", "warning", "where", "Double quotes around text",
                                   f'In SQL, "{col.name}" means a column name. Text values use single quotes: \'{col.name}\'. '
                                   "SQLite silently falls back to text, but other databases will fail.", col.name))
    for like in _outside_subqueries(select, select, exp.Like):
        pattern = like.expression
        if isinstance(pattern, exp.Literal) and pattern.is_string and not re.search(r"[%_]", pattern.this):
            out.append(Finding("like-without-wildcard", "info", "where", "LIKE without wildcards",
                               f"`{_snippet(like)}` has no % or _ wildcard, so it only matches the exact text. "
                               "Use '%text%' to match text anywhere in the value.", _snippet(like)))
    return out


def _is_integer(node: exp.Expression, scope: _Scope) -> bool:
    if isinstance(node, exp.Literal):
        return not node.is_string and "." not in node.this
    if isinstance(node, exp.Count):
        return True
    if isinstance(node, exp.Column):
        return "INT" in scope.column_type(node)
    if isinstance(node, exp.Sum) and isinstance(node.this, exp.Column):
        return "INT" in scope.column_type(node.this)
    if isinstance(node, exp.Paren):
        return _is_integer(node.this, scope)
    return False


def _literal_values(select: exp.Select, scope: _Scope, db_path: Path) -> list[Finding]:
    out = []
    checks = []
    for eq in _outside_subqueries(select, select, exp.EQ):
        col, lit = (eq.left, eq.right) if isinstance(eq.left, exp.Column) else (eq.right, eq.left)
        if isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string:
            table = scope.table_of(col)
            if table:
                checks.append((table, col.name, lit.this, eq))
    if not checks:
        return out
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table, column, value, eq in checks[:6]:
            q = f'SELECT "{column}" FROM "{table}" WHERE "{column}" = ? LIMIT 1'
            if conn.execute(q, (value,)).fetchone():
                continue
            near = conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE LOWER(TRIM("{column}")) = LOWER(TRIM(?)) LIMIT 1',
                                (value,)).fetchone()
            if near:
                out.append(Finding("literal-case", "error", "where", "Value not found (check capitalisation)",
                                   f"No row has {column} = '{value}', but '{near[0]}' exists. Text comparison with = is "
                                   "case-sensitive in SQLite.", _snippet(eq)))
            else:
                samples = [r[0] for r in conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}" LIMIT 4')]
                out.append(Finding("literal-missing", "info", "where", "Value never appears",
                                   f"No row has {column} = '{value}'. Example values: {', '.join(map(repr, samples))}.",
                                   _snippet(eq)))
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


# ------------------------------------------------------------------------------ 4. diff

def from_result_diff(diff: ResultDiff, student_sql: str, reference_sql: str, actual_has_duplicates: bool) -> list[Finding]:
    if diff.match:
        return []
    out: list[Finding] = []
    s, r = student_sql.lower(), reference_sql.lower()
    if diff.expected_cols != diff.actual_cols:
        out.append(Finding("wrong-columns", "error", "select", "Wrong number of columns", diff.summary))
    elif diff.order_only:
        out.append(Finding("wrong-order", "error", "order_limit", "Rows in the wrong order", diff.summary))
    elif diff.actual_rows > diff.expected_rows and actual_has_duplicates and "distinct" in r and "distinct" not in s:
        out.append(Finding("missing-distinct", "error", "distinct", "Duplicate rows",
                           "Your result contains duplicate rows that should appear only once. Consider DISTINCT."))
    elif diff.actual_rows < diff.expected_rows and "left join" in r and "left join" not in s and " join " in s:
        out.append(Finding("needs-outer-join", "error", "outer_join", "Rows lost by INNER JOIN",
                           "Your join drops rows that have no match in the other table. A LEFT JOIN keeps them."))
    else:
        out.append(Finding("wrong-result", "error", "select", "Result differs from expected", diff.summary))
    return out
