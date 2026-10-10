"""Behaviour tests for the orchestrator: a scripted provider and fake tools instead of a real model and Lean.

End-to-end test with real Lean: tests/test_lean.py (marker: lean).
"""

import json
import re

import pytest

from quaera import prompts
from quaera.gateway import Gateway, ScriptedProvider
from quaera.lean import normalize, statement_of
from quaera.orchestrator import AutoApprover, Orchestrator
from quaera.permissions import Permissions, PermissionDenied
from quaera.store import IntegrityError, Store

STATEMENT = "import Mathlib\n\ntheorem quaera_main (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2 := by sorry\n"
WRONG_STATEMENT = "import Mathlib\n\ntheorem quaera_main (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n * n + 1 := by sorry\n"
PROOF = STATEMENT.replace("by sorry", "by\n  induction n with\n  | zero => simp\n  | succ n ih => rw [Finset.sum_range_succ, ih]; ring")


class FakeTools:
    """Stands in for ToolRegistry: permission checks are real, tool results are fake."""

    def __init__(self, permissions):
        self.permissions = permissions
        self.calls = []

    def call(self, role, tool, **args):
        self.permissions.check_tool(role, tool)
        self.calls.append((role, tool))
        if tool == "arxiv.search":
            return json.dumps([{"id": "arXiv:1111.11111", "title": "Odd sums", "year": "2011", "summary": "..."}])
        if tool == "mathlib.search":
            return json.dumps([{"file": "Algebra/BigOperators.lean", "line": 1, "text": "theorem Finset.sum_range_succ"}])
        if tool == "openalex.search":
            return json.dumps([])
        if tool in ("arxiv.lookup", "crossref.lookup"):
            return "real"
        if tool == "sandbox.write":
            return json.dumps({"commit": "0000000"})
        if tool == "sandbox.exec":   # exploration script: "runs" the single line the Script wrote
            return json.dumps({"returncode": 0, "stdout": 'QUAERA_EXPLORE {"checked": "n ≤ 200", "counterexample": null, '
                                                         '"observations": ["the sum is always n*n"]}\n', "stderr": ""})
        if tool == "lean.compile":
            src, approved = args["source"], args.get("approved_statement")
            problems, errors = [], []
            if "sorry" in src:
                problems.append("Proof contains `sorry`.")
            if "BADPROOF" in src:
                errors.append("line 5: unsolved goals")
            if "first | " in src:     # automation tactic chains: the fake Lean does not count them as proofs (tested on real Lean)
                errors.append("line 3: automation failed (fake)")
            if approved and statement_of(src, "quaera_main") != normalize(approved):
                problems.append("The approved theorem statement was changed; it must be kept exactly.")
            compiled = not errors
            return json.dumps({"compiled": compiled, "verified": compiled and not problems, "theorem": "quaera_main",
                               "errors": errors, "warnings": [], "axioms": ["propext"], "problems": problems,
                               "output": "ok", "timed_out": False, "mode": "fake", "seconds": 0.0})
        raise AssertionError(tool)

    def call_json(self, role, tool, **args):
        return json.loads(self.call(role, tool, **args))

    def close(self):
        pass


