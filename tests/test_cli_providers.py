"""Codex CLI ve OpenCode CLI sağlayıcıları: tespit, çıktı ayrıştırma, hata, çapraz aile yönlendirmesi.
Gerçek CLI'ler çağrılmaz: PATH'e konan sahte ikililer kullanılır (para ve kota harcanmaz)."""

import json
import os
import stat

import pytest

from quaera import providers as P
from quaera.cli import make_providers
from quaera.gateway import Gateway, ModelError
from quaera.permissions import Permissions


def fake_bin(dirpath, name, body):
    path = dirpath / name
    path.write_text("#!/usr/bin/env python3\nimport sys, json\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


CODEX = '''
if "--version" in sys.argv: print("codex-cli 9.9.9"); sys.exit(0)
prompt = sys.stdin.read()
assert "--sandbox" in sys.argv and "read-only" in sys.argv and "--ephemeral" in sys.argv
eff = [a for a in sys.argv if a.startswith("model_reasoning_effort")]
print(json.dumps({"type": "thread.started"}))
if "FAIL" in prompt:
    print(json.dumps({"type": "turn.failed", "error": {"message": "rate limited"}})); sys.exit(1)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "codex says 51 " + eff[0]}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7, "reasoning_output_tokens": 3}}))
'''
OPENCODE = '''
if "--version" in sys.argv: print("opencode v9"); sys.exit(0)
args = sys.argv
task = open(args[args.index("-f") + 1]).read()
assert "--agent" in args and args[args.index("--agent") + 1] == "plan"
print(json.dumps({"type": "step_start", "part": {"type": "step-start"}}))
print(json.dumps({"type": "text", "part": {"type": "text", "text": "opencode read: " + ("TASK-OK" if "=== TASK ===" in task else "?") + " 51"}}))
'''


@pytest.fixture()
def fakes(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    fake_bin(b, "codex", CODEX)
    fake_bin(b, "opencode", OPENCODE)
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("QUAERA_PROVIDERS", raising=False)
    monkeypatch.delenv("QUAERA_OPENCODE_MODEL", raising=False)
    return b


def test_detect_finds_codex_and_opencode(fakes):
    found = {d["id"]: d for d in P.detect()}
    assert found["codex"]["ready"] and found["codex"]["version"] == "codex-cli 9.9.9" and found["codex"]["family"] == "openai"
    assert found["opencode"]["ready"] and found["opencode"]["family"] == "opencode"


def test_codex_needs_login(fakes, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nobody"))
    codex = next(d for d in P.detect() if d["id"] == "codex")
    assert not codex["ready"] and "codex login" in codex["note"]


def test_codex_provider_parses_answer_usage_and_effort(fakes):
    p = P.CodexCLIProvider("codex")
    c = p.complete(p.model_for("best"), "sys", "What is 17*3?", 500, 0.1)
    assert c.text.startswith("codex says 51") and 'model_reasoning_effort="high"' in c.text
    assert c.family == "openai" and c.cost_usd == 0.0 and c.output_tokens == 10
    with pytest.raises(ModelError, match="rate limited"):
        p.complete(p.model_for("cheap"), "sys", "FAIL please", 500, 0.1)


def test_opencode_provider_passes_prompt_as_file(fakes):
    p = P.OpenCodeCLIProvider("opencode")
    c = p.complete(p.model_for("cheap"), "sys", "task text", 500, 0.1)
    assert c.text == "opencode read: TASK-OK 51" and c.family == "opencode"


def test_opencode_family_follows_model_prefix(monkeypatch):
    monkeypatch.setenv("QUAERA_OPENCODE_MODEL", "openai/gpt-5")
    assert P.OpenCodeCLIProvider("opencode").family == "openai"


def test_make_providers_orders_families_and_respects_selection(fakes, monkeypatch):
    all_ = make_providers()
    assert "openai" in all_ and "opencode" in all_
    assert list(make_providers(["openai", "opencode"])) == ["openai", "opencode"]
    monkeypatch.setenv("QUAERA_PROVIDERS", "codex")
    assert "opencode" not in make_providers()


def test_critic_is_routed_to_a_different_family_and_billing_recorded(fakes):
    perms = Permissions.load()
    rec = []
    from quaera.gateway import ScriptedProvider
    gw = Gateway({"anthropic": ScriptedProvider(lambda s, p: "ok", "anthropic"), "openai": P.CodexCLIProvider("codex")},
                 1.0, perms.agents, lambda k, p: rec.append((k, p)))
    gw.call("engineer", "s", "p", 100)
    c, cross = gw.call("critic", "s", "review 17*3", 100)
    assert c.family == "openai" and cross
    call = [p for k, p in rec if k == "model.call"][-1]
    assert call["billing"] == "subscription" and call["costUsd"] == 0.0


def test_probe_reports_ok(fakes):
    r = P.probe(P.CodexCLIProvider("codex"))
    assert r["ok"] and r["family"] == "openai"
