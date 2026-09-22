"""Conversational tutor: intent routing + grounded, level-adapted, streamed responses.

Design rule: deterministic components (analyzer, stepper, result diff, pipeline)
produce FACTS; the LLM only turns facts into a friendly explanation. The system
prompt forbids it from introducing problems that are not in the facts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..catalog import describe, schema_prompt
from ..compare import diff_results, has_top_level_order_by
from ..llm import LLMUnavailable, OllamaClient
from ..sandbox import SandboxError, run_query
from ..text2sql.pipeline import PipelineConfig, Text2SQL
from .exercises import Exercise
from .misconceptions import analyze_query, explain_error
from .stepper import trace_query

INTENTS = ["generate_sql", "explain_sql", "concept", "hint", "other"]

_SQL_START = re.compile(r"^\s*(select|with)\b", re.I)
_CODE_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.I | re.S)

LEVEL_STYLE = {
    "beginner": "The learner is a beginner: use plain language, short sentences, an everyday analogy, and define "
                "every SQL keyword you mention. No jargon like 'cardinality' or 'predicate'.",
    "intermediate": "The learner knows the basics: be concise, focus on the reasoning behind each clause and on "
                    "common pitfalls.",
    "advanced": "The learner is advanced: be brief and precise; mention edge cases (NULLs, duplicates, ties) and "
                "performance considerations such as indexes and join order where relevant.",
}

SYSTEM = """You are a patient, encouraging SQL tutor inside a learning app. {style}

Grounding rules (very important):
- The FACTS section was produced by a SQL analyzer and by actually running queries. Trust it.
- Only mention mistakes that appear in FACTS. Never invent errors, tables, columns or numbers.
- When you quote row counts or results, use the numbers from FACTS.
- If an exercise is active, NEVER write its full solution unless FACTS says reveal_solution: true.
  Guide with questions and hints instead (Socratic method).
