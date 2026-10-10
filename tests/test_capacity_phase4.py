"""Capacity plan Phase 4: ML grids as branches (ML1), result bank and code reuse (ML2), the remote GPU runner (ML3) and
observability (O1). No model and no network; the runner is exercised with a local shell standing in for ssh."""

import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from quaera import tree
from quaera.bank import ResultBank, data_hash
from quaera.gateway import Gateway, ModelError, ScriptedProvider
from quaera.ml_loop import MLOrchestrator
from quaera.observability import metrics
from quaera.orchestrator import AutoApprover
from quaera.permissions import Permissions
from quaera.store import Store

from test_ml_loop import MLScript, SandboxTools
from test_orchestrator import Script, make
from test_tree import always_failing_proof

AGENTS = Permissions.load().agents


def make_ml(home: Path, name: str, data: Path, script=None):
    perms = Permissions.load()
    store = Store(home / name / "quaera.db", perms)
    store.set_meta("title", "XOR"); store.set_meta("domain", "ml"); store.set_meta("dataDir", str(data))
    sp = ScriptedProvider(script or MLScript())
    orch = MLOrchestrator(store, Gateway({"scripted": sp}, 5.0, perms.agents), SandboxTools(perms), AutoApprover(100), perms,
                          log=lambda m: None, reports_dir=home / name)
    if not store.latest("question"):
        store.put({"type": "question", "createdBy": orch.human(), "title": "Can a linear model exceed 75%?", "domain": "ml",
                   "scope": "/data/train.npz"})
    return orch, sp


# --- ML2 ------------------------------------------------------------------------------------------------

def test_reproduced_results_are_banked_and_recalled_on_the_same_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "train.csv").write_text("x,y\n1,0\n", encoding="utf-8")
    bank = ResultBank(tmp_path / "bank.db")
    first, _ = make_ml(tmp_path, "first", data)
    first.results = bank
    first.run()
    assert first.store.events("results.added") and first.state("data_hash") == data_hash(data)
    second, sp = make_ml(tmp_path, "second", data)
    second.results = bank
    second.run()
    design = next(c["prompt"] for c in sp.calls if "Verified results from earlier projects" in c["prompt"])
    assert "SAME DATA as this project" in design and second.store.events("results.recalled")
    assert data_hash(data) != data_hash(tmp_path)          # a different directory has a different fingerprint


