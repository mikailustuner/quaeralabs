"""Multi-provider gateway: the LiteLLM adapter (no network, mock) and the cross-model rule."""

import pytest

litellm = pytest.importorskip("litellm")

from quaera.gateway import BudgetExceeded, Gateway, LiteLLMProvider, ScriptedProvider  # noqa: E402
from quaera.permissions import Permissions  # noqa: E402

MODELS = {"cheap": "openai/gpt-4o-mini", "balanced": "openai/gpt-4o-mini", "best": "openai/gpt-4o-mini"}


def test_litellm_provider_reports_cost_and_family():
    p = LiteLLMProvider(MODELS, mock_response='{"ok": true}')
    c = p.complete("openai/gpt-4o-mini", "system", "question", 100, budget_usd=1.0)
    assert c.text == '{"ok": true}' and c.family == "openai" and c.cost_usd >= 0


def test_unknown_price_is_refused_before_calling():
    p = LiteLLMProvider({"cheap": "openai/unknown-model-xyz", "balanced": "openai/unknown-model-xyz",
                         "best": "openai/unknown-model-xyz"}, mock_response="x")
    gw = Gateway({"openai": p}, 5.0, Permissions.load().agents)
    with pytest.raises(BudgetExceeded):
        gw.call("literature", "s", "p", 100)


def test_cross_model_rule_routes_critic_to_other_family():
    perms = Permissions.load()
    gw = Gateway({"anthropic": ScriptedProvider(lambda s, p: "{}", "anthropic"),
                  "openai": LiteLLMProvider(MODELS, mock_response="{}")}, 5.0, perms.agents)
    eng, cross_e = gw.call("engineer", "s", "p", 100)
    crit, cross_c = gw.call("critic", "s", "p", 100)
    ver, cross_v = gw.call("verifier", "s", "p", 100)
    assert eng.family == "anthropic" and not cross_e
    assert crit.family == "openai" and cross_c
    assert ver.family == "openai" and cross_v


def test_single_family_reports_no_cross_model():
    perms = Permissions.load()
    gw = Gateway({"anthropic": ScriptedProvider(lambda s, p: "{}", "anthropic")}, 5.0, perms.agents)
    gw.call("engineer", "s", "p", 100)
    crit, cross = gw.call("critic", "s", "p", 100)
    assert crit.family == "anthropic" and cross is False


def test_effort_per_role_reaches_provider_and_raises_estimate():
    from quaera.gateway import EFFORT_FACTOR, Gateway, ScriptedProvider, worst_case_cost
    from quaera.permissions import Permissions
    perms = Permissions.load()
    sp = ScriptedProvider(lambda s, p: "ok")
    gw = Gateway({"scripted": sp}, 5.0, perms.agents)
    gw.call("engineer", "system", "question", 1000)
    gw.call("writer", "system", "question", 1000)
    assert [c["effort"] for c in sp.calls] == ["high", None]          # agents/engineer.yaml: high; writer: default
    gw2 = Gateway({"scripted": ScriptedProvider(lambda s, p: "x")}, 5.0, perms.agents, effort_overrides={"engineer": "default"})
    gw2.call("engineer", "system", "question", 1000)
    assert gw2.providers["scripted"].calls[0]["effort"] is None
    assert EFFORT_FACTOR["high"] > 1 and worst_case_cost("scripted", "s", "p", 1000) >= 0


def test_parallel_calls_never_exceed_cap():
    """Worst case: the provider spends the whole limit it is given on every call; 8 calls at once."""
    import threading
    import time
    from quaera.gateway import BudgetExceeded, Gateway, ScriptedProvider
    from quaera.permissions import Permissions

    def slow(system, prompt):
        time.sleep(0.05)
        return "ok"
    gw = Gateway({"scripted": ScriptedProvider(slow, cost_per_call=1000.0)}, 1.0, Permissions.load().agents)
    results = []

    def worker():
        try:
            gw.call("writer", "s", "p", 100)
            results.append("ok")
        except BudgetExceeded:
            results.append("blocked")
    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert gw.spent_usd <= 1.0 + 1e-9 and gw.reserved_usd == 0
    assert "ok" in results


def test_parse_json_prefers_the_object_over_brackets_in_prose():
    """The model may write plain text before the JSON with square brackets like [0, 1] in it (happened in a real discovery run)."""
    from quaera.gateway import parse_json
    text = 'For t in [0, 1] the bound holds, so:\n{"lemmas": [{"id": "L1"}], "assembly": "x"}\nDone.'
    assert parse_json(text) == {"lemmas": [{"id": "L1"}], "assembly": "x"}
    assert parse_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert parse_json('[{"index": 0}]') == [{"index": 0}]


def test_ask_json_retries_when_a_list_comes_back(tmp_path):
    from test_orchestrator import Script, make
    answers = iter(['[1, 2, 3]', '{"ok": true}'])
    orch = make(tmp_path, lambda s, p: next(answers))
    out, _ = orch.ask_json("hypothesis", "sys", "prompt", 500)
    assert out == {"ok": True}
