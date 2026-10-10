"""Capacity plan Phase 2 (and R2): lemma bank (K1), interactive prover (K2), plausible kill tests (K3), Mathlib index (K4),
best-of-N (K5), recursive decomposition (K6), budget-driven rounds (R2) and capability profiles (S2). Fake Lean only."""

import json

from quaera import prompts
from quaera.bank import LemmaBank, hint_block, rename, statement_key
from quaera.capability import ModelStats, adapt
from quaera.mathlib_index import MathlibIndex, query_terms
from quaera.prover import AUTOMATION, Interactive, ProofResult, prove_interactive, prove_search

from test_discovery import L2_FALSE, LemmaTools, make_discovery
from test_prover import STMT, Asker, fake_check

GOOD = "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  GOOD\n```"
BAD = "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  BAD\n```"


# --- K1 lemma bank --------------------------------------------------------------------------------------

def test_bank_matches_statements_regardless_of_name(tmp_path):
    bank = LemmaBank(tmp_path / "bank.db")
    src = "import Mathlib\n\ntheorem quaera_L1 (n : ℕ) : n + 0 = n := by\n  simp\n"
    assert bank.add(name="quaera_L1", lean="theorem quaera_L1 (n : ℕ) : n + 0 = n", statement="adding zero",
                    source=src, project="p1", rev="r1")
    assert not bank.add(name="x", lean="theorem other (n : ℕ) : n + 0 = n", statement="dup", source=src, project="p2", rev="r1")
    assert statement_key("lemma foo (n : ℕ) : n + 0 = n") == statement_key("theorem quaera_L7 (n : ℕ) : n + 0 = n")
    hit = bank.exact("theorem quaera_L5 (n : ℕ) : n + 0 = n", "r1")
    assert hit and hit["project"] == "p1" and bank.exact("theorem quaera_L5 (n : ℕ) : n + 0 = n", "r2") is None
    assert "quaera_L5 (n" in rename(hit["source"], hit["name"], "quaera_L5")
    found = bank.search("adding zero to a natural number", rev="r1")
    assert found and "quaera_bank_" in hint_block(found) and "quaera_L1" not in hint_block(found)


def test_discovery_reuses_banked_lemmas_without_a_model_call(tmp_path):
    bank = LemmaBank(tmp_path / "bank.db")
    first, _, _ = make_discovery(tmp_path, "first")
    first.bank = bank
    first.run()
    assert bank.stats()["lemmas"] >= 2                                   # L1 and the repaired L2 (after reverification)
    second, a, b = make_discovery(tmp_path, "second")
    second.bank = bank
    second.run()
    reused = second.store.events("bank.reused")
    assert {e["payload"]["id"] for e in reused} >= {"L1"}
    l1 = [e["payload"] for e in second.store.events("lemma.status") if e["payload"]["id"] == "L1"]
    assert l1[0]["status"] == "verified" and l1[0]["family"] == "bank"
    assert bank.stats()["reused"] >= 1
    assert "Reused from the lab's lemma bank" in (tmp_path / "second" / "report.md").read_text(encoding="utf-8")


# --- K3 plausible ---------------------------------------------------------------------------------------

class PlausibleTools(LemmaTools):
    def call(self, role, tool, **args):
        if tool == "lean.compile" and "plausible" in args["source"] and "n ^ 2 = n" in args["source"]:
            self.calls.append((role, tool))
            return json.dumps({"compiled": False, "verified": False, "theorem": args.get("theorem"), "axioms": [], "problems": [],
                               "errors": ["line 3: Found a counter-example!\nn := 2"], "warnings": [], "output": "",
                               "timed_out": False, "mode": "fake", "seconds": 0.0})
        return super().call(role, tool, **args)


def test_plausible_counterexample_refutes_a_lemma_before_any_proof_attempt(tmp_path):
    orch, _, _ = make_discovery(tmp_path, "pl")
    orch.tools = PlausibleTools(orch.permissions)
    orch.run()
    l2 = [e["payload"] for e in orch.store.events("lemma.status") if e["payload"]["id"] == "L2"]
    assert l2[0]["status"] == "numeric" and "plausible" in l2[0]["detail"]
    assert l2[1]["status"] == "refuted" and "plausible" in l2[1]["detail"]
    assert L2_FALSE


# --- K2 interactive prover ------------------------------------------------------------------------------

def test_interactive_prover_walks_goal_states_and_backtracks():
    states = {0: ["⊢ P n"], 1: ["case zero ⊢ P 0", "case succ ⊢ P (n+1)"]}

    def tactic(state, t):
        if t == AUTOMATION:
            return {"proofState": None, "goals": [], "error": "automation failed"}
        if state == 0 and t == "induction n":
            return {"proofState": 1, "goals": states[1], "error": None}
        if state == 1 and t == "all_goals GOOD":
            return {"proofState": 2, "goals": [], "error": None}
        return {"proofState": None, "goals": [], "error": f"unknown tactic {t}"}
    inter = Interactive(goals=lambda src: [{"proofState": 0, "goal": "⊢ P n", "line": 4}], tactic=tactic)
    replies = iter(['{"tactics": ["simp", "induction n"]}', '{"tactics": ["sorry", "all_goals GOOD"]}'])
    ask = lambda system, prompt, n: next(replies)  # noqa: E731
    res = ProofResult(False, None, 0)
    src = prove_interactive(STMT, "main", ask, fake_check, inter, "", res, lambda *a, **k: None)
    assert src and "induction n" in src and "all_goals GOOD" in src and "sorry" not in src
    assert res.log[-1]["kind"] == "interactive" and res.log[-1]["verified"]


