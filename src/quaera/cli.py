"""quaera command line.

  quaera doctor                                   checks the environment
  quaera ask "QUESTION" --domain math --budget 3  starts a new research project
  quaera resume PROJECT                           resumes an interrupted research project where it stopped
  quaera status PROJECT                           stages, objects, cost
  quaera branch PROJECT --at SEQ --name NEW       branches off at an event
  quaera verify PROJECT                           recompiles the proof in a clean environment
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from .gateway import ClaudeCLIProvider, Gateway
from .ml_loop import ML_STAGES, MLOrchestrator
from .orchestrator import STAGES, AutoApprover, CapApprover, InteractiveApprover, Orchestrator
from .permissions import Permissions
from .store import Store, now
from .tools import ToolRegistry

HOME = Path(os.environ.get("QUAERA_HOME", Path.home() / ".quaera")) / "projects"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ı", "i")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "research"


def stages_for(store: Store) -> list[str]:
    """The project's stage list: ML, math verification or math Discovery mode."""
    if store.meta("domain") == "ml":
        return ML_STAGES
    if store.meta("mode") == "discover":
        from .discovery import DISCOVERY_STAGES
        return DISCOVERY_STAGES
    return STAGES


def project_path(name: str) -> Path:
    p = Path(name)
    return p if p.exists() else HOME / name


def make_providers(families: list[str] | None = None) -> dict:
    """Model providers ready on this machine (Claude Code, Codex CLI, OpenCode CLI; ADR 0017) + optional LiteLLM.

    `families`: the families chosen for the project (e.g. ["anthropic", "openai"]); None means every ready one.
    """
    from .providers import build_cli_providers
    providers = build_cli_providers()
    # API-key provider (LiteLLM): QUAERA_LITELLM_MODELS='{"cheap": "openai/…", "balanced": "…", "best": "…"}'
    if os.environ.get("QUAERA_LITELLM_MODELS"):
        from .gateway import LiteLLMProvider
        extra = LiteLLMProvider(json.loads(os.environ["QUAERA_LITELLM_MODELS"]))
        providers.setdefault(extra.family, extra)
    if not providers:
        providers = {"anthropic": ClaudeCLIProvider()}       # none ready: old behaviour, the error shows up on the call
    if families:
        chosen = {f: p for f, p in providers.items() if f in families}
        providers = chosen or providers
    # The primary family (Engineer, Hypothesis lane A) is Claude; the others serve cross-checks and parallel lanes.
    order = sorted(providers, key=lambda f: (f != "anthropic", f))
    return {f: providers[f] for f in order}


def build(project: Path, budget: float | None, auto_limit: float | None, providers: dict | None = None,
          autonomy: str | None = None, domain: str | None = None, memory: bool = True):
    perms = Permissions.load()
    store = Store(project / "quaera.db", perms)
    cap = budget if budget is not None else store.meta("budgetCapUsd")
    if cap is None:
        raise SystemExit("no budget cap: pass --budget")
    store.set_meta("budgetCapUsd", cap)
    record = lambda kind, payload: store.append(kind, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, payload)  # noqa: E731
    spent = sum(e["payload"]["costUsd"] for e in store.events("model.call"))
    if providers is None:
        providers = make_providers(store.meta("families"))
    gateway = Gateway(providers, cap, perms.agents, record, spent_usd=spent)
    tools = ToolRegistry(perms, record)
    if autonomy == "cap":
        approver = CapApprover(gateway)
    elif auto_limit is not None:
        approver = AutoApprover(auto_limit)
    else:
        approver = InteractiveApprover()
    domain = domain or store.meta("domain", "math")
    store.set_meta("domain", domain)
    cls = MLOrchestrator if domain == "ml" else Orchestrator
    if domain == "math" and store.meta("mode") == "discover":
        from .discovery import DiscoveryOrchestrator
        cls = DiscoveryOrchestrator
    from .memory import LabMemory
    # Evals run with memory=False so a previous result of the same task cannot leak from memory.
    lab_memory = LabMemory(HOME.parent / "memory.db") if memory else None   # HOME is read at call time (tests change it)
    return cls(store, gateway, tools, approver, perms, reports_dir=project, memory=lab_memory)


