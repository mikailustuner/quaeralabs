"""Behaviour tests for the ML loop: scripted models, real Python execution (no sandbox, in a temporary directory)."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from quaera import prompts
from quaera.gateway import Gateway, ScriptedProvider
from quaera.ml_loop import MLOrchestrator, compare, stats_table
from quaera.orchestrator import AutoApprover, CapApprover
from quaera.permissions import Permissions
from quaera.store import Store

SCRIPT = '''import argparse, json, random
p = argparse.ArgumentParser(); p.add_argument("--seed", type=int); p.add_argument("--pilot", action="store_true")
a = p.parse_args(); random.seed(a.seed)
lin = 0.50 + random.random() * 0.02
mlp = 0.95 + random.random() * 0.02
print("QUAERA_METRICS " + json.dumps({"linear_acc": lin, "mlp_acc": mlp}))
'''
BROKEN = "import nonexistent_module\n"


class SandboxTools:
    """Runs the sandbox.* tools with real Python in a temporary directory; permission checks are real."""

    def __init__(self, permissions):
        self.permissions = permissions
        self.calls = []
        self.commits = {}

    def call(self, role, tool, **a):
        self.permissions.check_tool(role, tool)
        if tool == "sandbox.exec" and a.get("gpu"):
            self.permissions.check_action(role, "gpu_spend")
        self.calls.append((role, tool))
        if tool in ("arxiv.search", "openalex.search"):
            return "[]"
        if tool == "sandbox.write":
            w = Path(a["workdir"]); w.mkdir(parents=True, exist_ok=True)
            (w / a["path"]).write_text(a["content"])
            commit = f"{len(self.commits):07x}"
            self.commits[commit] = a["content"]
            return json.dumps({"commit": commit})
        if tool == "sandbox.create_clean":
            d = Path(tempfile.mkdtemp())
            (d / "experiment.py").write_text(self.commits[a["commit"]])
            return json.dumps({"workdir": str(d)})
        if tool == "sandbox.exec":
            p = subprocess.run([sys.executable, *a["command"][1:]], cwd=a["workdir"], capture_output=True, text=True, timeout=60)
            return json.dumps({"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "timed_out": False, "gpu": False})
        raise AssertionError(tool)

    def call_json(self, role, tool, **a):
        return json.loads(self.call(role, tool, **a))

    def close(self):
        pass


class MLScript:
    def __init__(self, plan_objection=True, result_objection=False, analyst_accepts=True, broken_first=True, result_severity="high"):
        self.n = {}
        self.result_severity = result_severity
        self.plan_objection, self.result_objection = plan_objection, result_objection
        self.analyst_accepts, self.broken_first = analyst_accepts, broken_first

    def __call__(self, system, prompt):
        key = next(k for k in ("LITERATURE_PLAN", "LITERATURE_SUMMARY", "HYPOTHESIS_ML", "RANK_HYPOTHESES", "DESIGN_ML", "CRITIC_EXPERIMENT",
                               "ENGINEER_ML", "ANALYST_RESPONSE", "ANALYST", "WRITER") if getattr(prompts, k) == system)
        self.n[key] = i = self.n.get(key, 0) + 1
        if key == "LITERATURE_PLAN":
            return '{"arxiv_queries": [], "openalex_queries": [], "mathlib_queries": []}'
        if key == "LITERATURE_SUMMARY":
            return '{"summary": "No relevant results.", "verdict": "unknown", "basis": "search", "cited": []}'
        if key == "RANK_HYPOTHESES":
            return '{"ranking": [{"index": 0, "testability": 8, "plausibility": 7, "novelty": 4, "scope": 9, "cost": 8, "reason": "Testable."}]}'
        if key == "HYPOTHESIS_ML":
            return json.dumps({"hypotheses": [{"statement": "A linear model cannot exceed 75%.", "falsifiabilityNote": "Refuted if linear accuracy > 0.75.",
                                               "scope": {"relation": "full", "note": ""}}]})
        if key == "DESIGN_ML":
            seeds = 2 if i == 1 else 3   # first plan 2 seeds (the Critic objects), revision 3 seeds
            return json.dumps({"method": "Compare logistic regression with an MLP.", "baselines": ["MLP"],
                               "metrics": [{"name": "linear_acc", "direction": "higher_is_better"}, {"name": "mlp_acc", "direction": "higher_is_better"}],
                               "primaryMetric": "linear_acc", "successCriterion": "Supported if mean linear_acc < 0.75.",
                               "analysisPlan": "Seed mean and 95% CI.", "seeds": seeds, "estimatedMinutes": 1, "_tag": f"plan{i}"})
        if key == "CRITIC_EXPERIMENT":
            if "PLAN" in prompt:
                bad = self.plan_objection and '"_tag": "plan1"' in prompt
                return json.dumps({"flawed": bad, "categories": ["statistics"] if bad else [], "severity": "high" if bad else None,
                                   "evidence": "2 seeds are not enough", "summary": "Too few seeds." if bad else "Plan is fine."})
            bad = self.result_objection and "contradicts" not in prompt and self.n[key] <= 3
            return json.dumps({"flawed": bad, "categories": ["overclaim"] if bad else [], "severity": self.result_severity if bad else None,
                               "evidence": "", "summary": "The result is overstated." if bad else "No issues."})
        if key == "ENGINEER_ML":
            return f"```python\n{BROKEN if (self.broken_first and i == 1) else SCRIPT}```"
        if key == "ANALYST":
            return json.dumps({"relation": "supports", "summary": "linear_acc ≈ 0.51 < 0.75.", "negative": False, "limitations": ["Single dataset."]})
        if key == "ANALYST_RESPONSE":
            return json.dumps({"accept": self.analyst_accepts, "response": "Accepted.", "relation": "inconclusive",
                               "summary": "Cautious interpretation.", "limitations": ["Single dataset."]})
        if key == "WRITER":
            return json.dumps({"discussion": ["The linear model stayed at about 51% accuracy [RES-0001].", "This sentence has no source."],
                               "answer": "no", "answerReason": "Linear accuracy is below 75% [RES-0001, VER-0001]."})


def make(tmp_path, script, cap=5.0, approver=None):
    perms = Permissions.load()
    store = Store(tmp_path / "p" / "quaera.db", perms)
    store.set_meta("title", "XOR"); store.set_meta("domain", "ml"); store.set_meta("dataDir", str(tmp_path))
    gw = Gateway({"scripted": ScriptedProvider(script)}, cap, perms.agents)
    orch = MLOrchestrator(store, gw, SandboxTools(perms), approver or AutoApprover(100), perms, log=lambda m: None,
                          reports_dir=tmp_path / "p")
    store.put({"type": "question", "createdBy": orch.human(), "title": "Can a linear model exceed 75%?", "domain": "ml",
               "scope": "/data/train.npz"})
    return orch


def test_ml_loop_end_to_end(tmp_path):
    script = MLScript()
    orch = make(tmp_path, script)
    report = open(orch.run(), encoding="utf-8").read()
    s = orch.store
    assert s.check_final() == []
    h = s.get(orch.state("hypothesis_id"))
    assert h["status"] == "supported"
    # Plan objection → revision (3 seeds) → resolved
    assert s.latest("preregistration")[-1]["seeds"] == 3
    assert any(c["status"] == "resolved" for c in s.latest("critique"))
    # Broken pilot → fix; 3 full runs; verification matches exactly
    runs = s.latest("run")
    assert [r["status"] for r in runs if r["kind"] == "pilot"] == ["failed", "succeeded"]
    assert len([r for r in runs if r["kind"] == "full"]) == 3
    assert s.latest("verification")[-1]["reproduced"] == "yes"
    # Writer: the unsourced sentence was dropped, the answer is tied to a source
    w = s.events("writer.output")[-1]["payload"]
    assert w["answer"] == "no" and len(w["kept"]) == 1 and len(w["dropped"]) == 1
    assert "Answer to the question" in report and "**No.**" in report and "computed by code" in report


def test_result_objection_debate_rounds(tmp_path):
    orch = make(tmp_path, MLScript(plan_objection=False, result_objection=True, broken_first=False))
    orch.run()
    s = orch.store
    crits = [c for c in s.latest("critique") if c["targetId"].startswith("RES")]
    assert crits and all(c["status"] == "accepted" for c in crits)
    assert s.get(orch.state("result_id"))["revision"] >= 2
    rounds = [m.get("round") for m in s.latest("message") if m["kind"] == "objection"]
    assert max(rounds) <= 3
    assert s.check_final() == []


def test_medium_result_flaw_is_not_dropped(tmp_path):
    # Bug found by live fault injection: a "medium" severity overgeneralization did not become an objection.
    orch = make(tmp_path, MLScript(plan_objection=False, result_objection=True, broken_first=False, result_severity="medium"))
    orch.run()
    s = orch.store
    crits = [c for c in s.latest("critique") if c["targetId"].startswith("RES")]
    assert crits and crits[0]["severity"] == "medium" and all(c["status"] == "accepted" for c in crits)
    assert any(m["kind"] == "response" and m["createdBy"]["role"] == "analyst" for m in s.latest("message"))
    assert s.get(orch.state("result_id"))["summary"] == "Cautious interpretation."
    assert s.check_final() == []


def test_unresolved_objection_blocks_support(tmp_path):
    script = MLScript(plan_objection=False, result_objection=True, analyst_accepts=False, broken_first=False)
    orch = make(tmp_path, script)
    orch.run()
    h = orch.store.get(orch.state("hypothesis_id"))
    assert h["status"] == "under_critique"
    assert orch.store.check_final() == []


def test_cap_approver_respects_budget_and_human_only_actions():
    class GW:
        def remaining(self):
            return 1.0
    ap = CapApprover(GW())
    assert ap.decide("approve_experiment", "", 0.9).approved
    assert not ap.decide("approve_experiment", "", 1.5).approved
    assert not ap.decide("publish", "", 0.0).approved
    assert not ap.decide("raise_budget_cap", "", 0.0).approved


def test_gpu_requires_permission(tmp_path):
    from quaera.permissions import PermissionDenied
    tools = SandboxTools(Permissions.load())
    with pytest.raises(PermissionDenied):
        tools.call("analyst", "sandbox.exec", workdir=str(tmp_path), command=["python", "-c", "1"], gpu=True)


def test_stats_and_compare():
    t = stats_table([{"metrics": {"a": 1.0}}, {"metrics": {"a": 2.0}}, {"metrics": {"a": 3.0}}])
    assert t["a"]["mean"] == 2.0 and t["a"]["std"] == 1.0
    assert t["a"]["ci95"][0] < 2.0 < t["a"]["ci95"][1]
    assert compare({"a": 0.5}, {"a": 0.5})[0] == "yes"
    assert compare({"a": 0.5}, {"a": 0.502})[0] == "partial"
    assert compare({"a": 0.5}, {"a": 0.6})[0] == "no"