def test_a_branch_starts_from_the_parents_script(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    root, _ = make_ml(tmp_path, "root", data)
    root.run()
    child = tree.branch_project(tmp_path, "root", "approach", "Try more seeds.", note="Use 5 seeds.", by={"kind": "human", "userId": "u"})
    orch, sp = make_ml(tmp_path, child, data)
    orch.run()
    assert orch.store.events("code.reused") and any("Starting point: the parent branch's experiment.py" in c["prompt"] for c in sp.calls)


# --- ML1 ------------------------------------------------------------------------------------------------

def test_grid_decision_opens_one_branch_per_value(tmp_path):
    root = make(tmp_path, always_failing_proof(), name="root")
    root.run()
    root.store.set_meta("domain", "ml")          # the grid is an ML decision; the branches themselves run the scripted loop
    grid = {"decision": "grid", "parameter": "learning rate", "values": ["1e-3", "1e-2", "1e-1"], "reason": "Tune the step size."}
    created = tree.iterate(tmp_path, "root", build=lambda path, b: make(tmp_path, Script(), name=path.name),
                           providers={"scripted": ScriptedProvider(lambda s, p: json.dumps(grid))}, agent_specs=AGENTS,
                           approve=lambda t, c: True, max_branches=3, budget_per_branch=3.0, log=lambda m: None)
    assert len(created) == 3
    cells = [Store(tmp_path / c / "quaera.db").meta("grid") for c in created]
    assert sorted(c["value"] for c in cells) == ["1e-1", "1e-2", "1e-3"] and all(c["of"] == 3 for c in cells)


def test_metric_gain_respects_the_metric_direction(tmp_path, monkeypatch):
    metric = {"root": {"name": "loss", "mean": 2.0}, "child": {"name": "loss", "mean": 1.0}}
    monkeypatch.setattr(tree, "lineage", lambda home, pid: ["root"] if pid == "root" else ["root", pid])
    monkeypatch.setattr(tree, "node_summary", lambda home, pid: {"metric": metric[pid]})
    monkeypatch.setattr(tree, "metric_direction", lambda home, pid, name: "lower_is_better")
    assert tree.metric_gain(tmp_path, "child") == 0.5        # loss halved: a gain, because lower is better
    monkeypatch.setattr(tree, "metric_direction", lambda home, pid, name: "higher_is_better")
    assert tree.metric_gain(tmp_path, "child") == -0.5
    assert tree.metric_gain(tmp_path, "root") == 0.0


def test_metric_direction_is_read_from_the_plan(tmp_path):
    orch, _ = make_ml(tmp_path, "plan", tmp_path)
    orch.run()
    plan = orch.store.latest("experiment")[-1]
    name = plan["metrics"][0]["name"]
    assert tree.metric_direction(tmp_path, "plan", name) == plan["metrics"][0]["direction"]
    assert tree.metric_direction(tmp_path, "plan", "unknown") == "higher_is_better"


# --- ML3 ------------------------------------------------------------------------------------------------

def test_remote_runner_syncs_runs_and_cleans_up(tmp_path, monkeypatch):
    from quaera.mcp_servers import sandbox_server as sb
    remote = tmp_path / "remote"
    fake = tmp_path / "fake-quaera"
    fake.write_text("#!/usr/bin/env python3\nimport json, sys, pathlib\nargs = sys.argv[1:]\nwork = args[args.index('--workdir') + 1]\n"
                    "data = args[args.index('--data-dir') + 1] if '--data-dir' in args else None\n"
                    "pathlib.Path(work, 'result.txt').write_text('trained on ' + str(sorted(p.name for p in pathlib.Path(data).iterdir())))\n"
                    "print(json.dumps({'returncode': 0, 'stdout': 'QUAERA_METRICS {\"acc\": 0.9}', 'stderr': '', 'timed_out': False,"
                    " 'gpu': '--gpu' in args, 'cmd': args[args.index('--') + 1:]}))\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("QUAERA_REMOTE_RUNNER", "sh -c")       # a local shell stands in for `ssh host`
    monkeypatch.setenv("QUAERA_REMOTE_PREFIX", "")
    monkeypatch.setenv("QUAERA_REMOTE_DIR", str(remote))
    monkeypatch.setenv("QUAERA_REMOTE_QUAERA", str(fake))
    work, data = tmp_path / "work", tmp_path / "data"
    work.mkdir(); data.mkdir()
    (work / "experiment.py").write_text("print(1)\n")
    (data / "train.npz").write_bytes(b"x")
    res = json.loads(sb.sandbox_exec(str(work), ["python", "experiment.py", "--seed", "0"], str(data), 60, gpu=True))
    assert res["returncode"] == 0 and res["gpu"] and res["cmd"] == ["python", "experiment.py", "--seed", "0"] and res["runner"] == "-c"
    assert (work / "result.txt").read_text() == "trained on ['train.npz']"          # synced back
    assert not any(remote.iterdir())                                                # remote job removed
    monkeypatch.setenv("QUAERA_REMOTE_RUNNER", "false")
    broken = json.loads(sb.sandbox_exec(str(work), ["python", "x.py"], None, 30, gpu=True))
    assert broken["returncode"] == -1 and "remote runner" in broken["stderr"]


def test_gpu_project_shows_where_it_runs(tmp_path, monkeypatch):
    orch, _ = make_ml(tmp_path, "gpu", tmp_path)
    assert orch.compute_label() == "local CPU"
    orch.store.set_meta("gpu", True)
    monkeypatch.setenv("QUAERA_REMOTE_RUNNER", "ssh gpu-box")
    assert orch.compute_label().startswith("remote GPU runner gpu-box")


# --- O1 -------------------------------------------------------------------------------------------------

def test_metrics_per_stage_and_provider_and_the_package_carries_them(tmp_path, monkeypatch):
    from quaera import package
    monkeypatch.setattr(package, "KEY_PATH", tmp_path / "key")
    orch = make(tmp_path, Script(), name="m", cost=0.001)
    orch.run()
    m = metrics(orch.store)
    stages = {s["stage"] for s in m["stages"]}
    assert {"literature", "prove"} <= stages and m["totals"]["calls"] > 5 and m["providers"][0]["provider"] == "scripted"
    assert abs(m["totals"]["usd"] - orch.gateway.spent_usd) < 1e-6
    blob = package.build_package(tmp_path / "m")
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert json.loads(z.read("observability.json"))["totals"]["calls"] == m["totals"]["calls"]
    assert package.verify_package(blob) == []                    # the file is covered by the signed manifest


def test_failing_provider_raises_one_alert():
    events = []

    def broken(s, p):
        raise ModelError("503 upstream")
    gw = Gateway({"flaky": ScriptedProvider(broken, "flaky")}, 5.0, AGENTS, lambda k, p: events.append((k, p)))
    for _ in range(6):
        with pytest.raises(ModelError):
            gw.call("writer", "s", "p", 10)
    alerts = [p for k, p in events if k == "provider.alert"]
    assert len(alerts) == 1 and alerts[0]["provider"] == "flaky" and alerts[0]["errorRate"] == 1.0