def cmd_ask(a) -> int:
    project = HOME / f"{now()[:10]}-{slugify(a.question)}"
    if project.exists():
        raise SystemExit(f"{project} already exists")
    if a.domain == "ml" and not a.data_dir:
        raise SystemExit("--domain ml requires --data-dir")
    orch = build(project, a.budget, a.auto_approve_under, autonomy=a.autonomy, domain=a.domain)
    orch.store.set_meta("title", a.question)
    if a.data_dir:
        orch.store.set_meta("dataDir", str(Path(a.data_dir).resolve()))
    default_scope = "Formal proof in Lean 4 + Mathlib" if a.domain == "math" else "Data under /data."
    orch.store.put({"type": "question", "createdBy": orch.human(), "title": a.question, "domain": a.domain,
                    "scope": a.scope or default_scope})
    print(f"Project: {project}")
    return finish(orch)


def finish(orch: Orchestrator) -> int:
    try:
        report = orch.run()
    finally:
        orch.tools.close()
    print(f"\nReport: {report}\nSpent: ${orch.gateway.spent_usd:.4f} / ${orch.gateway.cap_usd:.2f}")
    return 0


def cmd_resume(a) -> int:
    orch = build(project_path(a.project), a.budget, a.auto_approve_under, autonomy=a.autonomy)
    return finish(orch)


def cmd_status(a) -> int:
    store = Store(project_path(a.project) / "quaera.db")
    done = [e["payload"]["stage"] for e in store.events("stage.done")]
    calls = [e["payload"] for e in store.events("model.call")]
    print(f"Question: {store.meta('title')}")
    stages = stages_for(store)
    print("Stages: " + " · ".join(f"[{'x' if s in done else ' '}] {s}" for s in stages))
    for o in store.latest():
        if o["type"] in ("hypothesis", "result", "critique", "verification"):
            print(f"  {o['id']}: {o.get('status') or o.get('reproduced') or o.get('summary', '')[:80]}")
    print(f"Cost: ${sum(c['costUsd'] for c in calls):.4f} / ${store.meta('budgetCapUsd')} · {len(calls)} model calls · {len(store.events())} events")
    return 0


def cmd_branch(a) -> int:
    src = Store(project_path(a.project) / "quaera.db")
    target = HOME / a.name
    src.branch(target / "quaera.db", a.at)
    print(f"Branch created: {target} (up to event {a.at}). To continue: quaera resume {a.name}")
    return 0


def cmd_verify(a) -> int:
    from .lean import LeanChecker
    store = Store(project_path(a.project) / "quaera.db")
    states = {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}
    proof, formal = states.get("proof"), states.get("formal")
    if not proof:
        print("This project has no verified proof.")
        return 1
    report = LeanChecker().check(proof["source"], "quaera_main", formal["statement"], clean=True)
    print(json.dumps({"verified": report.verified, "axioms": report.axioms, "problems": report.problems,
                      "errors": report.errors, "seconds": report.seconds}, ensure_ascii=False, indent=2))
    return 0 if report.verified else 1