def test_interactive_rung_is_skipped_when_the_repl_is_missing():
    def broken(src):
        raise RuntimeError("no Lean here")
    r = prove_search(STMT, "main", Asker([BAD, BAD, "no sketch", "no sketch"]), fake_check, attempts=2,
                     interactive=Interactive(goals=broken, tactic=lambda s, t: {}))
    assert not r.solved and any(e["kind"] == "interactive" and "REPL unavailable" in e["errors"][0] for e in r.log)


# --- R2 rounds, K5 samples, K6 recursion --------------------------------------------------------------------

def test_search_keeps_going_while_the_budget_share_remains():
    replies = [BAD, BAD, "no sketch", "no sketch", BAD, GOOD]
    r = prove_search(STMT, "main", Asker(replies), fake_check, attempts=2, rounds=3, more=lambda: True)
    assert r.solved and any(e["kind"] == "round" and e["round"] == 2 for e in r.log)
    stopped = prove_search(STMT, "main", Asker(replies[:4]), fake_check, attempts=2, rounds=3, more=lambda: False)
    assert not stopped.solved and not any(e["kind"] == "round" for e in stopped.log)


def test_best_of_n_candidates_run_in_parallel():
    lanes = []

    def ask(system, prompt, n, lane=None):
        lanes.append(lane)
        return GOOD if lane == "D" else BAD
    r = prove_search(STMT, "main", ask, fake_check, attempts=1, samples=4, sketches=0)
    assert r.solved and sorted(lanes) == ["A", "B", "C", "D"] and r.log[-1]["lane"] == "D"


def test_a_stubborn_lemma_is_decomposed_one_level_deeper():
    outer = ("```lean\nimport Mathlib\n\nlemma quaera_step_1 (n : ℕ) : Q n := by sorry\n\n"
             "theorem main (n : ℕ) : P n := by\n  use_lemmas\n```")
    lemma_bad = "```lean\nimport Mathlib\n\nlemma quaera_step_1 (n : ℕ) : Q n := by\n  BAD\n\ntheorem main (n : ℕ) : P n := by\n  use_lemmas\n```"
    inner = ("```lean\nimport Mathlib\n\nlemma quaera_d1_step_1 (n : ℕ) : EASY n := by sorry\n\n"
             "lemma quaera_step_1 (n : ℕ) : Q n := by\n  GOOD\n\ntheorem main (n : ℕ) : P n := by\n  use_lemmas\n```")
    ask = Asker([BAD, BAD, outer, lemma_bad, lemma_bad, lemma_bad, inner])
    r = prove_search(STMT, "main", ask, fake_check, attempts=2, sketches=1, depth=2)
    assert r.solved, r.log
    assert any(e.get("depth") == 1 and e["kind"] == "sketch" and e.get("parent") == "quaera_step_1" for e in r.log)
    assert "quaera_d1_step_1" in r.source and "sorry" not in r.source
    assert prompts.SKETCH.replace("`quaera_main`", "`quaera_step_1`").replace("quaera_step_", "quaera_d1_step_") in ask.calls


# --- K4 Mathlib index -----------------------------------------------------------------------------------

def test_mathlib_index_ranks_names_and_notation(tmp_path):
    root = tmp_path / "Mathlib"
    (root / "Algebra").mkdir(parents=True)
    (root / "Algebra" / "Sum.lean").write_text(
        "/-- Sum over `range (n+1)` splits off the last term. -/\n"
        "theorem Finset.sum_range_succ (f : ℕ → M) (n : ℕ) : ∑ x ∈ range (n + 1), f x = ∑ x ∈ range n, f x + f n := by\n  simp\n\n"
        "theorem Nat.le_of_dvd {m n : ℕ} (h1 : 0 < n) : m ∣ n → m ≤ n := by\n  omega\n\n"
        "def Nat.factorial : ℕ → ℕ\n  | 0 => 1\n  | n + 1 => (n + 1) * n.factorial\n", encoding="utf-8")
    idx = MathlibIndex(tmp_path / "idx.db", root)
    assert idx.build() == 3
    assert idx.search("sum range succ")[0]["name"] == "Finset.sum_range_succ"
    assert idx.search("m ∣ n → m ≤ n")[0]["name"] == "Nat.le_of_dvd"            # notation matched by name
    assert "dvd" in query_terms("a ∣ b") and "sum" in query_terms("∑ i ∈ s, f i")


# --- S2 capability profiles ------------------------------------------------------------------------------

def test_capability_profile_adapts_the_search(tmp_path):
    stats = ModelStats(tmp_path / "stats.db")
    for _ in range(9):
        stats.bump("local/qwen", "PROVE", "compile_bad")
    stats.bump("local/qwen", "PROVE", "compile_ok")
    stats.bump("local/qwen", "HYPOTHESIS", "json_bad", 4)
    stats.bump("local/qwen", "HYPOTHESIS", "json_ok", 2)
    prof = stats.profile("local/qwen")
    assert prof["compile_rate"] == 0.1 and prof["json_reliability"] == 0.333
    settings, why = adapt(prof)
    assert settings["interactive_first"] and settings["samples"] == 4 and settings["depth"] == 3 and len(why) == 2
    assert adapt(stats.profile("unknown/model")) == (adapt(None)[0], [])
    assert [p["model"] for p in stats.board()] == ["local/qwen"]
