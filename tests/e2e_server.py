"""Arayüz uçtan uca testi için sahte modellerle çalışan sunucu (para harcamaz, ağ kullanmaz).

Kullanım: uv run python tests/e2e_server.py <geçici-dizin> [port]
Gerçek sunucu kodu (server.py, WebApprover, SSE) aynen çalışır; yalnızca model sağlayıcı ve araçlar
test_orchestrator'daki betikli sürümlerle değiştirilir.
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
        time.sleep(0.4)          # arayüzde ilerleme görülebilsin
        return script(system, prompt)
    return answer


class LiveFakeTools(LemmaTools):
    """Gerçek ToolRegistry gibi araç başlangıcını kayda yazar ve Lean derlemesi kısa sürer: canlı görünüm sınanabilsin."""

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
    if discover:   # keşif kipi: iki sahte model ailesi (fikir üretimi ve çapraz inceleme aileler arasında)
        providers = {"anthropic": Fam(slow(DiscoveryScript("anthropic")), "anthropic", 0.002),
                     "openai": Fam(slow(DiscoveryScript("openai")), "openai", 0.0)}
        providers["openai"].billing = "subscription"
    else:
        providers = {"scripted": ScriptedProvider(slow(Script()), "scripted", 0.002)}
    orch = real_build(project, budget, auto_limit, providers, autonomy, domain, memory)
    orch.tools = LiveFakeTools(orch.permissions, orch.store)
    return orch


def manager_reply(system, prompt):
    """Sahte Proje yöneticisi: kısa ad isteğine ad, sohbete kayda dayalı kısa yanıt (+ isteğe bağlı ekip notu önerisi)."""
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