def cmd_doctor(a) -> int:
    """Environment check. Returns 1 if a required component is missing; optional components only warn."""
    import platform
    import subprocess
    from .lean import DEFAULT_WORKSPACE, LeanChecker, LeanUnavailable
    from .mcp_servers.sandbox_server import ML_ENV
    from .server import WEB_DIST

    def runs(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    wsl = "microsoft" in platform.release().lower()
    print(f"Platform: {platform.system()} {platform.machine()}{' (WSL2)' if wsl else ''} · Python {platform.python_version()}")
    required = [
        ("Linux (for the sandbox)", platform.system() == "Linux", "macOS/Windows: docs/installation.md"),
        ("bubblewrap sandbox works", bool(shutil.which("bwrap")) and runs(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"]),
         "apt install bubblewrap; for namespace permission see docs/installation.md#bubblewrap"),
        ("model provider (claude CLI or QUAERA_LITELLM_MODELS)", bool(shutil.which("claude") or os.environ.get("QUAERA_LITELLM_MODELS")),
         "install Claude Code and log in"),
    ]
    optional = [
        ("systemd user session (memory/CPU limits)", bool(shutil.which("systemd-run")) and runs(["systemd-run", "--user", "--scope", "--quiet", "true"]),
         "WSL2: /etc/wsl.conf → [boot] systemd=true"),
        ("web UI built", (WEB_DIST / "index.html").exists(), "cd web && npm ci && npm run build"),
        ("ML experiment environment (ml-env)", (ML_ENV / "bin" / "python").exists(), "./install.sh --with-ml"),
    ]
    try:
        c = LeanChecker()
        optional.append(("Lean 4 + Mathlib", True, ""))
        optional.append(("Lean REPL", c.repl_bin.exists(), f"cd {DEFAULT_WORKSPACE} && lake build REPL/repl"))
    except LeanUnavailable as exc:
        optional.append(("Lean 4 + Mathlib (for math research)", False, f"./install.sh --with-lean ({exc})"))
    from .memory import default_embedder
    optional.append(("lab memory embeddings (Turkish/English recall)", default_embedder() is not None,
                     "uv sync --extra memory (or QUAERA_MEMORY_EMBED is off)"))
    from .providers import detect
    for d in detect():
        if d["id"] != "claude":
            optional.append((f"{d['name']} ({d['family']}; cross-check) {d['version'] or ''}".strip(), d["ready"], d["note"]))
    ok = True
    for name, passed, fix in required:
        print(f"[{'✓' if passed else '✗'}] {name}" + ("" if passed else f" → {fix}"))
        ok &= passed
    for name, passed, fix in optional:
        print(f"[{'✓' if passed else '–'}] {name}" + ("" if passed else f" (optional) → {fix}"))
    print("Ready." if ok else "Required components are missing.")
    return 0 if ok else 1

def cmd_memory(a) -> int:
    from .memory import LabMemory, outcome
    mem = LabMemory(HOME.parent / "memory.db")
    if a.action == "rebuild":
        HOME.mkdir(parents=True, exist_ok=True)
        print(f"{mem.rebuild(HOME)} projects written to memory ({mem.path})")
        return 0
    items = mem.recall(" ".join(a.text), k=a.k)
    if not items:
        print("No similar research in memory.")
    for e in items:
        print(f"- {e['project']}\n  {e['title']}\n  {outcome(e)}")
    return 0


def cmd_titles(a) -> int:
    """Names projects that lack a short title via the Project manager (from the manager chat budget, ~$0.01 per project).
    Eval projects are named after the start of their question without a model call."""
    from .manager import Manager, fallback_title
    from .memory import EVAL_PREFIXES
    for p in sorted(HOME.iterdir()):
        if not (p / "quaera.db").exists():
            continue
        store = Store(p / "quaera.db")
        has, title = store.meta("shortTitle"), store.meta("title") or p.name
        if p.name.startswith(EVAL_PREFIXES) and not has:
            store.set_meta("shortTitle", fallback_title(title))
        store.close()
        if has and not a.force or p.name.startswith(EVAL_PREFIXES):
            continue
        m = Manager(p)
        try:
            print(f"{p.name} → {m.name()}  (${m.gateway.spent_usd:.4f})")
        finally:
            m.close()
    return 0


def cmd_iterate(a) -> int:
    from .memory import LabMemory
    from .tree import iterate
    if a.budget_per_branch is None and a.total_budget is None:
        raise SystemExit("--budget-per-branch or --total-budget is required")
    pid = project_path(a.project).name
    if a.auto_approve_under is not None:
        approve = lambda text, cost: cost <= a.auto_approve_under  # noqa: E731
    else:
        def approve(text, cost):
            return input(f"{text}\nApprove (at most ${cost:.2f})? [y/N] ").strip().lower() in ("y", "yes", "e", "evet")
    created = iterate(HOME, pid, build=lambda path, budget: build(path, budget, a.auto_approve_under),
                      providers=make_providers(), agent_specs=Permissions.load().agents, approve=approve,
                      max_branches=a.max_branches, budget_per_branch=a.budget_per_branch, total_budget=a.total_budget,
                      memory=LabMemory(HOME.parent / "memory.db"))
    print(f"{len(created)} new branches: {', '.join(created) or '—'}")
    return 0


def cmd_audit(a) -> int:
    from . import audit
    HOME.mkdir(parents=True, exist_ok=True)
    audits = audit.audit_all(HOME, online=not a.offline, lean=a.lean)
    for x in audits:
        if x.skipped:
            continue
        mark = "✓" if not x.findings else "✗"
        print(f"[{mark}] {x.project}  {x.checked}")
        for f in x.findings:
            print(f"     {f.kind}: {f.detail}")
    s = audit.summary(audits)
    print(json.dumps(s, ensure_ascii=False))
    return 0 if s["clean"] else 1


def cmd_triage(a) -> int:
    from . import triage
    HOME.mkdir(parents=True, exist_ok=True)
    if a.action == "scan":
        print(triage.format_scan(triage.scan(HOME)))
        return 0
    if a.action == "export":
        out = Path(a.target or "quaera-failure-cases.zip")
        out.write_bytes(triage.export(HOME))
        print(f"{out} written. Review its contents before sending; user names and paths are redacted.")
        return 0
    expect = {k: v for k, v in (("answer", a.expect_answer), ("notStatus", a.expect_not_status)) if v}
    for k, v in (("mustObject", a.expect_objection), ("mustStop", a.expect_stop)):
        if v is not None:
            expect[k] = v == "yes"
    try:
        path = triage.add_case(HOME, a.target, a.note or "", expect)
    except (ValueError, FileNotFoundError, IndexError) as exc:
        print(f"Could not add case: {exc}")
        return 1
    print(f"Case saved: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quaera", description="QuaeraLabs AI research team")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask", help="new research project")
    s.add_argument("question")
    s.add_argument("--domain", choices=["math", "ml"], default="math")
    s.add_argument("--scope", help="ML: data description (columns, files)")
    s.add_argument("--data-dir", help="ML: data directory the experiment sees read-only (/data)")
    s.add_argument("--autonomy", choices=["manual", "under", "cap"], help="manual: every approval is asked · under: automatic below --auto-approve-under · cap: automatic up to the budget cap")
    s.add_argument("--budget", type=float, required=True, help="budget cap (USD); never exceeded")
    s.add_argument("--auto-approve-under", type=float, help="pre-grant approvals below this amount (autonomy level 2)")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("resume")
    s.add_argument("project")
    s.add_argument("--budget", type=float, help="to change the cap (human only)")
    s.add_argument("--auto-approve-under", type=float)
    s.add_argument("--autonomy", choices=["manual", "under", "cap"])
    s.set_defaults(fn=cmd_resume)
    for name, fn in (("status", cmd_status), ("verify", cmd_verify)):
        s = sub.add_parser(name)
        s.add_argument("project")
        s.set_defaults(fn=fn)
    s = sub.add_parser("iterate", help="retry inconclusive branches as the Director suggests (research tree)")
    s.add_argument("project")
    s.add_argument("--max-branches", type=int, default=2)
    s.add_argument("--budget-per-branch", type=float)
    s.add_argument("--total-budget", type=float,
                   help="keep trying until the whole research line has spent this much (each branch gets the remainder)")
    s.add_argument("--auto-approve-under", type=float, help="open new branches below this amount without asking")
    s.set_defaults(fn=cmd_iterate)
    s = sub.add_parser("audit", help="honesty audit: fabricated citations, fake verification, tampered report")
    s.add_argument("--offline", action="store_true", help="do not query the official API for sources")
    s.add_argument("--lean", action="store_true", help="recompile proofs in a clean Lean process (heavy)")
    s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("triage", help="failure review: scan | add <project> | export <zip>")
    s.add_argument("action", choices=["scan", "add", "export"])
    s.add_argument("target", nargs="?", help="add: project name · export: output file")
    s.add_argument("--note", help="add: what went wrong (required)")
    s.add_argument("--expect-answer", choices=["yes", "no", "unclear"])
    s.add_argument("--expect-not-status", choices=["supported", "refuted", "inconclusive", "under_critique"])
    s.add_argument("--expect-objection", choices=["yes", "no"])
    s.add_argument("--expect-stop", choices=["yes", "no"])
    s.set_defaults(fn=cmd_triage)
    s = sub.add_parser("memory", help="research memory: rebuild | search <text>")
    s.add_argument("action", choices=["rebuild", "search"])
    s.add_argument("text", nargs="*")
    s.add_argument("-k", type=int, default=5)
    s.set_defaults(fn=cmd_memory)
    s = sub.add_parser("titles", help="give projects short titles (Project manager)")
    s.add_argument("--force", action="store_true", help="also rename projects that already have a short title")
    s.set_defaults(fn=cmd_titles)
    s = sub.add_parser("branch")
    s.add_argument("project")
    s.add_argument("--at", type=int, required=True)
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_branch)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("serve", help="local web UI (127.0.0.1)")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=lambda a: __import__("quaera.server", fromlist=["serve"]).serve(a.port) or 0)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
