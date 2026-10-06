"""Araştırma ağacı: değişiklikli dallar, ağaç görünümü (farklar, oranlar) ve yinelemeli araştırma."""

import json

from quaera import prompts, tree
from quaera.gateway import ScriptedProvider
from quaera.permissions import Permissions

from test_orchestrator import Script, make

HUMAN = {"kind": "human", "userId": "arastirmaci"}


def always_failing_proof():
    script = Script(critic_objects_first=False)

    def responder(system, prompt):
        out = script(system, prompt)
        return out.replace("ring", "BADPROOF") if system == prompts.PROVE else out
    return responder


def test_human_hypothesis_branch_and_tree_view(tmp_path):
    root = make(tmp_path, Script(), name="kok", cost=0.01)
    root.run()
    child = tree.branch_project(tmp_path, "kok", "hypothesis", "Daha güçlü bir iddia deneyelim.",
                                hypothesis="Her n için ilk n tek sayının toplamı tam karedir.", by=HUMAN)
    assert child == "kok-d1"
    (tmp_path / "kok-dal-11").mkdir()          # Faz 3 tarzı birebir dal adları numaralandırmayı bozmamalı
    assert tree.branch_project(tmp_path, "kok", "note", "Ek not.", note="n=0 durumuna bak.", at_stage="hypothesis_approval",
                               by=HUMAN) == "kok-d2"
    c = make(tmp_path, Script(), name=child, cost=0.01)
    c.run()
    h = c.store.get(c.state("hypothesis_id"))
    assert h["statement"] == "Her n için ilk n tek sayının toplamı tam karedir." and h["createdBy"] == HUMAN
    assert c.store.events("branch.change")[0]["payload"]["reason"] == "Daha güçlü bir iddia deneyelim."
    f = tree.family(tmp_path, child)
    assert f["root"] == "kok" and {n["id"] for n in f["nodes"]} == {"kok", "kok-d1", "kok-d2"}
    node = next(n for n in f["nodes"] if n["id"] == "kok-d1")
    assert node["parent"] == "kok" and node["branch"]["kind"] == "hypothesis"
    assert ["+", "tam karedir."] in node["diff"]["hypothesis"] or any(op == "+" for op, _ in node["diff"]["hypothesis"])
    assert f["stats"]["branches"] == 3 and f["stats"]["finished"] == 2       # kok-d2 açıldı ama çalıştırılmadı
    assert f["stats"]["supported"] == 2 and f["stats"]["successRate"] == 1.0
    # dal maliyeti yalnızca dallanmadan sonraki çağrılar: ebeveynin kopyalanan harcaması iki kez sayılmaz
    copied = [e for e in c.store.events("model.call") if e["seq"] <= c.store.meta("branch")["atSeq"]]
    total = round(sum(e["payload"]["costUsd"] for e in c.store.events("model.call")), 4)
    assert copied and node["costUsd"] < total and node["costUsd"] == round(sum(e["payload"]["costUsd"] for e in c.store.events("model.call")
                                                   if e["seq"] > c.store.meta("branch")["atSeq"]), 4)


def test_inconclusive_branch_is_iterated_with_director_change(tmp_path):
    first = make(tmp_path, always_failing_proof(), name="kok")
    first.run()
    assert tree.node_summary(tmp_path, "kok")["outcome"] == "inconclusive"
    decision = {"decision": "approach", "instructions": "Tümevarımla ispatla; Finset.sum_range_succ kullan.",
                "reason": "İspat derlenmedi; tümevarım daha sağlam bir yol."}
    director = ScriptedProvider(lambda s, p: json.dumps(decision))
    asked = []
    created = tree.iterate(tmp_path, "kok", build=lambda path, budget: make(tmp_path, Script(), name=path.name),
                           providers={"scripted": director}, agent_specs=Permissions.load().agents,
                           approve=lambda text, cost: asked.append(cost) or True, max_branches=3, budget_per_branch=1.0,
                           log=lambda m: None)
    assert created == ["kok-d1"] and len(asked) == 1          # çocuk başarılı → yineleme durur
    child = make(tmp_path, Script(), name="kok-d1").store
    assert child.meta("branch")["by"]["role"] == "director" and child.meta("branch")["kind"] == "approach"
    delivered = {(e["payload"]["role"]) for e in child.events("message.delivered")}
    assert {"experiment_designer", "engineer"} & delivered               # talimat ilgili ajana iletildi
    parent = make(tmp_path, Script(), name="kok").store
    assert parent.events("branch.proposed")[0]["payload"]["decision"] == "approach"
    assert parent.events("branch.spawned")[0]["payload"]["child"] == "kok-d1"
    stats = tree.family(tmp_path, "kok")["stats"]
    assert stats["inconclusive"] == 1 and stats["supported"] == 1 and stats["successRate"] == 0.5


def test_no_branch_without_approval_and_refuted_is_not_iterated(tmp_path):
    first = make(tmp_path, always_failing_proof(), name="kok")
    first.run()
    created = tree.iterate(tmp_path, "kok", build=None, providers={}, agent_specs=Permissions.load().agents,
                           approve=lambda text, cost: False, max_branches=2, budget_per_branch=1.0, log=lambda m: None)
    assert created == []
    assert not tree.needs_iteration({"outcome": "refuted", "stopped": None})


def test_keep_trying_until_the_total_budget_is_spent_with_full_history(tmp_path):
    make(tmp_path, always_failing_proof(), name="kok", cost=0.05).run()
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
    root_cost = tree.node_summary(tmp_path, "kok")["costUsd"]
    total = root_cost + 4.0
    created = tree.iterate(tmp_path, "kok", build=build, providers={"scripted": ScriptedProvider(director)},
                           agent_specs=Permissions.load().agents, approve=lambda text, cost: True, max_branches=50,
                           total_budget=total, log=lambda m: None)
    assert len(created) >= 2                                            # kept going after the first failed branch
    spent = sum(tree.node_summary(tmp_path, p)["costUsd"] for p in tree.lineage(tmp_path, created[-1]))
    assert total - spent - tree.REVISE_CAP_USD < tree.MIN_BRANCH_USD    # stopped because the budget ran out
    assert all(b <= total for b in budgets) and budgets == sorted(budgets, reverse=True)   # each branch gets what is left
    assert tree.lineage(tmp_path, created[-1])[:2] == ["kok", created[0]]
    assert "Strategy 1: try a different tactic." in prompts_seen[-1]    # the Director sees every earlier attempt


def test_repeated_hypothesis_is_rejected_and_the_loop_stops(tmp_path):
    first = make(tmp_path, always_failing_proof(), name="kok")
    first.run()
    same = tree.node_summary(tmp_path, "kok")["hypothesis"]["statement"]
    director = ScriptedProvider(lambda s, p: json.dumps({"decision": "hypothesis", "newHypothesis": same, "reason": "Try again."}))
    created = tree.iterate(tmp_path, "kok", build=None, providers={"scripted": director}, agent_specs=Permissions.load().agents,
                           approve=lambda text, cost: True, max_branches=3, budget_per_branch=1.0, log=lambda m: None)
    parent = make(tmp_path, Script(), name="kok").store
    assert created == [] and len(parent.events("branch.repeat_rejected")) == 2
    assert parent.events("branch.proposed")[-1]["payload"]["decision"] == "stop"