- Use Markdown. Put SQL in ```sql blocks. Keep answers under 200 words unless asked for more."""


@dataclass
class TutorContext:
    db_path: Path
    level: str = "beginner"
    exercise: Exercise | None = None
    current_sql: str = ""
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class TutorTurn:
    """Structured part of a reply (sent to the UI before the streamed text)."""

    intent: str
    sql: str = ""
    findings: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    result: dict | None = None
    pipeline_trace: list[dict] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def meta(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "facts"}


class Tutor:
    def __init__(self, client: OllamaClient, pipeline_config: PipelineConfig | None = None, examples=None) -> None:
        self.client = client
        self.pipeline_config = pipeline_config or PipelineConfig(n_samples=1, self_correct_rounds=2, few_shot=3)
        self.examples = examples

    # ------------------------------------------------------------------ intent

    def classify(self, message: str, ctx: TutorContext) -> tuple[str, str, bool]:
        """Return (intent, sql_found_in_message, is_about_the_active_exercise)."""
        if block := _CODE_BLOCK.search(message):
            return "explain_sql", block.group(1).strip(), False
        if _SQL_START.match(message):
            return "explain_sql", message.strip(), False
        low = message.lower()
        if ctx.exercise and any(w in low for w in ("hint", "stuck", "help", "don't know", "dont know", "no idea")):
            return "hint", "", True
        exercise_line = f"\nThe learner is currently working on this exercise: {ctx.exercise.prompt}" if ctx.exercise else ""
        try:
            out = self.client.chat_json(
                [{"role": "system", "content": "Classify the user's message to a SQL tutor. Intents:\n"
                  "generate_sql = asks a question about the data that needs a query (e.g. 'which customers ...', 'how many orders ...')\n"
                  "explain_sql = asks to explain or review a query\n"
                  "concept = asks about a SQL concept or syntax in general (e.g. 'what is a LEFT JOIN?')\n"
                  "hint = asks for help with the current exercise\n"
                  "other = anything else\n"
                  "Also set about_active_exercise to true only if the message asks for (part of) the answer to the "
                  "learner's current exercise." + exercise_line},
                 {"role": "user", "content": message}],
                schema={"type": "object", "properties": {"intent": {"type": "string", "enum": INTENTS},
                                                         "about_active_exercise": {"type": "boolean"}},
                        "required": ["intent", "about_active_exercise"]},
                model=self.pipeline_config.model, max_tokens=40)
            intent = out.get("intent", "other")
            about = bool(out.get("about_active_exercise")) and ctx.exercise is not None
        except LLMUnavailable:
            asks_for_data = re.search(r"\b(which|who|how many|list|show|find\w*|top|average|total|write|query|get)\b", low)
            intent = "generate_sql" if asks_for_data and not low.startswith(("what is", "what's", "explain")) else "concept"
            about = ctx.exercise is not None and _overlap(message, ctx.exercise.prompt) >= 0.3
        if intent == "explain_sql" and ctx.current_sql:
            return intent, ctx.current_sql, about
        return (intent if intent in INTENTS else "other"), "", about

    # ------------------------------------------------------------------ fact gathering

    def analyze_sql(self, sql: str, db_path: Path) -> TutorTurn:
        tables = describe(db_path)
        turn = TutorTurn("explain_sql", sql=sql)
        try:
            result = run_query(db_path, sql, max_rows=50)
            turn.result = result.to_dict()
            findings = analyze_query(sql, tables, db_path)
            turn.stages = trace_query(sql, db_path)
            turn.facts["execution"] = f"ran successfully, {result.row_count}{'+' if result.truncated else ''} row(s)"
            turn.facts["row_flow"] = [f"{s['clause']}: {s['row_count']} rows" for s in turn.stages if s["row_count"] is not None]
        except SandboxError as exc:
            findings = explain_error(str(exc), tables, sql) + [
                f for f in analyze_query(sql, tables, db_path) if f.id not in ("syntax", "clause-order", "trailing-comma")]
            turn.facts["execution"] = f"failed: {exc}"
        turn.findings = [f.to_dict() for f in findings]
        turn.facts["findings"] = [f"[{f.severity}] {f.title}: {f.message}" for f in findings] or ["no problems detected"]
        return turn

    def generate(self, question: str, ctx: TutorContext) -> TutorTurn:
        pipeline = Text2SQL(self.pipeline_config, client=self.client, examples=self.examples)
        try:
            res = pipeline.run(question, ctx.db_path)
        except LLMUnavailable as exc:
            return TutorTurn("generate_sql", note=str(exc), facts={"generation_error": str(exc)})
        turn = self.analyze_sql(res.sql, ctx.db_path) if res.sql else TutorTurn("generate_sql")
        turn.intent = "generate_sql"
        turn.pipeline_trace = res.trace_dicts()
        turn.facts["generated_sql"] = res.sql or "(generation failed)"
        if res.error:
            turn.facts["generation_error"] = res.error
        return turn

    def build_turn(self, message: str, ctx: TutorContext, hint_level: int = 0) -> TutorTurn:
        intent, sql, about_exercise = self.classify(message, ctx)

        if intent == "generate_sql" and about_exercise:
            # answer-leak guard: don't let the chat solve the active exercise
            turn = TutorTurn("hint", note="An exercise is active, so I'll guide you instead of writing the answer.")
            intent = "hint"
        elif intent == "generate_sql":
            return self.generate(message, ctx)
        elif intent == "explain_sql" and sql:
            return self.analyze_sql(sql, ctx.db_path)
        else:
            turn = TutorTurn(intent)

        if intent == "hint" and ctx.exercise is not None:
            level = max(1, min(hint_level or 1, 4))
            if ctx.current_sql.strip():
                analyzed = self.analyze_sql(ctx.current_sql, ctx.db_path)
                analyzed.note = turn.note
                turn = analyzed
                turn.facts["compared_to_expected"] = self._diff_summary(ctx)
            turn.intent = "hint"
            turn.facts["hint_level"] = level
            turn.facts["reference_hint"] = ctx.exercise.hints[min(level, 3) - 1] if level <= 3 else ""
            if level >= 4:
                turn.facts["reveal_solution"] = True
                turn.facts["solution"] = ctx.exercise.solution
        return turn

    @staticmethod
    def _diff_summary(ctx: TutorContext) -> str:
        """How the learner's result differs from the (hidden) reference - without revealing it."""
        try:
            expected = run_query(ctx.db_path, ctx.exercise.solution, max_rows=10_000)
            actual = run_query(ctx.db_path, ctx.current_sql, max_rows=10_000)
        except SandboxError:
            return "not comparable (the learner's query does not run)"
        diff = diff_results(expected.rows, actual.rows, len(expected.columns), len(actual.columns),
                            has_top_level_order_by(ctx.exercise.solution))
        return f"{diff.summary} (learner: {diff.actual_rows} rows, expected: {diff.expected_rows} rows)"

    # ------------------------------------------------------------------ response

    def messages(self, message: str, ctx: TutorContext, turn: TutorTurn) -> list[dict[str, str]]:
        tables = describe(ctx.db_path)
        facts = dict(turn.facts)
        facts.setdefault("reveal_solution", False)
        context = [f"DATABASE SCHEMA:\n{schema_prompt(tables, with_samples=False)}"]
        if ctx.exercise:
            context.append(f"ACTIVE EXERCISE: {ctx.exercise.title} - {ctx.exercise.prompt}")
        if ctx.current_sql and turn.intent == "hint":
            context.append(f"LEARNER'S CURRENT SQL:\n{ctx.current_sql}")
        if turn.sql:
            context.append(f"SQL UNDER DISCUSSION:\n{turn.sql}")
        context.append("FACTS:\n" + json.dumps(facts, indent=1, default=str))
        task = {
            "generate_sql": "Show the generated SQL, then explain step by step how it answers the question, "
                            "following the row flow in FACTS. End by inviting the learner to modify it.",
            "explain_sql": "Explain what the query does clause by clause in logical execution order, using the row "
                           "flow in FACTS. Then discuss each finding in FACTS (if any) and how to fix it.",
            "hint": "Give ONE hint at the requested hint_level (1 = gentle nudge question, 2 = name the concept, "
                    "3 = partial query skeleton, 4 = reveal and explain the solution). Base it on reference_hint "
                    "and on the learner's current SQL and findings.",
            "concept": "Explain the concept with a tiny example that uses this database's tables.",
            "other": "Reply briefly and steer the learner back to learning SQL with this database.",
        }[turn.intent]
        system = SYSTEM.format(style=LEVEL_STYLE.get(ctx.level, LEVEL_STYLE["beginner"]))
        history = ctx.history[-6:]
        user = "\n\n".join(context) + f"\n\nTASK: {task}\n\nLEARNER MESSAGE: {message}"
        return [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]

    def stream_reply(self, message: str, ctx: TutorContext, turn: TutorTurn) -> Iterator[str]:
        try:
            yield from self.client.stream(self.messages(message, ctx, turn))
        except LLMUnavailable:
            yield fallback_text(turn)


_STOPWORDS = {"the", "a", "an", "of", "and", "or", "who", "which", "that", "in", "on", "for", "to", "is", "are", "with",
              "find", "show", "list", "all", "each", "their", "have", "has", "me", "write", "query", "sql"}


def _overlap(a: str, b: str) -> float:
    """Share of the exercise's content words that also appear in the message."""
    def stems(text: str) -> set[str]:
        out = set()
        for w in re.findall(r"[a-z]+", text.lower()):
            if w in _STOPWORDS or len(w) < 3:
                continue
            for suffix in ("ing", "ed", "es", "s"):  # crude stemming: "ordered" ~ "order"
                if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                    w = w[: -len(suffix)]
                    break
            out.add(w)
        return out

    wa, wb = stems(a), stems(b)
    return len(wa & wb) / len(wb) if wb else 0.0


def fallback_text(turn: TutorTurn) -> str:
    """Template reply used when no local LLM is running - the deterministic facts still teach."""
    parts = ["_(The local AI model is offline, so this is the analyzer's direct feedback.)_\n"]
    if turn.sql:
        parts.append(f"```sql\n{turn.sql}\n```")
    for s in turn.stages:
        if s.get("row_count") is not None:
            parts.append(f"- **{s['clause']}** - {s['explanation']} -> {s['row_count']} rows")
    for f in turn.findings:
        parts.append(f"\n**{f['title']}** ({f['severity']}): {f['message']}")
    if hint := turn.facts.get("reference_hint"):
        parts.append(f"\n**Hint:** {hint}")
    if turn.facts.get("reveal_solution"):
        parts.append(f"\n**Solution:**\n```sql\n{turn.facts['solution']}\n```")
    if len(parts) == 1:
        parts.append("Start the model with `ollama serve` for full explanations.")
    return "\n".join(parts)