class Script:
    """A script that answers based on the prompt. Counters track how many times each role was called."""

    def __init__(self, crash_on_prove=False, critic_objects_first=True):
        self.n = {}
        self.crash_on_prove = crash_on_prove
        self.critic_objects_first = critic_objects_first

    def __call__(self, system, prompt):
        keys = ("LITERATURE_PLAN", "LITERATURE_SUMMARY", "HYPOTHESIS", "FORMALIZE", "PROVE", "CRITIC_STATEMENT", "CRITIC_RESULT",
                "BACKTRANSLATE", "REFUTE", "EXPLORE_MATH", "RANK_HYPOTHESES", "SKETCH")
        key = next((k for k in keys if getattr(prompts, k) == system), None)
        if key is None:   # lemma proof prompt: the main proof prompt with the theorem name replaced by the lemma name
            norm = re.sub(r"`quaera_(?:d\d+_)?step_\d+`", "`quaera_main`", re.sub(r"quaera_d\d+_step_", "quaera_step_", system))
            key = next(k for k in keys if getattr(prompts, k) == norm)
        self.n[key] = self.n.get(key, 0) + 1
        i = self.n[key]
        if key == "LITERATURE_PLAN":
            return '{"arxiv_queries": ["sum odd numbers"], "openalex_queries": ["odd sum square"], "mathlib_queries": ["sum_range_succ"]}'
        if key == "LITERATURE_SUMMARY":
            return ('```json\n{"summary": "A known result.", "verdict": "already_done", '
                    '"cited": ["arXiv:1111.11111", "arXiv:2222.22222"], "relevant_mathlib": ["Finset.sum_range_succ"]}\n```')
        if key == "HYPOTHESIS":
            return json.dumps({"hypotheses": [
                {"statement": "For every n, the sum of the first n odd numbers is n².", "falsifiabilityNote": "An n that breaks the equality refutes the hypothesis."},
                {"statement": "The sum of the first n odd numbers is always odd.", "falsifiabilityNote": "Refuted because the sum is 4 for n=2."}]})
        if key == "FORMALIZE":
            wrong = self.critic_objects_first and i == 1
            return f"```lean\n{WRONG_STATEMENT if wrong else STATEMENT}```\nExplanation."
        if key == "CRITIC_STATEMENT":
            if "n * n + 1" in prompt:
                return '{"faithful": false, "issues": [{"severity": "high", "body": "The right-hand side became n²+1."}], "summary": "Does not match."}'
            return '{"faithful": true, "issues": [], "summary": "The statement is correct."}'
        if key == "PROVE":
            if self.crash_on_prove:
                self.crash_on_prove = False
                raise RuntimeError("simulated crash")
            return f"```lean\n{PROOF.replace('ring', 'BADPROOF') if i == 1 else PROOF}```"
        if key == "CRITIC_RESULT":
            return '{"concerns": []}'
        if key == "BACKTRANSLATE":
            return '{"translation": "For every natural number n, the sum of the first n odd numbers is n·n.", "oddities": []}'
        if key == "REFUTE":
            return "TRUE"
        if key == "SKETCH":   # single-lemma sketch; the lemma cannot be proved on the fake Lean (the proof changes the main theorem)
            return ("```lean\nimport Mathlib\n\nlemma quaera_step_1 (n : ℕ) : n * n = n ^ 2 := by sorry\n\n"
                    + STATEMENT.split("import Mathlib", 1)[1].replace("sorry", "BADPROOF").lstrip() + "```")
        if key == "RANK_HYPOTHESES":
            return json.dumps({"ranking": [
                {"index": 0, "testability": 9, "plausibility": 9, "novelty": 3, "scope": 10, "cost": 9, "reason": "Direct and provable."},
                {"index": 1, "testability": 9, "plausibility": 1, "novelty": 2, "scope": 3, "cost": 9, "reason": "Refuted immediately by n=2."}]})
        if key == "EXPLORE_MATH":
            return "```python\nprint('QUAERA_EXPLORE {\"checked\": \"n ≤ 200\", \"counterexample\": null, \"observations\": [\"the sum is always n*n\"]}')\n```"


def make(tmp_path, script, cap=5.0, family="scripted", cost=0.0, limit=100.0, name="p"):
    perms = Permissions.load()
    store = Store(tmp_path / name / "quaera.db", perms)
    store.set_meta("title", "Odd numbers")
    record = lambda k, p: store.append(k, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, p)  # noqa: E731
    gw = Gateway({family: ScriptedProvider(script, family, cost)}, cap, perms.agents, record)
    orch = Orchestrator(store, gw, FakeTools(perms), AutoApprover(limit), perms, log=lambda m: None, reports_dir=tmp_path / name)
    if not store.latest("question"):
        store.put({"type": "question", "createdBy": orch.human(), "title": "Is the sum of the first n odd numbers n²?",
                   "domain": "math", "scope": "Natural numbers"})
    return orch


