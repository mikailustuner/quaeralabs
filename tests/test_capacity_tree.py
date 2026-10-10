"""Capacity plan Phase 3: tree search selection (T1), continue branches (T2), stop rule (T3), budget split and top-up (T4)."""

import json

from starlette.testclient import TestClient

import quaera.cli as cli
import quaera.server as server
from quaera import tree
from quaera.gateway import ScriptedProvider
from quaera.permissions import Permissions

from test_orchestrator import Script, make
from test_tree import always_failing_proof

AGENTS = Permissions.load().agents


def test_best_node_is_expanded_first_and_budget_follows_score(tmp_path):
    make(tmp_path, always_failing_proof(), name="root").run()
    a = tree.branch_project(tmp_path, "root", "approach", "Try induction.", note="Use induction.",
                            by={"kind": "human", "userId": "u"})
    make(tmp_path, always_failing_proof(), name=a).run()
    # a node with verified lemmas scores higher than one without
    s = make(tmp_path, Script(), name=a).store
    s.append("lemma.status", {"kind": "agent", "role": "engineer", "model": "quaera/deterministic", "modelFamily": "quaera"},
             {"id": "L1", "name": "quaera_L1", "status": "verified", "round": 1, "family": "x", "detail": "", "sha256": None, "strategy": "S1"})
    assert tree.progress_score(tmp_path, a)["score"] > tree.progress_score(tmp_path, "root")["score"]
    director = ScriptedProvider(lambda s_, p: json.dumps({"decision": "approach", "instructions": "Another tactic.", "reason": "Change."}))
    budgets = []
    created = tree.iterate(tmp_path, "root", build=lambda path, b: budgets.append(b) or make(tmp_path, always_failing_proof(), name=path.name),
                           providers={"scripted": director}, agent_specs=AGENTS, approve=lambda t, c: True,
                           max_branches=1, budget_per_branch=1.0, log=lambda m: None)
    assert created and created[0].startswith(a + "-d")          # the better node was expanded, not the root
    assert make(tmp_path, Script(), name=a).store.events("tree.decision")[-1]["payload"]["selected"] == a


def test_stop_is_rejected_when_directions_remain_and_a_second_opinion_proposes_one(tmp_path):
    make(tmp_path, always_failing_proof(), name="root").run()
    answers = iter([{"decision": "stop", "reason": "Nothing works."},
                    {"decision": "approach", "instructions": "Use strong induction.", "reason": "Not tried yet."}])
    director = ScriptedProvider(lambda s_, p: json.dumps(next(answers)))
    created = tree.iterate(tmp_path, "root", build=lambda path, b: make(tmp_path, Script(), name=path.name),
                           providers={"scripted": director}, agent_specs=AGENTS, approve=lambda t, c: True,
                           max_branches=1, budget_per_branch=1.0, log=lambda m: None)
    root = make(tmp_path, Script(), name="root").store
    rejected = root.events("branch.stop_rejected")
    assert created == ["root-d1"] and rejected and "a approach change" in " ".join(rejected[0]["payload"]["gaps"])


def test_call_budget_stops_the_line(tmp_path):
    make(tmp_path, always_failing_proof(), name="root").run()
    calls = tree.line_usage(tmp_path, "root")["calls"]
    created = tree.iterate(tmp_path, "root", build=None, providers={}, agent_specs=AGENTS, approve=lambda t, c: True,
                           max_branches=3, budget_per_branch=1.0, max_calls=calls, log=lambda m: None)
    assert created == []


def test_continue_branch_needs_a_lemma_program(tmp_path):
    make(tmp_path, always_failing_proof(), name="root").run()
    try:
        tree.branch_project(tmp_path, "root", "continue", "Keep going.", note="Attack L2 differently.", by={"kind": "human", "userId": "u"})
    except ValueError as exc:
        assert "lemma program" in str(exc)
    else:
        raise AssertionError("a verify-mode project cannot be continued")


def test_budget_top_up_and_keep_trying_switch(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(cli, "HOME", projects)
    monkeypatch.setattr(server, "HOME", projects)
    orch = make(projects, Script(), name="p1")
    orch.store.set_meta("budgetCapUsd", 2.0)
    c = TestClient(server.create_app())
    assert c.post("/api/projects/p1/budget", json={"addUsd": 3}).json()["budgetCapUsd"] == 5.0
    assert c.post("/api/projects/p1/budget", json={"addUsd": -1}).status_code == 400
    assert orch.store.events("budget.raised")[-1]["actor"]["kind"] == "human"
    assert c.post("/api/projects/p1/keep-trying", json={"on": True}).json() == {"keepTrying": True}
    assert c.get("/api/projects").json()[0]["keepTrying"] is True
