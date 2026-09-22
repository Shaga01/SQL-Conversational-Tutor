"""Learner model: Bayesian Knowledge Tracing (Corbett & Anderson, 1994) + adaptive sequencing.

For every skill we keep P(known). After each graded attempt:

    posterior = P(known | observation)            (Bayes' rule with guess / slip)
    P(known)' = posterior + (1 - posterior) * T    (chance to learn from the practice)

Following common ITS practice (e.g. ASSISTments), an answer reached only after
several hints counts as an incorrect observation for mastery purposes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from .exercises import EXERCISES, Exercise
from .skills import SKILLS, topo_order


@dataclass(frozen=True)
class BKTParams:
    p_init: float = 0.15
    p_learn: float = 0.12
    p_guess: float = 0.20
    p_slip: float = 0.10


PARAMS = BKTParams()
MASTERED = 0.90
UNLOCK = 0.55


def bkt_update(p: float, correct: bool, params: BKTParams = PARAMS) -> float:
    if correct:
        num = p * (1 - params.p_slip)
        den = num + (1 - p) * params.p_guess
    else:
        num = p * params.p_slip
        den = num + (1 - p) * (1 - params.p_guess)
    posterior = num / den if den else p
    return posterior + (1 - posterior) * params.p_learn


class LearnerStore:
    def __init__(self, path: Path | None = None) -> None:
        path = path or settings.data_dir / "app.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS mastery (
                learner_id TEXT NOT NULL, skill_id TEXT NOT NULL, p_known REAL NOT NULL, n_obs INTEGER NOT NULL,
                PRIMARY KEY (learner_id, skill_id));
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, learner_id TEXT NOT NULL, exercise_id TEXT NOT NULL, sql TEXT NOT NULL,
                correct INTEGER NOT NULL, hints_used INTEGER NOT NULL, findings TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_by_learner ON attempts (learner_id, created_at);
        """)

    # ----------------------------------------------------------------- reads

    def mastery(self, learner_id: str) -> dict[str, float]:
        with self._lock:
            rows = dict(self._conn.execute("SELECT skill_id, p_known FROM mastery WHERE learner_id = ?", (learner_id,)).fetchall())
        return {sid: rows.get(sid, PARAMS.p_init) for sid in SKILLS}

    def solved(self, learner_id: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute(
                "SELECT DISTINCT exercise_id FROM attempts WHERE learner_id = ? AND correct = 1", (learner_id,))}

    def misconception_counts(self, learner_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            rows = self._conn.execute("SELECT findings FROM attempts WHERE learner_id = ?", (learner_id,)).fetchall()
        for (raw,) in rows:
            for f in json.loads(raw):
                if f.get("severity") in ("error", "warning"):
                    counts[f["id"]] = counts.get(f["id"], 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def history(self, learner_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT exercise_id, correct, hints_used, created_at FROM attempts WHERE learner_id = ? "
                "ORDER BY created_at DESC LIMIT ?", (learner_id, limit)).fetchall()
        return [{"exercise_id": e, "correct": bool(c), "hints_used": h, "at": t} for e, c, h, t in rows]

    # ----------------------------------------------------------------- writes

    def record_attempt(self, learner_id: str, exercise: Exercise, sql: str, correct: bool, hints_used: int,
                       findings: list[dict]) -> dict[str, tuple[float, float]]:
        """Store the attempt and update mastery. Returns {skill: (before, after)}."""
        observed_correct = correct and hints_used < 2
        evidence = {sid: observed_correct for sid in exercise.skills}
        # a detected error-level misconception is extra evidence against its skill
        for f in findings:
            if f.get("severity") == "error" and f.get("skill") in SKILLS and f["skill"] not in evidence:
                evidence[f["skill"]] = False

        current = self.mastery(learner_id)
        changes = {}
        with self._lock:
            for sid, obs in evidence.items():
                before = current[sid]
                after = bkt_update(before, obs)
                changes[sid] = (round(before, 3), round(after, 3))
                self._conn.execute(
                    "INSERT INTO mastery VALUES (?, ?, ?, 1) ON CONFLICT (learner_id, skill_id) "
                    "DO UPDATE SET p_known = excluded.p_known, n_obs = n_obs + 1", (learner_id, sid, after))
            self._conn.execute(
                "INSERT INTO attempts (learner_id, exercise_id, sql, correct, hints_used, findings, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (learner_id, exercise.id, sql, int(correct), hints_used, json.dumps(findings), time.time()))
            self._conn.commit()
        return changes

    def reset(self, learner_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM mastery WHERE learner_id = ?", (learner_id,))
            self._conn.execute("DELETE FROM attempts WHERE learner_id = ?", (learner_id,))
            self._conn.commit()


# --------------------------------------------------------------------- policy

def infer_level(mastery: dict[str, float]) -> str:
    core = ["select", "where", "aggregate", "group_by", "inner_join"]
    avg_core = sum(mastery[s] for s in core) / len(core)
    advanced = ["outer_join", "subquery", "cte", "window"]
    avg_adv = sum(mastery[s] for s in advanced) / len(advanced)
    if avg_core < 0.5:
        return "beginner"
    if avg_adv < 0.6:
        return "intermediate"
    return "advanced"


def unlocked(mastery: dict[str, float]) -> list[str]:
    return [s for s in topo_order() if all(mastery[p] >= UNLOCK for p in SKILLS[s].prereqs)]


def recommend(mastery: dict[str, float], solved: set[str]) -> tuple[Exercise | None, str]:
    """Pick the exercise in the learner's zone of proximal development."""
    open_skills = set(unlocked(mastery))
    best, best_score, reason = None, float("-inf"), ""
    for ex in EXERCISES:
        if ex.id in solved or not set(ex.skills) <= open_skills:
            continue
        focus = min(ex.skills, key=lambda s: mastery[s])
        if mastery[focus] >= MASTERED:
            continue
        avg = sum(mastery[s] for s in ex.skills) / len(ex.skills)
        desired_difficulty = 1 + 4 * avg
        score = -abs(ex.difficulty - desired_difficulty) - 2 * mastery[focus] - 0.3 * topo_order().index(focus) / len(SKILLS)
        if score > best_score:
            best, best_score = ex, score
            reason = f"Practises {SKILLS[focus].name} (your estimated mastery: {mastery[focus]:.0%})."
    if best is None:
        remaining = [e for e in EXERCISES if e.id not in solved]
        if remaining:
            return remaining[0], "Keep going - more practice."
        return None, "You have solved every exercise!"
    return best, reason
