"""Capacity plan M2: the scoreboard fed from gateway records, the deep probe and probe-based adaptation."""

from types import SimpleNamespace

from quaera.capability import ModelStats, adapt
from quaera.gateway import Gateway, ScriptedProvider
from quaera.permissions import Permissions
from quaera.providers import DEEP_PROBE, deep_probe


def test_gateway_records_feed_the_scoreboard(tmp_path):
    stats = ModelStats(tmp_path / "s.db")

    def record(kind, payload):
        if kind == "model.call":
            stats.record_call(payload)
    gw = Gateway({"anthropic": ScriptedProvider(lambda s, p: "x", "anthropic", cost_per_call=0.002)}, 5.0, Permissions.load().agents, record)
    for _ in range(3):
        gw.call("writer", "s", "p", 100)
    stats.record_error({"model": "anthropic/sonnet", "role": "writer"})
    row = next(r for r in stats.board() if r["model"] == "anthropic/sonnet")
    assert row["calls"] == 3 and abs(row["meanUsd"] - 0.002) < 1e-6 and row["errorRate"] == 0.25


def test_deep_probe_counts_what_lean_accepts(tmp_path):
    class P(ScriptedProvider):
        def model_for(self, profile):
            return "tiny"
    answers = iter(["```lean\nimport Mathlib\n\ntheorem probe_1 (n : ℕ) : n + 0 = n := by\n  simp\n```"] + ["nope"] * 4 + ['{"answer": 51}'])
    prov = P(lambda s, p: next(answers), "local")
    check = lambda src, name, approved: SimpleNamespace(verified="simp" in src)  # noqa: E731
    res = deep_probe(prov, check)
    assert res["provingScore"] == 1 and res["of"] == len(DEEP_PROBE) and res["jsonOk"] and res["model"] == "tiny"
    stats = ModelStats(tmp_path / "s.db")
    stats.set_probe("local/tiny", res["provingScore"], res["of"], res["jsonOk"])
    prof = stats.profile("local/tiny")
    settings, why = adapt(prof)
    assert prof["probe"]["score"] == 1 and settings["interactive_first"] and "deep probe 1/5" in why[0]


def test_models_endpoint(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    import quaera.cli as cli
    import quaera.server as server
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(cli, "HOME", projects)
    monkeypatch.setattr(server, "HOME", projects)
    ModelStats(tmp_path / "stats.db").set_probe("local/tiny", 4, 5, True)
    rows = TestClient(server.create_app()).get("/api/models").json()
    assert rows[0]["model"] == "local/tiny" and rows[0]["probe"]["score"] == 4 and rows[0]["adapts"] == []
