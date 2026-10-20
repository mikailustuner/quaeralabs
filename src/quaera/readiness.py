"""What this machine can do, in tiers (improvement plan A3). Used by `quaera doctor` and by /api/settings.

- core: needed for anything (a sandbox and a model provider). Missing core → `quaera doctor` exits 1.
- ml: AI/ML research (the experiment environment).
- math: mathematics research (Lean 4 + Mathlib + REPL).
- extras: nice to have.

A missing ml or math tier is not an error: that research domain is simply off, and the UI says why.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Check:
    tier: str
    name: str
    ok: bool
    fix: str = ""


def _runs(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def checks(providers: list[dict] | None = None) -> list[Check]:
    from . import images
    from .lean import DEFAULT_WORKSPACE, SLOW_LOAD_S, LeanChecker, LeanUnavailable, last_load
    from .mcp_servers.sandbox_server import ML_ENV, ml_image_in_use
    from .resources import IN_REPO
    from .sandbox import SandboxUnavailable, make_sandbox
    from .server import WEB_DIST

    out: list[Check] = []
    try:
        box = make_sandbox()
        out.append(Check("core", f"sandbox ({box.kind})", True))
    except SandboxUnavailable as exc:
        out.append(Check("core", "sandbox", False, f"{exc}; see docs/installation.md#bubblewrap"))
    if providers is None:
        from .providers import detect
        providers = detect()
    out.append(Check("core", "model provider (a CLI, an API key in the provider registry, or QUAERA_LITELLM_MODELS)",
                     bool(shutil.which("claude") or os.environ.get("QUAERA_LITELLM_MODELS") or any(d["ready"] for d in providers)),
                     "install Claude Code and log in, or add an API key: quaera providers add …"))
    out.append(Check("core", "web UI built", (WEB_DIST / "index.html").exists(), "cd web && npm ci && npm run build"))

    def fix(image: str, host: str) -> str:
        """Ways to get a research domain: pull its image, or (Linux) install it on the host."""
        ways = ([f"{images.engine()} pull {image}"] if images.engine() else []) + ([host] if sys.platform == "linux" else [])
        return " or ".join(ways) or "install Docker or Podman, then pull " + image

    try:
        img = ml_image_in_use()
        ok = images.present(img) if img else (ML_ENV / "bin" / "python").exists()
        out.append(Check("ml", f"ML experiment environment ({f'image {img}' if img else 'host ml-env'})", ok,
                         fix(images.ml_image(), "./install.sh --with-ml" if IN_REPO else "quaera setup ml")))
    except images.ImageUnavailable as exc:
        out.append(Check("ml", "ML experiment environment", False, str(exc)))

    lean_host = "./install.sh --with-lean" if IN_REPO else "git clone … && ./install.sh --with-lean"
    try:
        c = LeanChecker()
        out.append(Check("math", f"Lean 4 + Mathlib ({f'image {c.image}' if c.image else 'host'})", True))
        out.append(Check("math", "Lean REPL", c.has_repl(), f"cd {DEFAULT_WORKSPACE} && lake build REPL/repl"))
        load = last_load()
        if load:   # measured by the last research run; a slow disk can make Mathlib impractical (improvement plan A4)
            slow = " — slow, research runs will wait this long at every REPL start" if load["seconds"] > SLOW_LOAD_S else ""
            out.append(Check("math", f"last Mathlib load: {'loaded' if load['ok'] else 'FAILED'} in {load['seconds']:.0f} s "
                                     f"({load['at']}){slow}", load["ok"], "Lean is impractical on this disk: move lean/.lake to an SSD, or run Lean on another "
                                   "machine (docs/installation.md#lean-on-a-slow-disk)"))
    except (LeanUnavailable, SandboxUnavailable) as exc:
        out.append(Check("math", "Lean 4 + Mathlib", False, f"{fix(images.lean_image(), lean_host)} ({exc})"))
    return out + _extras(providers)


def _extras(providers: list[dict]) -> list[Check]:
    from .resources import IN_REPO
    out: list[Check] = []
    out.append(Check("extras", "systemd user session (memory/CPU limits for bwrap)",
                     bool(shutil.which("systemd-run")) and _runs(["systemd-run", "--user", "--scope", "--quiet", "true"]),
                     "WSL2: /etc/wsl.conf → [boot] systemd=true"))
    from .memory import default_embedder
    out.append(Check("extras", "lab memory embeddings (Turkish/English recall)", default_embedder() is not None,
                     ("uv sync --extra memory" if IN_REPO else "pip install 'quaeralabs[memory]'") + " (or QUAERA_MEMORY_EMBED is off)"))
    for d in providers:
        if d["id"] != "claude":
            out.append(Check("extras", f"{d['name']} ({d['family']}; cross-check) {d['version'] or ''}".strip(), d["ready"], d["note"]))
    return out


def domains(found: list[Check]) -> dict:
    """Per research domain: ready, and what is missing (shown next to the domain switch in the UI)."""
    def tier(t):
        missing = [f"{c.name} → {c.fix}" for c in found if c.tier in ("core", t) and not c.ok and c.name != "web UI built"]
        return {"ready": not missing, "missing": missing}
    return {"math": tier("math"), "ml": tier("ml")}


TIER_TITLE = {"core": "Core (required)", "ml": "AI/ML research", "math": "Mathematics research (Lean)", "extras": "Extras"}


def report(found: list[Check]) -> tuple[str, bool]:
    wsl = "microsoft" in platform.release().lower()
    lines = [f"Platform: {platform.system()} {platform.machine()}{' (WSL2)' if wsl else ''} · Python {platform.python_version()}"]
    core_ok = all(c.ok for c in found if c.tier == "core")
    for t, title in TIER_TITLE.items():
        tier = [c for c in found if c.tier == t]
        if not tier:
            continue
        lines.append(f"\n{title}")
        for c in tier:
            mark = "✓" if c.ok else ("✗" if t == "core" else "–")
            lines.append(f"  [{mark}] {c.name}" + ("" if c.ok else f" → {c.fix}"))
    dom = domains(found)
    on = [d for d in ("math", "ml") if dom[d]["ready"]]
    lines.append("")
    lines.append("Ready: " + (", ".join({"math": "mathematics", "ml": "AI/ML"}[d] for d in on) + " research." if core_ok and on else
                              "the core works, but no research domain is installed yet (see the hints above)."
                              if core_ok else "required components are missing."))
    return "\n".join(lines), core_ok
