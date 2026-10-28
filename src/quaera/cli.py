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
import sys
import unicodedata
from pathlib import Path

from .gateway import ClaudeCLIProvider, Gateway
from .ml_loop import ML_STAGES, MLOrchestrator
from .review import REVIEW_STAGES, ReviewOrchestrator
from .orchestrator import STAGES, AutoApprover, CapApprover, InteractiveApprover, Orchestrator
from .permissions import Permissions
from .store import Store, now
from .tools import ToolRegistry

HOME = Path(os.environ.get("QUAERA_HOME", Path.home() / ".quaera")) / "projects"
DEFAULT_SCOPE = {"math": "Formal proof in Lean 4 + Mathlib", "ml": "Data under /data.",
                 "review": "Published studies (Europe PMC, OpenAlex); data traced to open-access full texts and abstracts."}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ı", "i")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "research"


def stages_for(store: Store) -> list[str]:
    """The project's stage list: ML, review, math verification or math Discovery mode."""
    if store.meta("domain") == "ml":
        return ML_STAGES
    if store.meta("domain") == "review":
        return REVIEW_STAGES
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
    from .providers import build_providers
    providers = build_providers()
    # API-key provider (LiteLLM): QUAERA_LITELLM_MODELS='{"cheap": "openai/…", "balanced": "…", "best": "…"}'
    if os.environ.get("QUAERA_LITELLM_MODELS"):
        from .gateway import LiteLLMProvider
        extra = LiteLLMProvider(json.loads(os.environ["QUAERA_LITELLM_MODELS"]))
        providers.setdefault(extra.family, extra)
    if not providers:
        providers = {"anthropic": ClaudeCLIProvider()}       # none ready: old behaviour, the error shows up on the call
    if families:   # the project's chosen routes; older projects stored family names, which match a route or its family
        chosen = {k: p for k, p in providers.items() if k in families or getattr(p, "family", k) in families}
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
    from .capability import ModelStats
    stats = ModelStats(HOME.parent / "stats.db") if memory else None

    def record(kind: str, payload: dict) -> None:
        store.append(kind, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, payload)
        if stats is not None and kind in ("model.call", "model.error"):   # M2 scoreboard: cost, calls, errors per model
            try:
                (stats.record_call if kind == "model.call" else stats.record_error)(payload)
            except Exception as exc:   # auxiliary; recorded, never fatal
                store.append("stats.error", {"kind": "agent", "role": "director", "model": "quaera/deterministic",
                                             "modelFamily": "quaera"}, {"error": str(exc)[:200]})
    paid = [e["payload"] for e in store.events("model.call") if not e["payload"].get("cached")]
    spent = sum(c["costUsd"] for c in paid)
    if providers is None:
        providers = make_providers(store.meta("families"))
    from . import registry
    from .cache import CompletionCache, enabled as cache_enabled
    from .gateway import Limits
    reg = registry.load()
    routing = {**reg["routing"], **(store.meta("routing") or {})}
    ladders = {**reg["ladders"], **(store.meta("ladders") or {})}
    if os.environ.get("QUAERA_ENGINEER_LADDER") and "engineer" not in ladders and providers:
        # improvement plan C2 (measurement switch): the Engineer starts cheap and climbs, on its primary route
        ladders["engineer"] = [f"{next(iter(providers))}@{p.strip()}" for p in os.environ["QUAERA_ENGINEER_LADDER"].split(",")]
    lim = store.meta("budget") or {}          # P4: {"calls": n, "tokens": n, "deadline": unix time}
    gateway = Gateway(providers, cap, perms.agents, record, spent_usd=spent, routing=routing, ladders=ladders,
                      limits=Limits(lim.get("calls"), lim.get("tokens"), lim.get("deadline")), calls=len(paid),
                      tokens=sum(c.get("inputTokens", 0) + c.get("outputTokens", 0) for c in paid),
                      cache=CompletionCache(HOME.parent / "cache.db") if memory and cache_enabled() else None)
    tools = ToolRegistry(perms, record)
    if autonomy == "cap":
        approver = CapApprover(gateway)
    elif auto_limit is not None:
        approver = AutoApprover(auto_limit)
    else:
        approver = InteractiveApprover()
    domain = domain or store.meta("domain", "math")
    store.set_meta("domain", domain)
    cls = {"ml": MLOrchestrator, "review": ReviewOrchestrator}.get(domain, Orchestrator)
    if domain == "math" and store.meta("mode") == "discover":
        from .discovery import DiscoveryOrchestrator
        cls = DiscoveryOrchestrator
    from .memory import LabMemory
    # Evals run with memory=False so a previous result of the same task cannot leak from memory.
    lab_memory = LabMemory(HOME.parent / "memory.db") if memory else None   # HOME is read at call time (tests change it)
    # Lemma bank (K1) and capability scoreboard (S2) are lab knowledge like the memory: off for evals.
    from .bank import LemmaBank, ResultBank
    use_bank = memory and os.environ.get("QUAERA_BANK", "on") != "off"
    bank = LemmaBank(HOME.parent / "bank.db") if use_bank else None
    results = ResultBank(HOME.parent / "bank.db") if use_bank else None
    return cls(store, gateway, tools, approver, perms, reports_dir=project, memory=lab_memory, bank=bank, stats=stats, results=results)


