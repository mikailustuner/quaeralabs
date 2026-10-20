"""Improvement plan phase 4 (2026-10-20): one Lean REPL for all branches (A4), the uncontaminated
measurement sets (C1), discovery expectations (D4). No model calls, no real Lean."""

import json
import sys
from pathlib import Path

import pytest

from quaera import lean, tools
from quaera.permissions import Permissions

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))


# --- A4: one Lean server per process ----------------------------------------------------------------------------------

class FakePool:
    def __init__(self):
        self.calls = []

    def call(self, server, tool, args, timeout=3600):
        self.calls.append((server, tool))
        return "{}"

    def close(self):
        pass


def test_branches_share_one_lean_server(monkeypatch):
    shared = FakePool()
    monkeypatch.setattr(tools, "shared_pool", lambda: shared)
    perms = Permissions.load()
    a, b = FakePool(), FakePool()
    for pool in (a, b):
        reg = tools.ToolRegistry(perms, pool=pool)
        reg.call("engineer", "lean.compile", source="x")
        reg.call("literature", "arxiv.search", query="q")
    assert shared.calls == [("lean", "lean_compile")] * 2                 # both branches, one server
    assert a.calls == b.calls == [("literature", "arxiv_search")]        # other servers stay per branch
    monkeypatch.setenv("QUAERA_SHARED_LEAN", "0")
    tools.ToolRegistry(perms, pool=a).call("engineer", "lean.compile", source="x")
    assert a.calls[-1] == ("lean", "lean_compile")


def test_a_failed_mathlib_load_is_recorded_and_explained(monkeypatch, tmp_path):
    monkeypatch.setenv("QUAERA_HOME", str(tmp_path))
    with pytest.raises(lean.LeanUnavailable, match="within 1 s. On a slow"):
        lean._Repl(["sleep", "30"], load_timeout_s=1)
    load = lean.last_load()
    assert load["ok"] is False and load["seconds"] >= 1


def test_one_shot_builds_take_a_slot():
    assert lean.LEAN_SLOTS.acquire(blocking=False)
    lean.LEAN_SLOTS.release()


# --- C1: measurement sets -----------------------------------------------------------------------------------------------

def test_putnambench_is_pinned_without_answers():
    from _harness import CONTAMINATION, SET_NAMES, load_set
    tasks = load_set("putnambench", n=50, seed=1)
    assert len(tasks) == 50 and len({t.name for t in tasks}) == 50
    assert all("_solution" not in t.statement_file for t in tasks)          # problems with an answer to guess are left out
    assert load_set("putnambench", n=5, seed=1)[0].name != load_set("putnambench", n=5, seed=2)[0].name
    rows = [json.loads(l) for l in next((ROOT / "evals/data").glob("putnambench-*.jsonl")).read_text().splitlines()]
    assert len(rows) == 672
    for r in rows:     # PutnamBench writes the answer as a comment right below `..._solution := sorry`; it must be gone
        assert ":= sorry\n--" not in r["statement_file"].replace(" ", "")
    assert set(SET_NAMES) <= set(CONTAMINATION)


def test_fresh_set_loads_only_approved_problems():
    from _harness import load_set
    assert load_set("fresh") == []


def test_hidden_split_needs_a_release_tag():
    import capacity_run
    with pytest.raises(SystemExit):
        capacity_run.main(["--set", "capacity-synth", "--split", "hidden", "--budget", "1"])


# --- D4: discovery expectations --------------------------------------------------------------------------------------------

def test_discovery_expectations_and_repeated_failures(tmp_path):
    from capacity_run import expectation_met, repeated_failures
    score = {"mainSolved": False, "verifiedLemmas": 2}
    assert expectation_met("lemmas", score, None) and not expectation_met("synthesis", score, None)
    assert expectation_met("mathlib", score, "mathlib") and expectation_met("anything", score, None) is None
    rows = [{"id": "odd-sums", "met": False}, {"id": "erdos-straus", "met": True}]
    assert repeated_failures(rows, tmp_path) == []
    (tmp_path / "capacity-discovery-cheap-20261020T000000.json").write_text(json.dumps(
        {"rows": {"discovery": [{"id": "odd-sums", "met": False}, {"id": "erdos-straus", "met": False}]}}))
    assert repeated_failures(rows, tmp_path) == ["odd-sums"]
    import yaml
    targets = yaml.safe_load((ROOT / "evals/sets/capacity-discovery.yaml").read_text())["targets"]
    assert {t["id"]: t["expect"] for t in targets}["sum-of-squares-primes"] == "mathlib"
