"""Research tree: branches with changes, the tree view (diffs, rates) and iterative research."""

import json

from quaera import prompts, tree
from quaera.gateway import ScriptedProvider
from quaera.permissions import Permissions

from test_orchestrator import Script, make

HUMAN = {"kind": "human", "userId": "researcher"}


def always_failing_proof():
    script = Script(critic_objects_first=False)

    def responder(system, prompt):
        out = script(system, prompt)
        return out.replace("ring", "BADPROOF") if system == prompts.PROVE else out
    return responder


def test_human_hypothesis_branch_and_tree_view(tmp_path):
    root = make(tmp_path, Script(), name="root", cost=0.01)
    root.run()
    child = tree.branch_project(tmp_path, "root", "hypothesis", "Let us try a stronger claim.",
                                hypothesis="For every n, the sum of the first n odd numbers is a perfect square.", by=HUMAN)
    assert child == "root-d1"
    (tmp_path / "root-branch-11").mkdir()          # Phase 3 style literal branch names must not break the numbering
    assert tree.branch_project(tmp_path, "root", "note", "Extra note.", note="Check the n=0 case.", at_stage="hypothesis_approval",
                               by=HUMAN) == "root-d2"
    c = make(tmp_path, Script(), name=child, cost=0.01)
    c.run()
    h = c.store.get(c.state("hypothesis_id"))
    assert h["statement"] == "For every n, the sum of the first n odd numbers is a perfect square." and h["createdBy"] == HUMAN
    assert c.store.events("branch.change")[0]["payload"]["reason"] == "Let us try a stronger claim."
    f = tree.family(tmp_path, child)
    assert f["root"] == "root" and {n["id"] for n in f["nodes"]} == {"root", "root-d1", "root-d2"}
    node = next(n for n in f["nodes"] if n["id"] == "root-d1")
    assert node["parent"] == "root" and node["branch"]["kind"] == "hypothesis"
    assert ["+", "perfect square."] in node["diff"]["hypothesis"] or any(op == "+" for op, _ in node["diff"]["hypothesis"])
    assert f["stats"]["branches"] == 3 and f["stats"]["finished"] == 2       # root-d2 was opened but not run
    assert f["stats"]["supported"] == 2 and f["stats"]["successRate"] == 1.0
    # branch cost is only the calls after the branch point: the parent's copied spend is not counted twice
    copied = [e for e in c.store.events("model.call") if e["seq"] <= c.store.meta("branch")["atSeq"]]
    total = round(sum(e["payload"]["costUsd"] for e in c.store.events("model.call")), 4)
    assert copied and node["costUsd"] < total and node["costUsd"] == round(sum(e["payload"]["costUsd"] for e in c.store.events("model.call")
                                                   if e["seq"] > c.store.meta("branch")["atSeq"]), 4)


def test_inconclusive_branch_is_iterated_with_director_change(tmp_path):
    first = make(tmp_path, always_failing_proof(), name="root")
    first.run()
    assert tree.node_summary(tmp_path, "root")["outcome"] == "inconclusive"
    decision = {"decision": "approach", "instructions": "Prove it by induction; use Finset.sum_range_succ.",
                "reason": "The proof did not compile; induction is a sturdier route."}
    director = ScriptedProvider(lambda s, p: json.dumps(decision))
    asked = []
    created = tree.iterate(tmp_path, "root", build=lambda path, budget: make(tmp_path, Script(), name=path.name),
                           providers={"scripted": director}, agent_specs=Permissions.load().agents,
                           approve=lambda text, cost: asked.append(cost) or True, max_branches=3, budget_per_branch=1.0,
                           log=lambda m: None)
    assert created == ["root-d1"] and len(asked) == 1          # child succeeded → iteration stops
    child = make(tmp_path, Script(), name="root-d1").store
    assert child.meta("branch")["by"]["role"] == "director" and child.meta("branch")["kind"] == "approach"
    delivered = {(e["payload"]["role"]) for e in child.events("message.delivered")}
    assert {"experiment_designer", "engineer"} & delivered               # the instructions reached the relevant agent
    parent = make(tmp_path, Script(), name="root").store
    assert parent.events("branch.proposed")[0]["payload"]["decision"] == "approach"
    assert parent.events("branch.spawned")[0]["payload"]["child"] == "root-d1"
    stats = tree.family(tmp_path, "root")["stats"]
    assert stats["inconclusive"] == 1 and stats["supported"] == 1 and stats["successRate"] == 0.5


def test_no_branch_without_approval_and_refuted_is_not_iterated(tmp_path):
    first = make(tmp_path, always_failing_proof(), name="root")
    first.run()
    created = tree.iterate(tmp_path, "root", build=None, providers={}, agent_specs=Permissions.load().agents,
                           approve=lambda text, cost: False, max_branches=2, budget_per_branch=1.0, log=lambda m: None)
    assert created == []
    assert not tree.needs_iteration({"outcome": "refuted", "stopped": None})


def test_keep_trying_until_the_total_budget_is_spent_with_full_history(tmp_path):
    make(tmp_path, always_failing_proof(), name="root", cost=0.05).run()
    prompts_seen, n = [], [0]

    def director(system, prompt):
        prompts_seen.append(prompt)
        n[0] += 1
        return json.dumps({"decision": "approach", "instructions": f"Strategy {n[0]}: try a different tactic.",
                           "reason": f"Attempt {n[0]} failed; change tactic."})
    budgets = []

    def build(path, budget):
        budgets.append(budget)
        return make(tmp_path, always_failing_proof(), name=path.name, cost=0.05)
    root_cost = tree.node_summary(tmp_path, "root")["costUsd"]
    total = root_cost + 4.0
    created = tree.iterate(tmp_path, "root", build=build, providers={"scripted": ScriptedProvider(director)},
                           agent_specs=Permissions.load().agents, approve=lambda text, cost: True, max_branches=50,
                           total_budget=total, log=lambda m: None)
    assert len(created) >= 2                                            # kept going after the first failed branch
    spent = sum(tree.node_summary(tmp_path, p)["costUsd"] for p in tree.lineage(tmp_path, created[-1]))
    assert total - spent - tree.REVISE_CAP_USD < tree.MIN_BRANCH_USD    # stopped because the budget ran out
    assert all(b <= total for b in budgets) and budgets == sorted(budgets, reverse=True)   # each branch gets what is left
    assert tree.lineage(tmp_path, created[-1])[:2] == ["root", created[0]]
    assert "Strategy 1: try a different tactic." in prompts_seen[-1]    # the Director sees every earlier attempt


def test_repeated_hypothesis_is_rejected_and_the_loop_stops(tmp_path):
    first = make(tmp_path, always_failing_proof(), name="root")
    first.run()
    same = tree.node_summary(tmp_path, "root")["hypothesis"]["statement"]
    director = ScriptedProvider(lambda s, p: json.dumps({"decision": "hypothesis", "newHypothesis": same, "reason": "Try again."}))
    created = tree.iterate(tmp_path, "root", build=None, providers={"scripted": director}, agent_specs=Permissions.load().agents,
                           approve=lambda text, cost: True, max_branches=3, budget_per_branch=1.0, log=lambda m: None)
    parent = make(tmp_path, Script(), name="root").store
    assert created == [] and len(parent.events("branch.repeat_rejected")) == 2
    assert parent.events("branch.proposed")[-1]["payload"]["decision"] == "stop"