def test_full_loop_supported(tmp_path):
    script = Script()
    orch = make(tmp_path, script)
    report = orch.run()
    store = orch.store
    h = store.get(orch.state("hypothesis_id"))
    assert h["status"] == "supported"
    assert store.check_final() == []
    # Objection → response → resolved
    crit = store.latest("critique")
    assert crit and crit[0]["status"] == "resolved"
    msgs = store.latest("message")
    objection = next(m for m in msgs if m["kind"] == "objection")
    assert any(m.get("inReplyTo") == objection["id"] for m in msgs)
    # Literature: a source missing from the search results was rejected
    assert [e["payload"]["ref"] for e in store.events("citation.rejected")] == ["arXiv:2222.22222"]
    # Pilot, failed and successful proof attempts, verification run
    kinds = [(r["kind"], r["status"]) for r in store.latest("run")]
    assert ("pilot", "succeeded") in kinds and ("full", "failed") in kinds and ("full", "succeeded") in kinds
    assert ("verification", "succeeded") in kinds
    assert "QuaeraLabs AI agent team" in open(report, encoding="utf-8").read()


def test_resume_after_crash_does_not_repeat_finished_stages(tmp_path):
    script = Script(crash_on_prove=True)
    orch = make(tmp_path, script)
    with pytest.raises(RuntimeError):
        orch.run()
    formalize_calls = script.n["FORMALIZE"]
    orch2 = make(tmp_path, script)  # same project file: reopened
    orch2.run()
    assert script.n["FORMALIZE"] == formalize_calls
    assert script.n["LITERATURE_PLAN"] == 1
    assert orch2.store.get(orch2.state("hypothesis_id"))["status"] == "supported"


def test_budget_cap_is_never_exceeded(tmp_path):
    orch = make(tmp_path, Script(), cap=0.05, family="anthropic", cost=0.009)
    orch.run()
    assert orch.gateway.spent_usd <= 0.05
    assert orch.state("stopped") and "budget" in orch.state("stopped")
    assert orch.store.events("budget.blocked")
    assert orch.state("report_path")


def test_approval_above_limit_stops_research(tmp_path):
    orch = make(tmp_path, Script(), cap=5.0, limit=0.5)
    orch.run()
    assert "did not approve the experiment" in orch.state("stopped")
    assert not orch.store.latest("run")


def test_branch_continues_independently(tmp_path):
    orch = make(tmp_path, Script())
    orch.run()
    seq = next(e["seq"] for e in orch.store.events("stage.done") if e["payload"]["stage"] == "formalize")
    branch = orch.store.branch(tmp_path / "branch" / "quaera.db", seq)
    assert "prove" not in [e["payload"]["stage"] for e in branch.events("stage.done")]
    orch_b = make(tmp_path, Script(critic_objects_first=False), name="branch")
    orch_b.run()
    assert orch_b.store.meta("branchOf")["atSeq"] == seq
    assert orch_b.store.get(orch_b.state("hypothesis_id"))["status"] == "supported"


def test_store_enforces_permissions_and_rules(tmp_path):
    orch = make(tmp_path, Script())
    store = orch.store
    eng = {"kind": "agent", "role": "engineer", "model": "x/y", "modelFamily": "x"}
    with pytest.raises(PermissionDenied):
        store.put({"type": "critique", "createdBy": eng, "targetId": "Q-0001", "category": "other",
                   "severity": "low", "body": "the engineer cannot write critiques", "status": "open"})
    with pytest.raises(PermissionDenied):
        orch.tools.call("engineer", "arxiv.search", query="x")
    with pytest.raises(IntegrityError):
        store.put({"type": "hypothesis", "createdBy": {**eng, "role": "hypothesis"}, "questionId": "Q-0001",
                   "statement": "Hypothesis accepted without approval", "falsifiabilityNote": "must be invalid", "status": "accepted"})
    with pytest.raises(Exception):
        store.db.execute("DELETE FROM events")


