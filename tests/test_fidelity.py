"""Formalization fidelity: signature parsing, negation and vacuity files (no Lean needed)."""

from quaera.fidelity import as_prop, refutation_file, split_theorem, vacuity_file

SRC = "import Mathlib\n\nopen Real\n\ntheorem quaera_main (n : ℕ) (h : 0 < n) : 6 ∣ n ^ 3 - n := by\n  sorry\n"


def test_split_and_negate():
    header, binders, concl = split_theorem(SRC, "quaera_main")
    assert header.endswith("open Real\n\n") and binders == "(n : ℕ) (h : 0 < n)" and concl == "6 ∣ n ^ 3 - n"
    f, stmt = refutation_file(SRC, "quaera_main")
    assert stmt == "theorem quaera_refute : ¬ (∀ (n : ℕ) (h : 0 < n), 6 ∣ n ^ 3 - n)"
    assert f.startswith("import Mathlib\n\nopen Real\n\ntheorem quaera_refute") and "push_neg" in f


def test_no_binders_and_nested_types():
    src = "import Mathlib\ntheorem quaera_main : ∀ f : ℕ → ℕ, (∀ x, f (f x) = x) → Function.Injective f := by sorry"
    assert split_theorem(src, "quaera_main")[1:] == ("", "∀ f : ℕ → ℕ, (∀ x, f (f x) = x) → Function.Injective f")
    assert vacuity_file(src, "quaera_main") is None                       # no binders: no vacuity question
    src2 = "import Mathlib\ntheorem quaera_main {α : Type} [Fintype α] (s : Finset (α × α)) : s.card ≤ Fintype.card α ^ 2 := by sorry"
    b = split_theorem(src2, "quaera_main")[1]
    assert b == "{α : Type} [Fintype α] (s : Finset (α × α))"
    assert as_prop(b, "True") == "∀ (α : Type) (_inst : Fintype α) (s : Finset (α × α)), True"
    assert vacuity_file(src2, "quaera_main")[1] == f"theorem quaera_vacuous {b} : False"


def test_unparseable_returns_none():
    assert split_theorem("import Mathlib\ntheorem quaera_main (n : ℕ := by sorry", "quaera_main") is None
    assert refutation_file("import Mathlib\nlemma other : True := trivial", "quaera_main") is None


# --- inside the orchestrator (with a fake Lean) --------------------------------------------------------
import json  # noqa: E402

from quaera import prompts  # noqa: E402

from test_orchestrator import FakeTools, Script, make  # noqa: E402


class FidelityTools(FakeTools):
    """Counts the given helper theorem (quaera_vacuous / quaera_refute) as 'verified'."""

    def __init__(self, permissions, accept: str):
        super().__init__(permissions)
        self.accept = accept

    def call(self, role, tool, **args):
        if tool == "lean.compile" and args.get("theorem") == self.accept:
            self.permissions.check_tool(role, tool)
            return json.dumps({"compiled": True, "verified": True, "theorem": self.accept, "errors": [], "warnings": [],
                               "axioms": ["propext"], "problems": [], "output": "ok", "timed_out": False, "mode": "fake", "seconds": 0.0})
        return super().call(role, tool, **args)


def test_vacuous_statement_stops_with_blocking_critique(tmp_path):
    orch = make(tmp_path, Script(critic_objects_first=False))
    orch.tools = FidelityTools(orch.permissions, "quaera_vacuous")
    orch.run()
    assert "vacuously true" in str(orch.state("stopped"))
    assert any(c["severity"] == "blocking" and "False" in c["body"] for c in orch.store.latest("critique"))
    assert orch.store.check_final() == []


def test_refuted_statement_is_reported_as_refuted_with_lean_check(tmp_path):
    orch = make(tmp_path, Script(critic_objects_first=False))
    orch.tools = FidelityTools(orch.permissions, "quaera_refute")
    report = open(orch.run(), encoding="utf-8").read()
    h = orch.store.get(orch.state("hypothesis_id"))
    assert h["status"] == "refuted" and orch.state("proof") is None
    assert orch.store.latest("verification")[-1]["reproduced"] == "yes"
    assert "Hypothesis refuted" in report and "negation" in report
    assert orch.store.check_final() == []


def test_critic_sees_blind_backtranslation(tmp_path):
    script, seen = Script(critic_objects_first=False), []

    def spy(system, prompt):
        if system == prompts.CRITIC_STATEMENT:
            seen.append(prompt)
        return script(system, prompt)
    orch = make(tmp_path, spy)
    orch.run()
    assert seen and "Independent back-translation" in seen[0] and "n·n" in seen[0]
    assert orch.store.events("statement.backtranslated")
