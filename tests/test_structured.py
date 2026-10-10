"""Capacity plan R1: a weak model's broken JSON is coerced, repaired or degraded instead of ending the research."""

import json

from quaera import prompts
from quaera.structured import REPAIR_JSON, normalize, parse

from test_orchestrator import Script, make


def test_shape_coercion_handles_weak_model_habits():
    out = parse('Sure! Here it is: {"plausibility": "7/10", "novelty": 6, "barrierAwareness": "score: 3", "testability": null}',
                "CROSS_REVIEW")
    assert out["plausibility"] == 7.0 and out["barrierAwareness"] == 3.0 and out["testability"] == 0.0
    assert normalize([{"statement": "a"}], "HYPOTHESIS") == {"hypotheses": [{"statement": "a"}]}   # a bare list is wrapped
    assert normalize({"faithful": "yes", "issues": {"severity": "low", "body": "x"}}, "CRITIC_STATEMENT")["issues"] == [
        {"severity": "low", "body": "x"}]


def test_broken_json_is_repaired_by_a_cheap_call_and_the_research_continues(tmp_path):
    script = Script()

    def weak(system, prompt):
        if system == REPAIR_JSON:                       # the repair pass returns the content as JSON
            return '{"faithful": true, "issues": [], "summary": "Repaired."}'
        if system == prompts.CRITIC_STATEMENT and "n * n + 1" not in prompt:
            return "The statement is faithful, no issues at all. faithful: true"   # prose instead of JSON
        return script(system, prompt)

    orch = make(tmp_path, weak)
    orch.run()
    assert not orch.state("stopped")
    assert orch.store.events("model.repaired")[0]["payload"]["level"] == "repair"
    assert orch.store.get(orch.state("hypothesis_id"))["status"] == "supported"


def test_degradable_call_returns_default_when_every_level_fails(tmp_path):
    orch = make(tmp_path, lambda s, p: "no json here")
    out, _ = orch.ask_json("literature", prompts.LANDSCAPE, "q", 500, default={"approaches": []})
    assert out == {"approaches": []} and orch.store.events("model.degraded")
    assert len(orch.store.events("model.invalid_json")) >= 2