def limits_meta(calls: int | None, hours: float | None, tokens: int | None, prev: dict | None = None) -> dict | None:
    """P4: the budget dimensions besides USD as project meta; the deadline is fixed when the limit is set."""
    import time
    out = dict(prev or {})
    if calls is not None:
        out["calls"] = int(calls)
    if tokens is not None:
        out["tokens"] = int(tokens)
    if hours is not None:
        out["deadline"] = time.time() + float(hours) * 3600
        out["hours"] = float(hours)
    return out or None


def apply_limits(store: Store, a) -> None:
    lim = limits_meta(getattr(a, "max_calls", None), getattr(a, "max_hours", None), getattr(a, "max_tokens", None),
                      store.meta("budget"))
    if lim:
        store.set_meta("budget", lim)


def cmd_ask(a) -> int:
    project = HOME / f"{now()[:10]}-{slugify(a.question)}"
    if project.exists():
        raise SystemExit(f"{project} already exists")
    if a.domain == "ml" and not a.data_dir:
        raise SystemExit("--domain ml requires --data-dir")
    pre = Store(project / "quaera.db")
    apply_limits(pre, a)
    pre.close()
    orch = build(project, a.budget, a.auto_approve_under, autonomy=a.autonomy, domain=a.domain)
    orch.store.set_meta("title", a.question)
    if a.data_dir:
        orch.store.set_meta("dataDir", str(Path(a.data_dir).resolve()))
    default_scope = DEFAULT_SCOPE[a.domain]
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
    pre = Store(project_path(a.project) / "quaera.db")
    apply_limits(pre, a)
    pre.close()
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
    if store.meta("domain") == "review":
        from .review import recheck
        out = recheck(store)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["reproduced"] == "yes" else 1
    states = {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}
    proof, formal = states.get("proof"), states.get("formal")
    if not proof:
        print("This project has no verified proof.")
        return 1
    report = LeanChecker().check(proof["source"], "quaera_main", formal["statement"], clean=True)
    print(json.dumps({"verified": report.verified, "axioms": report.axioms, "problems": report.problems,
                      "errors": report.errors, "seconds": report.seconds}, ensure_ascii=False, indent=2))
    return 0 if report.verified else 1


