"""Improvement plan phases 5 and 6 (2026-10-20): `quaera critique` (E1), the fast path and time to
result (C4), the measurement switches of C2, the package verifier in Python and in the browser (E3). No model calls."""

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from quaera import critique, package, prompts
from quaera.prover import apply_suggestions, prove_search

from test_orchestrator import Script, make
from test_server import finished_project, home  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


# --- E1: critique ---------------------------------------------------------------------------------------------------------

def experiment(tmp_path):
    d = tmp_path / "exp"
    (d / "data").mkdir(parents=True)
    (d / ".venv").mkdir()
    (d / "train.py").write_text("import numpy as np\nX = np.load('data/x.npy')\nscaler.fit(X)  # before the split\n")
    (d / "results.json").write_text(json.dumps({"accuracy": 0.97, "seeds": 1}))
    (d / "prereg.md").write_text("Primary metric: F1. Five seeds.\n")
    (d / "data" / "x.npy").write_bytes(b"\x93NUMPY")
    (d / ".venv" / "x.py").write_text("print(1)\n")
    (d / "plot.png").write_bytes(b"\x89PNG")
    return d


def test_collect_numbers_lines_and_reads_plans_and_results_first(tmp_path):
    d = experiment(tmp_path)
    text, read, skipped = critique.collect(d, prereg=d / "prereg.md", results=d / "results.json")
    assert read == ["prereg.md", "results.json", "train.py"]                  # data/ and .venv/ are not read
    assert "=== prereg.md (pre-registration)" in text and "   3| scaler.fit(X)  # before the split" in text
    assert "plot.png (not a text file)" in skipped


def test_review_uses_the_measured_prompt_and_a_second_family_for_blocking():
    seen = []

    def first(system, prompt, n):
        seen.append(system)
        return json.dumps({"flawed": True, "categories": ["leakage", "made_up"], "severity": "blocking",
                           "evidence": "train.py:3", "summary": "s"}), "anthropic/x"
    out = critique.review("report", first, lambda s, p, n: ('{"flawed": true, "severity": "high"}', "openai/y"))
    assert seen == [prompts.CRITIC_EXPERIMENT] and out["categories"] == ["leakage"]
    assert out["severity"] == "high" and "did not confirm" in out["note"] and out["models"] == ["anthropic/x", "openai/y"]
    confirmed = critique.review("report", first, lambda s, p, n: ('{"flawed": true, "severity": "blocking"}', "openai/y"))
    assert confirmed["severity"] == "blocking" and "note" not in confirmed


def test_critique_command(tmp_path, monkeypatch, capsys):
    import quaera.cli as cli
    from quaera.gateway import ScriptedProvider
    reply = '{"flawed": true, "categories": ["leakage"], "severity": "high", "evidence": "train.py:3", "summary": "Leak."}'
    monkeypatch.setattr(cli, "make_providers", lambda families=None: {"scripted": ScriptedProvider(lambda s, p: reply)})
    d = experiment(tmp_path)
    assert cli.main(["critique", str(d), "--budget", "0.1"]) == 0
    md = capsys.readouterr().out
    assert "**objection** · severity `high` · leakage" in md and "- train.py" in md and "nothing was executed" in md
    assert cli.main(["critique", str(d), "--budget", "0.1", "--fail-on", "high", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["severity"] == "high"


# --- C4: fast path and time to result -------------------------------------------------------------------------------------

def test_known_results_skip_the_numerical_exploration(tmp_path):
    script = Script(critic_objects_first=False)
    orch = make(tmp_path, script)
    report = open(orch.run(), encoding="utf-8").read()
    assert "EXPLORE_MATH" not in script.n and orch.store.events("fastpath")
    assert any(e["payload"] == {"stage": "explore", "skipped": "fast path"} for e in orch.store.events("stage.done"))
    assert "- Time to result:" in report and "- Fast path: the literature step found the result already known" in report


# --- C2: measurement switches ---------------------------------------------------------------------------------------------

def test_apply_suggestions_reach_later_rounds():
    out = "info:1: Try this: refine Nat.le_trans ?_ ?_\ninfo:1: Try this: exact foo sorry\ninfo:2: Try this: refine Nat.le_trans ?_ ?_"
    assert apply_suggestions(out) == "- refine Nat.le_trans ?_ ?_"
    prompts_seen, asked = [], []

    def ask(system, prompt, n, lane=None):
        prompts_seen.append(prompt)
        return "```lean\ntheorem t : 1 = 2 := by\n  rfl\n```"
    res = prove_search("theorem t : 1 = 2 := by\n  sorry\n", "t", ask, lambda src, *a: {"verified": False, "errors": ["e"]},
                       attempts=1, sketches=0, rounds=2, more=lambda: True, suggest=lambda: asked.append(1) or "- refine Foo.bar",
                       heavy=False)
    assert not res.solved and asked == [1]
    assert "refine Foo.bar" not in prompts_seen[0] and "refine Foo.bar" in prompts_seen[-1]


# --- E3: package verifier -------------------------------------------------------------------------------------------------

def rewrite(data, name, fn):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for n in src.namelist():
            dst.writestr(n, fn(src.read(n)) if n == name else src.read(n))
    return buf.getvalue()


def test_check_package_offline(home):  # noqa: F811
    pid, _ = finished_project(home)
    data = package.build_package(home / pid, approver="test")
    checks = {c["check"]: c["status"] for c in package.check_package(data, online=False)}
    assert checks == {"signature, AI label and file hashes": "ok", "manifest schema": "ok", "citations": "skipped",
                      "Lean proof": "skipped"}
    bad = package.check_package(rewrite(data, "report.md", lambda b: b + b"\nedited"), online=False)
    assert bad[0]["status"] == "fail" and "modified file: report.md" in bad[0]["detail"]
    assert package.check_package(b"not a zip", online=False)[0]["status"] == "fail"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_browser_verifier_agrees_with_python(home, tmp_path):  # noqa: F811
    pid, _ = finished_project(home)
    data = package.build_package(home / pid, approver="test")
    good, bad = tmp_path / "good.zip", tmp_path / "bad.zip"
    good.write_bytes(data)
    bad.write_bytes(rewrite(data, "quaera-manifest.json", lambda b: b.replace(b"QuaeraLabs AI agent team", b"QuaeraLabs agents")))
    script = (f"import {{ verifyPackage }} from {json.dumps(str(ROOT / 'web/verify/verify.js'))};\n"
              "import { readFileSync } from 'node:fs';\n"
              "for (const f of process.argv.slice(1)) console.log(JSON.stringify(await verifyPackage(readFileSync(f))));\n")
    out = subprocess.run(["node", "--input-type=module", "-e", script, str(good), str(bad)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    good_checks, bad_checks = (json.loads(l) for l in out.stdout.splitlines())
    status = lambda cs: {c["check"]: c["status"] for c in cs}  # noqa: E731
    assert status(good_checks) == {"signature": "ok", "AI label": "ok", "file hashes": "ok", "citations and Lean proof": "skipped"}
    assert status(bad_checks)["signature"] == "fail"