def test_model_error_retries_then_stops_with_report(tmp_path):
    from quaera.gateway import ModelError

    script = Script()

    def flaky(system, prompt):   # general model calls: controlled stop after two attempts (proof search is separate: test_prover)
        if system == prompts.FORMALIZE:
            raise ModelError("provider error", cost_usd=0.01)
        return script(system, prompt)

    orch = make(tmp_path, flaky, cap=5.0)
    orch.run()
    errors = orch.store.events("model.error")
    assert len(errors) == 2 and errors[0]["payload"]["costUsd"] == 0.01
    assert abs(orch.gateway.spent_usd - 0.02) < 1e-9
    assert "after 2 attempts" in orch.state("stopped")
    assert orch.state("report_path")
    assert orch.store.check_final() == []


def test_broken_cross_family_critic_falls_back_instead_of_stopping(tmp_path):
    """A second family that answers without JSON (the agy incident) must not stop the research."""
    class Family(ScriptedProvider):
        model_for = lambda self, profile: self.family  # noqa: E731
        price = lambda self, model: (0.0, 0.0)          # noqa: E731

    script = Script()
    orch = make(tmp_path, script)
    orch.gateway.providers = {"main": Family(script, "main"),
                              "other": Family(lambda s, p: "I am operating strictly as a plain-text model.", "other")}
    orch.run()
    assert not orch.state("stopped")
    assert orch.store.get(orch.state("hypothesis_id"))["status"] == "supported"
    bad = [e for e in orch.store.events("model.invalid_json") if e["actor"]["modelFamily"] == "other"]
    assert bad                     # the broken family was used, and every role fell back to the working one


def test_restricted_hypothesis_is_not_reported_as_full_answer(tmp_path):
    script = Script(critic_objects_first=False)

    def restricted(system, prompt):
        out = script(system, prompt)
        if system == prompts.HYPOTHESIS:
            data = json.loads(out)
            data["hypotheses"][0]["scope"] = {"relation": "restricted", "note": "Only for n ≤ 100."}
            return json.dumps(data)
        if system == prompts.CRITIC_STATEMENT:
            data = json.loads(out)
            data.update(weakerThanQuestion=True, scopeNote="The infinite case remains open.")
            return json.dumps(data)
        return out

    orch = make(tmp_path, restricted)
    report = open(orch.run(), encoding="utf-8").read()
    assert "restricted version of the question was proved" in report and "original question remains open" in report
    assert "Only for n ≤ 100." in report and "The infinite case remains open." in report
    h = orch.store.get(orch.state("hypothesis_id"))
    assert h["scopeRelation"]["relation"] == "restricted"


def test_gateway_calibrates_after_underestimate():
    perms = Permissions.load()
    events = []
    gw = Gateway({"anthropic": ScriptedProvider(lambda s, p: "{}", "anthropic", cost_per_call=10.0)}, 100.0, perms.agents,
                 lambda k, p: events.append((k, p)))
    gw.provider_cost_cap = None
    # If the provider reports a cost above the estimate (ScriptedProvider clamps to the estimate; we disable the clamping here)
    gw.providers["anthropic"].complete = lambda m, s, p, n, budget_usd, effort=None: __import__("quaera.gateway", fromlist=["Completion"]).Completion("{}", "x", "anthropic", 1.0)
    first = gw.call("literature", "s", "p", 100)
    assert any(k == "budget.estimate_exceeded" for k, _ in events)
    factor = gw.calibration["haiku"]
    assert factor > 1.0
    gw.call("literature", "s", "p", 100)
    est2 = [p for k, p in events if k == "model.call"][-1]["estimateUsd"]
    assert est2 >= 1.0  # the new estimate covers the observed cost


def test_literature_service_outage_does_not_stop_research(tmp_path):
    orch = make(tmp_path, Script())
    real = orch.tools.call

    def flaky(role, tool, **a):
        if tool in ("arxiv.search", "openalex.search"):
            raise RuntimeError("Error executing tool arxiv_search: 503")
        return real(role, tool, **a)

    orch.tools.call = flaky
    orch.tools.call_json = lambda role, tool, **a: json.loads(flaky(role, tool, **a))
    orch.run()
    assert orch.store.events("tool.error")
    assert orch.store.get(orch.state("hypothesis_id"))["status"] == "supported"