def cmd_prove(a) -> int:
    """Lean project mode (improvement plan C3): close the `sorry` of one declaration in your own Lean project."""
    import time
    from . import registry
    from .gateway import BudgetExceeded, Limits
    from .lean import LeanUnavailable
    from .prove_file import ProveError, checker_for, load_target, prove_file, run_dir
    from .resources import quaera_home
    from .sandbox import SandboxUnavailable

    try:
        target = load_target(Path(a.file), a.decl, Path(a.project) if a.project else None)
        checker = checker_for(target)
    except (ProveError, LeanUnavailable, SandboxUnavailable) as exc:
        print(f"quaera prove: {exc}", file=sys.stderr)
        return 2
    perms = Permissions.load()
    run = run_dir(quaera_home(), target)
    store = Store(run / "quaera.db", perms)
    store.set_meta("title", f"prove {target.name} in {target.path}")
    store.set_meta("mode", "prove")
    actor = {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}
    record = lambda kind, payload: store.append(kind, actor, payload)  # noqa: E731
    reg = registry.load()
    deadline = time.time() + a.max_hours * 3600 if a.max_hours else None
    gateway = Gateway(make_providers(), a.budget, perms.agents, record, routing=reg["routing"], ladders=reg["ladders"],
                      limits=Limits(a.max_calls, a.max_tokens, deadline))
    share = gateway.share_cap(0.95)       # keep a little for the final clean build's bookkeeping, never above the cap

    class Spent(Exception):
        pass

    def ask(system: str, prompt: str, n: int, lane: str | None = None) -> str:
        if not share():
            raise Spent(gateway.exhausted() or "budget used")
        try:   # one declaration needs far less output than a research proof file; this also keeps each call affordable
            completion, _ = gateway.call("engineer", system, prompt, min(n, a.max_output))
        except BudgetExceeded as exc:
            raise Spent(f"{exc} (raise --budget, lower --max-output, or route the engineer to a cheaper model: "
                        f"quaera route engineer …)") from exc
        return completion.text

    def progress(step, status, detail="", lane=None):
        if status in ("start", "done", "fail") and not a.quiet:
            print(f"  {'·' if status == 'start' else '✓' if status == 'done' else '✗'} {step}", flush=True)

    hints = ""
    if "import Mathlib" in target.prefix:
        try:
            from .mathlib_index import MathlibIndex
            hits = MathlibIndex.default().search(target.statement, 8)
            hints = "Possibly useful Mathlib declarations: " + "; ".join(h.get("text", "")[:160] for h in hits) if hits else ""
        except Exception:   # the index is optional (built lazily from our Mathlib workspace)
            hints = ""
    print(f"Proving `{target.name}` in {target.path} (project {target.project.name}, "
          f"{'REPL' if checker.repl_bin else 'one-shot builds'}) · budget ${a.budget:.2f}", flush=True)
    try:
        out = prove_file(target, checker, ask, hints=hints, rounds=a.rounds, more=share, record=record, progress=progress)
    except Spent as exc:
        out = None
        stopped = str(exc)
    finally:
        checker.close()
    spent = f"${gateway.spent_usd:.4f} / ${gateway.cap_usd:.2f}, {gateway.calls} model calls"
    if out is None or not out.solved:
        why = stopped if out is None else out.stopped
        store.append("prove.done", actor, {"solved": False, "stopped": why})
        print(f"No proof found: {why}. Spent {spent}. Log: {run}")
        return 1
    store.append("prove.done", actor, {"solved": True, "calls": out.model_calls})
    print(out.diff)
    print(f"Proof found and checked by a clean build of the whole file. Spent {spent}.")
    if not a.write:
        patch = run / f"{target.name}.patch"
        patch.write_text(out.diff, encoding="utf-8")
        print(f"The file was not changed. Patch: {patch}\nApply it with --write, or: cd {target.project} && git apply {patch}")
        return 0
    if target.path.read_text(encoding="utf-8") != target.text:
        print("The file changed while the proof was searched; not writing. Run again.", file=sys.stderr)
        return 1
    if not a.yes and input(f"Write the proof into {target.path}? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("Not written.")
        return 0
    target.path.write_text(out.new_text, encoding="utf-8")
    print(f"Written: {target.path}")
    return 0


ML_PACKAGES = ["numpy", "scipy", "scikit-learn"]


def cmd_setup(a) -> int:
    """`quaera setup ml`: the ML experiment environment for an installed package (install.sh --with-ml does the same in a
    repo checkout). A separate virtualenv: agent-written experiment code never runs in quaera's own environment."""
    import shutil
    import subprocess
    from .resources import ml_env
    target = Path(a.path) if a.path else ml_env()
    if (target / "bin" / "python").exists() and not a.force:
        print(f"ML environment already exists: {target} (use --force to rebuild)")
        return 0
    gpu = bool(shutil.which("nvidia-smi")) and subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
    index = "https://download.pytorch.org/whl/cu126" if gpu else "https://download.pytorch.org/whl/cpu"
    print(f"Creating the ML environment ({'CUDA' if gpu else 'CPU'}) in {target} …", flush=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    steps = [[sys.executable, "-m", "venv", "--clear", str(target)],
             [str(target / "bin" / "python"), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
             [str(target / "bin" / "python"), "-m", "pip", "install", "--quiet", *ML_PACKAGES]]
    if not a.no_torch:
        steps.append([str(target / "bin" / "python"), "-m", "pip", "install", "--quiet", "torch", "--index-url", index])
    for cmd in steps:
        if subprocess.run(cmd).returncode != 0:
            print(f"failed: {' '.join(cmd)}", file=sys.stderr)
            return 1
    print(f"ML environment ready: {target}" + ("" if a.path is None or a.path == str(ml_env()) else
                                                f"\nSet QUAERA_ML_ENV={target} so research uses it."))
    return 0


def cmd_doctor(a) -> int:
    """Environment check in tiers (core, ML, mathematics, extras). Returns 1 only if a core component is missing."""
    from .readiness import checks, report
    text, ok = report(checks())
    print(text)
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
    s = Store(HOME / pid / "quaera.db")
    root = s.meta("root") or pid
    s.close()
    rs = Store(HOME / root / "quaera.db")
    lim = rs.meta("budget") or {}      # P4: the line's call and time budget is set on the root project
    rs.close()
    created = iterate(HOME, pid, build=lambda path, budget: build(path, budget, a.auto_approve_under),
                      providers=make_providers(), agent_specs=Permissions.load().agents, approve=approve,
                      max_branches=a.max_branches, budget_per_branch=a.budget_per_branch, total_budget=a.total_budget,
                      memory=LabMemory(HOME.parent / "memory.db"), beam=a.beam,
                      max_calls=a.max_calls if a.max_calls is not None else lim.get("calls"), deadline=lim.get("deadline"))
    print(f"{len(created)} new branches: {', '.join(created) or '—'}")
    return 0


def cmd_providers(a) -> int:
    """API-key providers in ~/.quaera/providers.json (capacity plan P1): list | add | remove | test | key."""
    from . import registry
    from .providers import detect, probe
    data = registry.load()
    if a.action == "list":
        for d in detect():
            print(f"[{'✓' if d['ready'] else '–'}] {d['id']:<14} {d['family']:<10} {d.get('billing', '')}  {d.get('note') or ''}")
        if data["routing"]:
            print("routing: " + ", ".join(f"{r}→{k}" for r, k in data["routing"].items()))
        if data["ladders"]:
            print("ladders: " + "; ".join(f"{r}: {' → '.join(l)}" for r, l in data["ladders"].items()))
        return 0
    if a.action == "add":
        models = {}
        for item in a.model or []:
            prof, _, name = item.partition("=")
            models[prof if name else "balanced"] = name or prof
        entry = {"id": a.id, "kind": a.kind, "models": models, "enabled": True}
        for k, v in (("apiBase", a.api_base), ("family", a.family), ("apiKeyEnv", a.key_env)):
            if v:
                entry[k] = v
        if a.free:
            entry["free"] = True
        if a.price:
            entry["price"] = {m: [float(x) for x in pr.split(",")] for m, pr in (p.split("=", 1) for p in a.price)}
        if a.concurrent:
            entry["limits"] = {"concurrent": a.concurrent}
        data["providers"] = [e for e in data["providers"] if e["id"] != a.id] + [entry]
        registry.save(data)
        print(f"{a.id} saved to {registry.registry_path()}" + ("" if registry.has_key(entry) else f"; set its key: quaera providers key {a.id}"))
        return 0
    if a.action == "remove":
        data["providers"] = [e for e in data["providers"] if e["id"] != a.id]
        data["routing"] = {r: k for r, k in data["routing"].items() if k != a.id}
        registry.save(data)
        print(f"{a.id} removed")
        return 0
    entry = next((e for e in data["providers"] if e["id"] == a.id), None)
    if entry is None:
        raise SystemExit(f"no registry provider '{a.id}'")
    if a.action == "key":
        import getpass
        registry.set_secret(registry.key_env(entry) or f"QUAERA_KEY_{a.id.upper().replace('-', '_')}",
                            getpass.getpass(f"API key for {a.id} (input hidden, empty = remove): "))
        print(f"key stored in {registry.secrets_path()} (mode 600)")
        return 0
    registry.load_secrets()
    print(json.dumps(probe(registry.build(entry)), ensure_ascii=False))
    return 0


def cmd_route(a) -> int:
    """Role routing and escalation ladders (P2, S1): quaera route critic openrouter · quaera route engineer --ladder a@cheap b@best"""
    from . import registry
    data = registry.load()
    if a.ladder:
        data["ladders"][a.role] = a.ladder
    elif a.provider in ("-", "none"):
        data["routing"].pop(a.role, None)
        data["ladders"].pop(a.role, None)
    elif a.provider:
        data["routing"][a.role] = a.provider
    registry.save(data)
    print(json.dumps({"routing": data["routing"], "ladders": data["ladders"]}, ensure_ascii=False))
    return 0


def cmd_bank(a) -> int:
    """Lemma bank (K1): stats | search <text>."""
    from .bank import LemmaBank
    bank = LemmaBank(HOME.parent / "bank.db")
    if a.action == "stats":
        print(json.dumps(bank.stats()))
        return 0
    for it in bank.search(" ".join(a.text), k=a.k):
        print(f"- [{it['id']}] {it['lean']}\n  {it['statement']}  (from {it['project']})")
    return 0


def cmd_models(a) -> int:
    """Capability scoreboard (M2/S2): per model calls, cost, errors, JSON reliability, Lean compile rate and the deep
    probe. `quaera models probe <provider>` runs the deep probe (five small Lean proofs, one JSON question)."""
    from .capability import ModelStats, adapt
    stats = ModelStats(HOME.parent / "stats.db")
    if a.action == "probe":
        from .lean import LeanChecker
        from .providers import build_providers, deep_probe
        provs = build_providers()
        if a.provider not in provs:
            raise SystemExit(f"no ready provider '{a.provider}': {', '.join(provs) or 'none'}")
        lean = LeanChecker()
        try:
            res = deep_probe(provs[a.provider], lean.check, a.profile)
        finally:
            lean.close()
        label = f"{getattr(provs[a.provider], 'family', a.provider)}/{res['model']}"
        stats.set_probe(label, res["provingScore"], res["of"], res["jsonOk"])
        print(json.dumps({**res, "recordedAs": label}, ensure_ascii=False, indent=2))
        return 0
    rows = stats.board()
    if not rows:
        print("No observations yet: the scoreboard fills as research runs (or run `quaera models probe <provider>`).")
    fmt = lambda x: "—" if x is None else f"{x:.0%}"  # noqa: E731
    for p in rows:
        _, why = adapt(p)
        probe = p["probe"]
        cut = f", {p['output_cut']} cut off" if p["output_cut"] else ""
        print(f"{p['model']:<40} calls {p['calls']} · ${p['meanUsd'] or 0:.4f}/call · errors {fmt(p['errorRate'])} · "
              f"JSON {fmt(p['json_reliability'])} ({p['json_n']}{cut}) · compile {fmt(p['compile_rate'])} ({p['compile_n']})"
              + (f" · probe {probe['score']}/{probe['of']}" if probe else "") + (f"\n    adapts: {'; '.join(why)}" if why else ""))
    return 0


def cmd_sandbox_exec(a) -> int:
    """Runs one command in this machine's sandbox and prints the JSON result: the remote end of the ML3 runner."""
    from .mcp_servers.sandbox_server import sandbox_exec
    print(sandbox_exec(a.workdir, a.command, a.data_dir, a.timeout, a.gpu))
    return 0


def cmd_cache(a) -> int:
    from .cache import CompletionCache
    c = CompletionCache(HOME.parent / "cache.db")
    print(json.dumps(c.stats() if a.action == "stats" else {"cleared": c.clear()}))
    return 0


def cmd_critique(a) -> int:
    """Improvement plan E1: the measured Critic on an experiment directory that QuaeraLabs did not run."""
    from . import registry
    from .critique import as_dict, critique, markdown
    from .gateway import BudgetExceeded
    root = Path(a.dir)
    if not root.is_dir():
        print(f"quaera critique: no such directory: {root}", file=sys.stderr)
        return 2
    perms = Permissions.load()
    reg = registry.load()
    gateway = Gateway(make_providers(), a.budget, perms.agents, routing=reg["routing"], ladders=reg["ladders"])

    def on(family):
        def call(system, prompt, n):
            c, _ = gateway.call("critic", system, prompt, n, family=family)
            return c.text, c.model
        return call
    first = next(iter(gateway.providers))
    other = next((k for k in gateway.providers if gateway.family_of(k) != gateway.family_of(first)), None)
    try:
        c = critique(root, on(first), on(other) if other else None,
                     Path(a.prereg) if a.prereg else None, Path(a.results) if a.results else None)
    except BudgetExceeded as exc:
        print(f"quaera critique: {exc} (raise --budget)", file=sys.stderr)
        return 2
    text = json.dumps(as_dict(c), ensure_ascii=False, indent=2) if a.json else markdown(c, root)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    if c.flawed is None:
        return 2
    return 1 if a.fail_on and c.flawed and SEVERITY.index(c.severity or "low") >= SEVERITY.index(a.fail_on) else 0


SEVERITY = ["low", "medium", "high", "blocking"]


def cmd_verify_package(a) -> int:
    """Improvement plan E3: check an evidence package someone else produced."""
    from .package import check_package
    data = Path(a.file).read_bytes()
    checks = check_package(data, online=not a.offline, lean=a.lean)
    if a.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = {"ok": "✓", "fail": "✗", "skipped": "–"}[c["status"]]
            print(f"[{mark}] {c['check']}" + (f": {c['detail']}" if c["detail"] else ""))
    return 1 if any(c["status"] == "fail" for c in checks) else 0


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


def add_limit_args(s) -> None:
    s.add_argument("--max-calls", type=int, help="budget dimension: at most this many model calls (bounds subscription CLIs)")
    s.add_argument("--max-hours", type=float, help="budget dimension: wall-clock hours from now")
    s.add_argument("--max-tokens", type=int, help="budget dimension: total input+output tokens")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quaera", description="QuaeraLabs AI research team")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask", help="new research project")
    s.add_argument("question")
    s.add_argument("--domain", choices=["math", "ml", "review"], default="math", help="review: systematic review and meta-analysis")
    s.add_argument("--scope", help="ML: data description (columns, files); review: population and setting")
    s.add_argument("--data-dir", help="ML: data directory the experiment sees read-only (/data)")
    s.add_argument("--autonomy", choices=["manual", "under", "cap"], help="manual: every approval is asked · under: automatic below --auto-approve-under · cap: automatic up to the budget cap")
    s.add_argument("--budget", type=float, required=True, help="budget cap (USD); never exceeded")
    s.add_argument("--auto-approve-under", type=float, help="pre-grant approvals below this amount (autonomy level 2)")
    add_limit_args(s)
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("prove", help="close the `sorry` of one declaration in your own Lean project (Lean project mode)")
    s.add_argument("file", help="the .lean file")
    s.add_argument("--decl", required=True, help="name of the theorem or lemma whose `sorry` to close")
    s.add_argument("--project", help="the Lean project's root (default: the nearest directory with a lakefile)")
    s.add_argument("--budget", type=float, required=True, help="budget cap (USD); never exceeded")
    s.add_argument("--rounds", type=int, default=3, help="search rounds while the budget lasts")
    s.add_argument("--max-output", type=int, default=6000, help="output tokens per model call (bounds the worst-case cost of a call)")
    s.add_argument("--write", action="store_true", help="write the proof into the file (asks first; the file is otherwise unchanged)")
    s.add_argument("--yes", action="store_true", help="with --write: do not ask")
    s.add_argument("--quiet", action="store_true")
    add_limit_args(s)
    s.set_defaults(fn=cmd_prove)
    s = sub.add_parser("critique", help="the Critic on an experiment directory you ran yourself (nothing is executed)")
    s.add_argument("dir", help="the experiment directory: code, results, pre-registration, report")
    s.add_argument("--prereg", help="the pre-registration file, if it is not in the directory or should be read first")
    s.add_argument("--results", help="the results file (JSON/CSV), read before the code")
    s.add_argument("--budget", type=float, default=0.5, help="budget cap (USD); never exceeded")
    s.add_argument("--json", action="store_true", help="JSON instead of Markdown")
    s.add_argument("--out", help="also write the critique to this file")
    s.add_argument("--fail-on", choices=SEVERITY, help="exit 1 when an objection has at least this severity (CI)")
    s.set_defaults(fn=cmd_critique)
    s = sub.add_parser("verify-package", help="check an evidence package: signature, label, hashes, citations, Lean")
    s.add_argument("file", help="the evidence package (.zip)")
    s.add_argument("--offline", action="store_true", help="do not query the official registries for the citations")
    s.add_argument("--lean", action="store_true", help="recompile the proof in a clean Lean process (heavy)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_verify_package)
    s = sub.add_parser("resume")
    s.add_argument("project")
    s.add_argument("--budget", type=float, help="to change the cap (human only)")
    s.add_argument("--auto-approve-under", type=float)
    s.add_argument("--autonomy", choices=["manual", "under", "cap"])
    add_limit_args(s)
    s.set_defaults(fn=cmd_resume)
    s = sub.add_parser("providers", help="API-key providers: list | add ID | remove ID | key ID | test ID")
    s.add_argument("action", choices=["list", "add", "remove", "key", "test"])
    s.add_argument("id", nargs="?")
    s.add_argument("--kind", choices=["anthropic", "openai", "google", "openrouter", "openai-compatible"])
    s.add_argument("--model", action="append", help="profile=model (cheap/balanced/best), repeatable; a bare name is 'balanced'")
    s.add_argument("--api-base", help="openai-compatible: server URL (e.g. http://127.0.0.1:11434/v1)")
    s.add_argument("--family", help="model family for the cross-model rule (default from the kind)")
    s.add_argument("--key-env", help="environment variable holding the key")
    s.add_argument("--price", action="append", help="model=in,out USD per million tokens (repeatable)")
    s.add_argument("--free", action="store_true", help="local model: no per-call charge")
    s.add_argument("--concurrent", type=int, help="maximum parallel calls")
    s.set_defaults(fn=cmd_providers)
    s = sub.add_parser("route", help="route a role to a provider, or give it an escalation ladder")
    s.add_argument("role")
    s.add_argument("provider", nargs="?", help="provider id ('-' clears)")
    s.add_argument("--ladder", nargs="+", help="provider@profile … climbed on failure")
    s.set_defaults(fn=cmd_route)
    s = sub.add_parser("bank", help="lemma bank of Lean-verified statements: stats | search <text>")
    s.add_argument("action", choices=["stats", "search"])
    s.add_argument("text", nargs="*")
    s.add_argument("-k", type=int, default=8)
    s.set_defaults(fn=cmd_bank)
    s = sub.add_parser("models", help="capability scoreboard per model; `models probe <provider>` runs the deep probe")
    s.add_argument("action", nargs="?", default="list", choices=["list", "probe"])
    s.add_argument("provider", nargs="?")
    s.add_argument("--profile", default="cheap", choices=["cheap", "balanced", "best"])
    s.set_defaults(fn=cmd_models)
    s = sub.add_parser("sandbox-exec", help="run a command in this machine's sandbox (the remote end of a GPU runner)")
    s.add_argument("--workdir", required=True)
    s.add_argument("--data-dir")
    s.add_argument("--timeout", type=int, default=600)
    s.add_argument("--gpu", action="store_true")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(fn=lambda a: cmd_sandbox_exec(argparse.Namespace(**{**vars(a), "command": [c for c in a.command if c != "--"]})))
    s = sub.add_parser("cache", help="completion cache: stats | clear")
    s.add_argument("action", choices=["stats", "clear"])
    s.set_defaults(fn=cmd_cache)
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
    s.add_argument("--beam", type=int, default=1, help="expand this many of the most promising nodes concurrently (tree search)")
    s.add_argument("--max-calls", type=int, help="stop when the whole tree has made this many paid model calls")
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
    s = sub.add_parser("setup", help="install a research domain's environment outside a repo checkout: setup ml")
    s.add_argument("what", choices=["ml"])
    s.add_argument("--path", help="where to create it (default: the repo's ml-env, or ~/.quaera/ml-env for an installed package)")
    s.add_argument("--no-torch", action="store_true", help="skip PyTorch (numpy, scipy and scikit-learn only)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("serve", help="local web UI (127.0.0.1)")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=lambda a: __import__("quaera.server", fromlist=["serve"]).serve(a.port) or 0)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
