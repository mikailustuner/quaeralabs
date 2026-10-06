"""#3 experimental exploration: small-case scan and counterexample in math, data profile in ML."""

import json

from quaera import prompts

from test_ml_loop import MLScript, make as make_ml
from test_orchestrator import FakeTools, Script, make


def test_exploration_reaches_prover_prompt(tmp_path):
    script, prove_prompts = Script(critic_objects_first=False), []

    def spy(system, prompt):
        if system == prompts.PROVE:
            prove_prompts.append(prompt)
        return script(system, prompt)
    orch = make(tmp_path, spy)
    orch.run()
    assert orch.state("exploration")["checked"] == "n ≤ 200"
    assert "Numerical exploration" in prove_prompts[0] and "n*n" in prove_prompts[0]
    assert orch.store.events("exploration.done")


class CounterexampleTools(FakeTools):
    def call(self, role, tool, **args):
        if tool == "sandbox.exec":
            return json.dumps({"returncode": 0, "stderr": "", "stdout": 'QUAERA_EXPLORE {"checked": "n ≤ 50", '
                               '"counterexample": {"values": {"n": 2}, "detail": "sum is 4"}, "observations": []}'})
        return super().call(role, tool, **args)


def test_candidate_counterexample_triggers_refutation_before_proof(tmp_path):
    script, order = Script(critic_objects_first=False), []

    def spy(system, prompt):
        if system in (prompts.REFUTE, prompts.PROVE):
            order.append("REFUTE" if system == prompts.REFUTE else "PROVE")
            if system == prompts.REFUTE:
                assert "candidate counterexample" in prompt and '"n": 2' in prompt
        return script(system, prompt)
    orch = make(tmp_path, spy)
    orch.tools = CounterexampleTools(orch.permissions)
    orch.run()
    assert order[0] == "REFUTE"          # with a candidate counterexample, refutation is tried first (fails on the fake Lean, then the proof)


def test_ml_data_profile_reaches_hypothesis_agent(tmp_path):
    script, seen = MLScript(plan_objection=False, broken_first=False), []

    def spy(system, prompt):
        if system == prompts.HYPOTHESIS_ML:
            seen.append(prompt)
        return script(system, prompt)
    spy.n = script.n
    orch = make_ml(tmp_path, spy)
    orch.run()
    assert orch.state("data_profile") is not None and "files" in orch.state("data_profile")
    assert "Data profile" in seen[0]
