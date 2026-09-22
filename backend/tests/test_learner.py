from __future__ import annotations

from app.catalog import resolve_db
from app.sandbox import run_query
from app.tutor.exercises import BY_ID, EXERCISES
from app.tutor.learner import PARAMS, LearnerStore, bkt_update, infer_level, recommend
from app.tutor.skills import SKILLS, topo_order


def test_every_exercise_solution_runs_and_uses_known_skills():
    db = resolve_db("shop")
    for ex in EXERCISES:
        assert set(ex.skills) <= set(SKILLS), ex.id
        assert run_query(db, ex.solution, max_rows=5000).row_count > 0, ex.id


def test_skill_graph_is_acyclic_and_complete():
    assert sorted(topo_order()) == sorted(SKILLS)


def test_bkt_moves_in_the_right_direction():
    p = PARAMS.p_init
    assert bkt_update(p, True) > p
    assert bkt_update(p, False) < bkt_update(p, True)
    for _ in range(8):
        p = bkt_update(p, True)
    assert p > 0.9


def test_hint_heavy_success_counts_as_weak_evidence(tmp_path):
    store = LearnerStore(tmp_path / "a.sqlite")
    ex = BY_ID["where-1"]
    clean = store.record_attempt("alice", ex, ex.solution, True, 0, [])["where"][1]
    helped = store.record_attempt("bob", ex, ex.solution, True, 3, [])["where"][1]
    assert clean > helped


def test_recommendation_starts_with_basics_and_progresses(tmp_path):
    store = LearnerStore(tmp_path / "b.sqlite")
    first, _ = recommend(store.mastery("carol"), set())
    assert first is not None and first.skills == ("select",)
    assert infer_level(store.mastery("carol")) == "beginner"

    # a learner who aces the basics gets unlocked into joins/aggregation
    for ex_id in ["sel-1", "sel-2", "where-1", "where-2", "where-3", "null-1", "agg-2"]:
        ex = BY_ID[ex_id]
        for _ in range(3):
            store.record_attempt("carol", ex, ex.solution, True, 0, [])
    nxt, reason = recommend(store.mastery("carol"), store.solved("carol"))
    assert nxt is not None and not set(nxt.skills) <= {"select", "where"}, (nxt.id, reason)
