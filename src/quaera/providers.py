"""Extra model providers: the user's installed Codex CLI and OpenCode CLI tools (ADR 0017).

The goal is cross-checking: the Critic and the Verifier can run on a different model family than the Engineer, and
idea generation in Discovery mode can run in parallel across several families.

Security: every call runs in an empty, temporary directory.
- Codex is called with `--sandbox read-only --ephemeral`: it cannot write files and keeps no session log.
- OpenCode is called with the read-only `plan` agent.
Only a text answer is taken from the model; QuaeraLabs tools (Lean, sandbox) still go through their own permission layer.

Cost: both tools run on the user's subscription or on free models; they report no per-call charge.
The event log records costUsd=0 and `billing: "subscription"`. The budget cap limits only metered (API) spending;
the number of calls to these providers is limited by the number of discovery rounds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .gateway import Completion, ModelError

# Cost profile -> Codex reasoning effort (the model is the one in the user's Codex config, or QUAERA_CODEX_MODEL).
CODEX_EFFORT = {"cheap": "low", "balanced": "medium", "best": "high"}
EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
NO_TOOLS = ("You are being called as a plain text model by the QuaeraLabs research system. Do not run commands, "
            "do not read or edit files, do not browse. Answer directly from your own knowledge and the text below.")


def _workdir() -> str:
    return tempfile.mkdtemp(prefix="quaera-cli-")


class CodexCLIProvider:
    """OpenAI models through the user's logged-in `codex` CLI (`codex exec --json`)."""

    family = "openai"
    billing = "subscription"

    def __init__(self, binary: str = "codex", model: str | None = None, timeout_s: int = 900):
        self.binary, self.model, self.timeout_s = binary, model or os.environ.get("QUAERA_CODEX_MODEL") or None, timeout_s

    def model_for(self, profile: str) -> str:
        return f"{self.model or 'default'}@{CODEX_EFFORT.get(profile, 'medium')}"

    def price(self, model: str) -> tuple[float, float]:
        return (0.0, 0.0)

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        name, _, prof_effort = model.partition("@")
        eff = EFFORT_MAP.get(effort or "", prof_effort or "medium")
        cwd = _workdir()
        cmd = [self.binary, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
               "-C", cwd, "-c", f'model_reasoning_effort="{eff}"'] + (["-m", name] if name != "default" else []) + ["-"]
        text_in = f"{NO_TOOLS}\n\n=== SYSTEM ===\n{system}\n\n=== TASK ===\n{prompt}\n\n(Keep the answer under about {max_output_tokens} tokens.)"
        try:
            proc = subprocess.run(cmd, input=text_in, capture_output=True, text=True, timeout=self.timeout_s, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise ModelError(f"codex did not finish within {self.timeout_s} s") from exc
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        return parse_codex(proc.stdout, proc.stderr, name)


def parse_codex(stdout: str, stderr: str, model: str) -> Completion:
    text, usage, error = "", {}, None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text", "")
        elif ev.get("type") == "turn.completed":
            usage = ev.get("usage") or {}
        elif ev.get("type") in ("error", "turn.failed"):
            error = (ev.get("message") or (ev.get("error") or {}).get("message") or json.dumps(ev))[:300]
    if error or not text:
        raise ModelError(f"codex error: {error or (stderr.strip()[-300:] or 'empty answer')}")
    return Completion(text=text, model=f"openai/{model if model != 'default' else 'codex-default'}", family="openai",
                      cost_usd=0.0, input_tokens=int(usage.get("input_tokens", 0)),
                      output_tokens=int(usage.get("output_tokens", 0)) + int(usage.get("reasoning_output_tokens", 0)))


class OpenCodeCLIProvider:
    """Any model configured in the OpenCode CLI (`opencode run --format json`).

    The family is derived from the model's provider prefix (e.g. `opencode/big-pickle` → `opencode`); without a model,
    OpenCode's default is used and the family counts as `opencode`.
    """

    billing = "subscription"

    def __init__(self, binary: str = "opencode", model: str | None = None, timeout_s: int = 900):
        self.binary, self.timeout_s = binary, timeout_s
        self.model = model or os.environ.get("QUAERA_OPENCODE_MODEL") or None
        prefix = (self.model or "opencode/").split("/", 1)[0]
        # If an OpenAI or Anthropic model is chosen, that is the family: so the cross-check really goes to a different family.
        self.family = {"openai": "openai", "anthropic": "anthropic"}.get(prefix, "opencode")

    def model_for(self, profile: str) -> str:
        return self.model or "default"

    def price(self, model: str) -> tuple[float, float]:
        return (0.0, 0.0)

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        cwd = _workdir()
        try:
            task = Path(cwd) / "task.md"
            task.write_text(f"=== SYSTEM ===\n{system}\n\n=== TASK ===\n{prompt}\n", encoding="utf-8")
            msg = (f"{NO_TOOLS} The full instructions are in the attached file task.md; follow them exactly. "
                   f"Keep the answer under about {max_output_tokens} tokens.")
            cmd = [self.binary, "run", "--format", "json", "--agent", "plan", "-f", str(task)] + \
                  (["-m", model] if model != "default" else []) + ["--", msg]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise ModelError(f"opencode did not finish within {self.timeout_s} s") from exc
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        return parse_opencode(proc.stdout, proc.stderr, model, self.family)


def parse_opencode(stdout: str, stderr: str, model: str, family: str) -> Completion:
    parts: dict[str, str] = {}
    error, tokens = None, {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = ev.get("part") or {}
        if ev.get("type") == "text" and part.get("text"):
            parts[part.get("id") or str(len(parts))] = part["text"]   # an update of the same part replaces the earlier one
        elif ev.get("type") == "error":
            error = json.dumps(ev.get("error") or part or ev)[:300]
        elif ev.get("type") == "step_finish" and isinstance(part.get("tokens"), dict):
            tokens = part["tokens"]
    text = "\n\n".join(parts.values()).strip()
    if not text:
        raise ModelError(f"opencode error: {error or stderr.strip()[-300:] or 'empty answer'}")
    return Completion(text=text, model=f"opencode/{model}" if not model.startswith(("opencode/", "openai/", "anthropic/")) and model != "default"
                      else (model if model != "default" else "opencode/default"),
                      family=family, cost_usd=0.0, input_tokens=int(tokens.get("input", 0) or 0),
                      output_tokens=int(tokens.get("output", 0) or 0))


class AntigravityCLIProvider:
    """Google models through the signed-in Antigravity CLI.

    The prompt goes over stdin as one stream-json `user` message (no argument-length limit, and the model never has
    to open a file), in an empty working directory; `--mode plan` and `--sandbox` keep the agent read-only.
    """

    family = "google"
    billing = "subscription"

    def __init__(self, binary: str = "agy", model: str | None = None, timeout_s: int = 900):
        self.binary, self.timeout_s = binary, timeout_s
        self.model = model or os.environ.get("QUAERA_AGY_MODEL") or None

    def model_for(self, profile: str) -> str:
        return f"{self.model or 'default'}@{CODEX_EFFORT.get(profile, 'medium')}"

    def price(self, model: str) -> tuple[float, float]:
        return (0.0, 0.0)

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        name, _, prof_effort = model.partition("@")
        eff = EFFORT_MAP.get(effort or "", prof_effort or "medium")
        text = (f"{NO_TOOLS}\n\n=== SYSTEM ===\n{system}\n\n=== TASK ===\n{prompt}\n\n"
                f"Keep the answer under about {max_output_tokens} tokens.")
        message = json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False) + "\n"
        cmd = [self.binary, "--input-format", "stream-json", "--output-format", "stream-json", "--mode", "plan",
               "--sandbox", "--effort", eff, "--print-timeout", f"{self.timeout_s}s"] + (["--model", name] if name != "default" else [])
        cwd = _workdir()
        try:
            proc = subprocess.run(cmd, input=message, capture_output=True, text=True, timeout=self.timeout_s + 30, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise ModelError(f"agy did not finish within {self.timeout_s} s") from exc
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        return parse_agy(proc.stdout, proc.stderr, name)


def parse_agy(stdout: str, stderr: str, model: str) -> Completion:
    """Reads the final `result` event of a stream-json run (same shape as the `--output-format json` envelope)."""
    env: dict = {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("event") == "result":
            env = ev.get("result") or {}
    text = (env.get("response") or "").strip()
    if env.get("status") != "SUCCESS" or not text:
        raise ModelError(f"agy error: {env.get('error') or env.get('status') or stderr.strip()[-300:] or 'empty answer'}")
    usage = env.get("usage") or {}
    return Completion(text=text, model=f"agy/{model}", family="google", cost_usd=0.0,
                      input_tokens=int(usage.get("input_tokens", 0) or 0),
                      output_tokens=int(usage.get("output_tokens", 0) or 0))   # already includes thinking tokens


# --- detection -------------------------------------------------------------------------------

def _version(binary: str) -> str | None:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (out.stdout or out.stderr).strip().splitlines()[0][:80] if out.returncode == 0 else None


def detect() -> list[dict]:
    """Model CLIs on this machine: name, family, version, whether ready and why. Makes no model call (free, fast)."""
    found = []
    claude = shutil.which("claude")
    found.append({"id": "claude", "name": "Claude Code CLI", "family": "anthropic", "binary": claude,
                  "version": _version(claude) if claude else None, "billing": "metered (budget-capped)",
                  "ready": bool(claude), "note": "" if claude else "install Claude Code and log in"})
    codex = shutil.which(os.environ.get("QUAERA_CODEX_BIN", "codex"))
    codex_auth = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
    found.append({"id": "codex", "name": "Codex CLI", "family": "openai", "binary": codex,
                  "version": _version(codex) if codex else None, "billing": "subscription (no per-call charge reported)",
                  "model": os.environ.get("QUAERA_CODEX_MODEL") or "Codex default",
                  "ready": bool(codex) and codex_auth.exists(),
                  "note": "" if codex and codex_auth.exists() else ("run `codex login`" if codex else "not installed")})
    oc = shutil.which(os.environ.get("QUAERA_OPENCODE_BIN", "opencode"))
    oc_family = OpenCodeCLIProvider(oc or "opencode").family
    found.append({"id": "opencode", "name": "OpenCode CLI", "family": oc_family, "binary": oc,
                  "version": _version(oc) if oc else None, "billing": "subscription / free models (no per-call charge reported)",
                  "model": os.environ.get("QUAERA_OPENCODE_MODEL") or "OpenCode default", "ready": bool(oc),
                  "note": "" if oc else "not installed"})
    agy = shutil.which(os.environ.get("QUAERA_AGY_BIN", "agy"))
    found.append({"id": "agy", "name": "Antigravity CLI", "family": "google", "binary": agy,
                  "version": _version(agy) if agy else None, "billing": "subscription (no per-call charge reported)",
                  "model": os.environ.get("QUAERA_AGY_MODEL") or "Antigravity default", "ready": bool(agy),
                  "note": "" if agy else "not installed"})
    return found


def enabled_ids() -> set[str] | None:
    """Selected with QUAERA_PROVIDERS=claude,codex,opencode; if unset or 'auto', all ready ones."""
    raw = os.environ.get("QUAERA_PROVIDERS", "auto").strip().lower()
    return None if raw in ("", "auto") else {x.strip() for x in raw.split(",") if x.strip()}


def build_cli_providers(only: set[str] | None = None) -> dict:
    """Ready CLI providers keyed by family. A second provider of the same family is not added (cross-checks are per family)."""
    from .gateway import ClaudeCLIProvider
    allow = only if only is not None else enabled_ids()
    out: dict = {}
    for d in detect():
        if not d["ready"] or (allow is not None and d["id"] not in allow) or d["family"] in out:
            continue
        if d["id"] == "claude":
            out["anthropic"] = ClaudeCLIProvider()
        elif d["id"] == "codex":
            out["openai"] = CodexCLIProvider(d["binary"])
        elif d["id"] == "opencode":
            p = OpenCodeCLIProvider(d["binary"])
            out[p.family] = p
        elif d["id"] == "agy":
            out["google"] = AntigravityCLIProvider(d["binary"])
    return out


def probe(provider, profile: str = "cheap") -> dict:
    """Tests the provider with a real but small call (the "Test" button on the settings page)."""
    started = time.monotonic()
    model = provider.model_for(profile) if hasattr(provider, "model_for") else "haiku"
    try:
        c = provider.complete(model, "You are a test.", "What is 17 * 3? Reply with just the number.", 2000, 0.05)
    except ModelError as exc:
        return {"ok": False, "error": str(exc)[:300], "seconds": round(time.monotonic() - started, 1)}
    return {"ok": "51" in c.text, "answer": c.text.strip()[:80], "model": c.model, "family": c.family,
            "costUsd": c.cost_usd, "seconds": round(time.monotonic() - started, 1)}
