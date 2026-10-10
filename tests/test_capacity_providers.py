"""Capacity plan, Phase 1: provider registry (P1), routing (P2), API accounting and backoff (P3), budget dimensions (P4),
completion cache (C1). No network: LiteLLM runs with mock responses or a patched completion function."""

import json
import os
import stat
import time

import pytest

litellm = pytest.importorskip("litellm")

from quaera import registry  # noqa: E402
from quaera.cache import CompletionCache  # noqa: E402
from quaera.gateway import BudgetExceeded, Gateway, Limits, ScriptedProvider, worst_case_cost  # noqa: E402
from quaera.permissions import Permissions  # noqa: E402

AGENTS = Permissions.load().agents


@pytest.fixture()
def qhome(tmp_path, monkeypatch):
    monkeypatch.setenv("QUAERA_HOME", str(tmp_path / "qh"))
    for k in ("OPENROUTER_API_KEY", "QUAERA_KEY_LOCAL"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "qh"


ENTRY = {"id": "router", "kind": "openrouter", "models": {"cheap": "openai/gpt-4o-mini", "best": "anthropic/claude-x"},
         "price": {"openai/gpt-4o-mini": [0.15, 0.6], "anthropic/claude-x": [3, 15]}}
LOCAL = {"id": "local", "kind": "openai-compatible", "apiBase": "http://127.0.0.1:1/v1", "family": "qwen",
         "models": {"cheap": "qwen3:8b"}, "free": True}


def test_registry_validation_and_round_trip(qhome):
    bad = {"providers": [{"id": "Bad Id", "kind": "x", "models": {}}, {"id": "anthropic", "kind": "openai", "models": {"cheap": "m"}},
                         {"id": "loc", "kind": "openai-compatible", "models": {"cheap": "m"}}],
           "ladders": {"engineer": ["a@huge"]}}
    errors = registry.validate(bad)
    assert any("lowercase" in e for e in errors) and any("reserved" in e for e in errors)
    assert any("apiBase" in e for e in errors) and any("profile must be" in e for e in errors)
    with pytest.raises(ValueError):
        registry.save(bad)
    registry.save({"providers": [ENTRY, LOCAL], "routing": {"critic": "router"}, "ladders": {"engineer": ["local@cheap", "router@best"]}})
    data = registry.load()
    assert [e["id"] for e in data["providers"]] == ["router", "local"] and data["routing"]["critic"] == "router"


def test_secret_is_stored_privately_and_never_described(qhome):
    registry.save({"providers": [ENTRY]})
    assert not registry.has_key(ENTRY) and registry.describe(ENTRY)["ready"] is False
    registry.set_secret("OPENROUTER_API_KEY", "sk-test-123")
    mode = stat.S_IMODE(os.stat(registry.secrets_path()).st_mode)
    assert mode == 0o600
    d = registry.describe(ENTRY)
    assert d["ready"] and d["keySet"] and "sk-test-123" not in json.dumps(d)
    os.environ.pop("OPENROUTER_API_KEY")
    registry.load_secrets()
    assert os.environ["OPENROUTER_API_KEY"] == "sk-test-123"
    os.environ.pop("OPENROUTER_API_KEY")


def test_registry_provider_prices_family_and_free_local(qhome):
    p = registry.build(ENTRY, mock_response="{}")
    assert p.family == "openai" and p.model_for("cheap") == "openrouter/openai/gpt-4o-mini"
    assert p.price("openrouter/openai/gpt-4o-mini") == (0.15, 0.6)
    local = registry.build(LOCAL, mock_response="ok")
    assert local.family == "qwen" and local.price(local.model_for("cheap")) == (0.0, 0.0) and local.billing == "local"
    # a model without a registry price and without a LiteLLM table entry is refused (budget guarantee)
    unknown = registry.build({**LOCAL, "free": False, "models": {"cheap": "mystery-model"}}, mock_response="x")
    gw = Gateway({"local": unknown}, 5.0, AGENTS)
    with pytest.raises(BudgetExceeded):
        gw.call("literature", "s", "p", 100)


def test_api_accounting_is_exact_compared_to_cli():
    cli_estimate = worst_case_cost("x", "s" * 1000, "p" * 1000, 1000, (1.0, 5.0))
    api_estimate = worst_case_cost("x", "s" * 1000, "p" * 1000, 1000, (1.0, 5.0), overhead=0, safety=1.0, cache_write=1.0)
    assert api_estimate < cli_estimate / 2


def test_routing_and_same_family_providers_keep_the_cross_model_rule():
    a1 = ScriptedProvider(lambda s, p: "{}", "anthropic")
    a2 = ScriptedProvider(lambda s, p: "{}", "anthropic")
    o = ScriptedProvider(lambda s, p: "{}", "openai")
    gw = Gateway({"anthropic": a1, "anthropic-api": a2, "router": o}, 5.0, AGENTS,
                 routing={"manager": "anthropic-api", "critic": "anthropic-api"})
    eng, _ = gw.call("engineer", "s", "p", 100)
    man, _ = gw.call("manager", "s", "p", 100)
    crit, cross = gw.call("critic", "s", "p", 100)          # routed to an anthropic route, but must differ from the Engineer
    assert eng.family == "anthropic" and len(a2.calls) >= 1 and man.family == "anthropic"
    assert crit.family == "openai" and cross
    assert gw.alternative("engineer", "anthropic") == "router"     # another family first


def test_ladder_climbs_routes_and_profiles():
    cheap = ScriptedProvider(lambda s, p: "cheap", "local")
    best = ScriptedProvider(lambda s, p: "best", "anthropic")
    gw = Gateway({"local": cheap, "anthropic": best}, 5.0, AGENTS, ladders={"engineer": ["local@cheap", "anthropic@best"]})
    assert gw.call("engineer", "s", "p", 100, rung=0)[0].text == "cheap" and cheap.calls[-1]["model"] == "cheap"
    assert gw.call("engineer", "s", "p", 100, rung=1)[0].text == "best" and best.calls[-1]["model"] == "opus"
    assert gw.call("engineer", "s", "p", 100, rung=9)[0].text == "best"        # the top rung repeats
    assert gw.ladder_step("critic", 0) is None


def test_call_and_time_budgets_bound_free_providers():
    events = []
    gw = Gateway({"scripted": ScriptedProvider(lambda s, p: "x")}, 5.0, AGENTS, lambda k, p: events.append((k, p)),
                 limits=Limits(calls=2))
    gw.call("writer", "s", "p", 10)
    ok = gw.share_cap(1.0)
    assert ok()
    gw.call("writer", "s", "p", 10)
    assert not ok() and "call budget" in gw.exhausted()
    with pytest.raises(BudgetExceeded, match="call budget"):
        gw.call("writer", "s", "p", 10)
    assert any(k == "budget.blocked" and p.get("dimension") for k, p in events)
    late = Gateway({"scripted": ScriptedProvider(lambda s, p: "x")}, 5.0, AGENTS, limits=Limits(deadline=time.time() - 1))
    with pytest.raises(BudgetExceeded, match="time budget"):
        late.call("writer", "s", "p", 10)


def test_completion_cache_never_pays_twice(tmp_path):
    sp = ScriptedProvider(lambda s, p: "answer", "anthropic", cost_per_call=0.01)
    events = []
    gw = Gateway({"anthropic": sp}, 5.0, AGENTS, lambda k, p: events.append((k, p)), cache=CompletionCache(tmp_path / "c.db"))
    first, _ = gw.call("writer", "s", "p", 100)
    second, _ = gw.call("writer", "s", "p", 100)
    third, _ = gw.call("writer", "s", "p", 100, sample=2)           # a best-of-N sample is a different call
    assert first.text == second.text == "answer" and second.cached and second.cost_usd == 0
    assert len(sp.calls) == 2 and abs(gw.spent_usd - 0.02) < 1e-9 and not third.cached
    assert [p.get("cached", False) for k, p in events if k == "model.call"] == [False, True, False]


def test_backoff_retries_rate_limits(monkeypatch):
    p = registry.build({"id": "oa", "kind": "openai", "models": {"cheap": "gpt-4o-mini"}, "price": {"gpt-4o-mini": [1, 1]}})
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    class RateLimitError(Exception):
        pass

    real, n = litellm.completion, {"calls": 0}

    def flaky(**kw):
        n["calls"] += 1
        if n["calls"] < 3:
            raise RateLimitError("429")
        assert kw["api_key"] == "k"
        return real(**{**kw, "mock_response": "fine"})
    monkeypatch.setattr(p.litellm, "completion", flaky)
    c = p.complete("openai/gpt-4o-mini", "s", "q", 50, 1.0)
    assert c.text == "fine" and n["calls"] == 3


def test_build_providers_adds_registry_entries(qhome, monkeypatch):
    from quaera import providers
    monkeypatch.setattr(providers, "build_cli_providers", lambda only=None: {"anthropic": ScriptedProvider(lambda s, p: "x", "anthropic")})
    registry.save({"providers": [LOCAL, {**ENTRY, "enabled": False}]})
    out = providers.build_providers()
    assert set(out) == {"anthropic", "local"} and out["local"].family == "qwen"


def test_server_registry_endpoints_hide_keys(qhome, tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    import quaera.cli as cli
    import quaera.server as server
    monkeypatch.setattr(cli, "HOME", tmp_path / "projects")
    monkeypatch.setattr(server, "HOME", tmp_path / "projects")
    c = TestClient(server.create_app())
    r = c.put("/api/registry", json={"entry": ENTRY, "key": "sk-secret-999"})
    assert r.status_code == 200
    got = c.get("/api/registry").json()
    assert got["providers"][0]["keySet"] and "sk-secret-999" not in json.dumps(got)
    assert c.put("/api/registry", json={"entry": {"id": "x y", "kind": "openai", "models": {}}}).status_code == 400
    assert c.put("/api/registry", json={"routing": {"critic": "router"}}).status_code == 200
    assert registry.load()["routing"] == {"critic": "router"}
    assert c.request("DELETE", "/api/registry/router", json={}).status_code == 200
    assert registry.load() == {"providers": [], "routing": {}, "ladders": {}}
    os.environ.pop("OPENROUTER_API_KEY", None)
