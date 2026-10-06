"""Server with fake models for the UI end-to-end test (spends no money, uses no network).

Usage: uv run python tests/e2e_server.py <temp-dir> [port]
The real server code (server.py, WebApprover, SSE) runs unchanged; only the model provider and tools
are replaced with the scripted versions from test_orchestrator.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import quaera.cli as cli  # noqa: E402
import quaera.server as server  # noqa: E402
from quaera import package  # noqa: E402
from quaera.gateway import ScriptedProvider  # noqa: E402
from test_discovery import DiscoveryScript, Fam, LemmaTools  # noqa: E402
from test_orchestrator import Script  # noqa: E402

home = Path(sys.argv[1])
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8766
cli.HOME = server.HOME = home / "projects"
package.KEY_PATH = home / "signing-key"
real_build = cli.build


def slow(script):
    def answer(system, prompt):
        time.sleep(0.4)          # so progress is visible in the UI
        return script(system, prompt)
    return answer


class LiveFakeTools(LemmaTools):
    """Records tool starts like the real ToolRegistry and Lean compilation takes a moment, so the live view can be tested."""

    def __init__(self, permissions, store):
        super().__init__(permissions)
        self.store = store

    def call(self, role, tool, **args):
        code = args.get("source") or args.get("content")
        actor = {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}
        self.store.append("tool.started", actor, {"role": role, "tool": tool, "args": {"theorem": args.get("theorem")},
                                                  **({"code": code} if code else {})})
        if tool == "lean.compile":
            time.sleep(1.5)
        out = super().call(role, tool, **args)
        self.store.append("tool.call", actor, {"role": role, "tool": tool, "args": {}, "resultPreview": out[:100]})
        return out


def fake_build(project, budget, auto_limit, providers=None, autonomy=None, domain=None, memory=True):
    from quaera.store import Store
    s = Store(project / "quaera.db")
    discover = s.meta("mode") == "discover"
    s.close()
    if discover:   # Discovery mode: two fake model families (ideation and cross-review happen across families)
        providers = {"anthropic": Fam(slow(DiscoveryScript("anthropic")), "anthropic", 0.002),
                     "openai": Fam(slow(DiscoveryScript("openai")), "openai", 0.0)}
        providers["openai"].billing = "subscription"
    else:
        providers = {"scripted": ScriptedProvider(slow(Script()), "scripted", 0.002)}
    orch = real_build(project, budget, auto_limit, providers, autonomy, domain, memory)
    orch.tools = LiveFakeTools(orch.permissions, orch.store)
    return orch


def manager_reply(system, prompt):
    """Fake Project manager: a title for the title request, a short record-based reply to chat (+ an optional team note proposal)."""
    time.sleep(0.6)
    if "You name research projects" in system:
        return "Sum of first n odds"
    last = prompt.rsplit("Human:", 1)[-1].split("\n")[0].strip()
    stages = next((line for line in prompt.splitlines() if line.startswith("Stages done:")), "Stages done: none")
    reply = (f"Here is where things stand. {stages}. The hypothesis states that $\\sum_{{i<n}} (2i+1) = n^2$ "
             "and the proof uses `Finset.sum_range_succ`.")
    if "tell the team" in last.lower():
        reply += "\nFORWARD: Please also state the n = 0 case explicitly in the report."
    return reply


server.build = fake_build
server.MANAGER_PROVIDERS = {"scripted": ScriptedProvider(manager_reply, "scripted", 0.001)}
server.PROVIDER_PROBES = {k: ScriptedProvider(lambda s, p: "51", "scripted") for k in ("claude", "codex", "opencode")}
server.serve(port)
