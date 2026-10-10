"""Capacity plan against real Lean 4 + Mathlib: REPL goal states and tactic steps (K2), plausible kill tests (K3),
the heavy automation rung (K3), the interactive prover end to end and the Mathlib index (K4). Skipped without Lean.

Run: uv run pytest -m lean tests/test_lean_capacity.py
"""

import pytest

from quaera.lean import LeanChecker, LeanUnavailable
from quaera.prover import AUTOMATION_HEAVY, Interactive, ProofResult, prove_interactive, with_proof

pytestmark = pytest.mark.lean
STMT = "import Mathlib\n\ntheorem quaera_main (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2 := by\n  sorry\n"


@pytest.fixture(scope="module")
def lean():
    try:
        checker = LeanChecker()
    except LeanUnavailable as exc:
        pytest.skip(str(exc))
    yield checker
    checker.close()


def test_goal_states_and_tactic_steps(lean):
    goals = lean.goals(STMT)
    assert len(goals) == 1 and "n ^ 2" in goals[0]["goal"] and goals[0]["line"] == 4
    step = lean.tactic(goals[0]["proofState"], "induction n")
    assert step["error"] is None and len(step["goals"]) == 2
    bad = lean.tactic(goals[0]["proofState"], "exact foo_bar_baz")
    assert bad["error"] and bad["goals"] == []
    arms = lean.tactic(goals[0]["proofState"], "induction n with\n| zero => simp\n| succ n ih => skip")
    assert arms["error"] and "unsolved goals" in arms["error"]          # an unfinished arm is an error, not an open goal
    one = lean.tactic(step["proofState"], "· simp")
    assert one["error"] is None and len(one["goals"]) == 1
    named = lean.tactic(one["proofState"], "rename_i k ih")
    done = lean.tactic(named["proofState"], "rw [Finset.sum_range_succ, ih]; ring")
    assert done["error"] is None and done["goals"] == []


def test_forbidden_commands_never_reach_the_repl(lean):
    assert lean.goals("import Mathlib\n#eval 1\ntheorem t : True := by sorry\n") == []


def test_plausible_finds_a_counterexample(lean):
    src = with_proof("import Mathlib\n\ntheorem quaera_L2 (n : ℕ) : n ^ 2 = n := by\n  sorry\n", "quaera_L2", "plausible")
    r = lean.check(src, "quaera_L2")
    assert not r.verified and any("ounter" in e for e in r.errors), r.errors


def test_heavy_automation_closes_a_library_fact(lean):
    src = with_proof("import Mathlib\n\ntheorem quaera_main (a b : ℕ) : a + b = b + a := by\n  sorry\n", "quaera_main", AUTOMATION_HEAVY)
    r = lean.check(src, "quaera_main", "theorem quaera_main (a b : ℕ) : a + b = b + a")
    assert r.verified, (r.errors, r.problems)


def test_interactive_prover_end_to_end(lean):
    replies = iter(['{"tactics": ["induction n"]}', '{"tactics": ["· simp"]}', '{"tactics": ["rename_i k ih"]}',
                    '{"tactics": ["rw [Finset.sum_range_succ, ih]; ring"]}'])
    inter = Interactive(goals=lean.goals, tactic=lean.tactic)
    approved = "theorem quaera_main (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2"
    check = lambda src, name=None, ap=None: lean.check(src, name or "quaera_main", ap or approved).__dict__  # noqa: E731
    res = ProofResult(False, None, 0)
    src = prove_interactive(STMT, "quaera_main", lambda s, p, n: next(replies), check, inter, "", res, lambda *a, **k: None)
    assert src and check(src)["verified"]


def test_mathlib_index_on_the_real_library(tmp_path):
    from quaera.lean import DEFAULT_WORKSPACE
    from quaera.mathlib_index import MathlibIndex
    root = DEFAULT_WORKSPACE / ".lake" / "packages" / "mathlib" / "Mathlib"
    if not root.exists():
        pytest.skip("Mathlib sources are not available")
    idx = MathlibIndex(tmp_path / "m.db", root / "Algebra" / "BigOperators")
    assert idx.build() > 100
    names = [h["name"] for h in idx.search("sum range succ", 10)]
    assert any("sum_range_succ" in n for n in names), names
