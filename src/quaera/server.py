"""Local web server (Phase 3): `quaera serve`.

Listens on 127.0.0.1 only. Serves the UI (web/dist), a JSON API and a live event stream (SSE).
Research runs in a background thread; at approval points `WebApprover` waits for the human decision.
Pending approvals are written to the event log as `approval.pending`; the UI shows them as cards.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from .cli import HOME, build, slugify, stages_for
from .ml_loop import ML_STAGES
from .orchestrator import STAGES, Approver, Decision
from .store import Store, now, report_file

ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = ROOT / "web" / "dist"


# --- web approver ----------------------------------------------------------------

@dataclass
class PendingApproval:
    id: str
    action: str
    summary: str
    cost_usd: float
    options: list[str] | None
    event: threading.Event = field(default_factory=threading.Event)
    decision: Decision | None = None


class WebApprover(Approver):
    """Waits for the human decision from the UI. In autonomy mode 'under' or 'cap' it decides itself by the amount rule."""

    def __init__(self, store: Store, autonomy: str = "manual", limit_usd: float = 0.0, gateway=None):
        self.store, self.autonomy, self.limit_usd, self.gateway = store, autonomy, limit_usd, gateway
        self.pending: dict[str, PendingApproval] = {}
        self.cancelled = False

    def decide(self, action, summary, cost_usd, options=None):
        if action not in ("publish", "raise_budget_cap"):
            if self.autonomy == "under" and cost_usd <= self.limit_usd:
                return Decision(True, "auto_under_limit", 0, f"within the pre-approved ${self.limit_usd:.2f} limit")
            if self.autonomy == "cap" and self.gateway and cost_usd <= self.gateway.remaining() + 1e-9:
                return Decision(True, "auto_to_budget_cap", 0, "auto-approved within the budget cap")
        pid = f"approval-{len(self.store.events('approval.pending')) + 1}"
        p = PendingApproval(pid, action, summary, cost_usd, options)
        self.pending[pid] = p
        self.store.append("approval.pending", {"kind": "human", "userId": self.user},
                          {"id": pid, "action": action, "summary": summary, "costUsd": cost_usd, "options": options})
        while not p.event.wait(1.0):
            if self.cancelled:
                return Decision(False, "manual", note="server stopped")
        self.pending.pop(pid, None)
        self.store.append("approval.resolved", {"kind": "human", "userId": self.user},
                          {"id": pid, "approved": p.decision.approved, "choice": p.decision.choice})
        return p.decision


# --- run manager -----------------------------------------------------------------

class Runner:
    """At most one background research thread per project."""

    def __init__(self):
        self.threads: dict[str, threading.Thread] = {}
        self.approvers: dict[str, WebApprover] = {}
        self.errors: dict[str, str] = {}

    def running(self, pid: str) -> bool:
        t = self.threads.get(pid)
        return bool(t and t.is_alive())

    def start(self, pid: str, autonomy: str = "manual", limit_usd: float = 0.0) -> None:
        if self.running(pid):
            return
        orch = build(HOME / pid, None, None)
        approver = WebApprover(orch.store, autonomy, limit_usd, orch.gateway)
        orch.approver = approver
        orch.log = lambda m: None
        self.approvers[pid] = approver
        self.errors.pop(pid, None)

        def work():
            try:
                orch.run()
            except Exception as exc:  # shown in the UI; the research can be continued with `resume`
                self.errors[pid] = str(exc)[:500]
                orch.store.append("run.crashed", orch.det("director"), {"error": str(exc)[:500]})
                return
            finally:
                orch.tools.close()
            if orch.store.meta("keepTrying"):    # inconclusive → new branches until the project's budget is spent
                from .tree import MAX_ITERATIONS
                self.iterate_loop(pid, MAX_ITERATIONS, None, autonomy, limit_usd,
                                  total_budget=float(orch.store.meta("budgetCapUsd") or 0))

        t = threading.Thread(target=work, daemon=True, name=f"quaera-{pid}")
        self.threads[pid] = t
        t.start()


    def start_iterate(self, pid: str, max_branches: int, budget_per_branch: float | None, autonomy: str, limit_usd: float,
                      total_budget: float | None = None) -> None:
        """The iterate loop runs in a background thread; every new branch gets its own web approver (approval cards work)."""
        def work():
            self.iterate_loop(pid, max_branches, budget_per_branch, autonomy, limit_usd, total_budget)

        t = threading.Thread(target=work, daemon=True, name=f"quaera-iterate-{pid}")
        self.threads[pid] = t
        t.start()

    def iterate_loop(self, pid: str, max_branches: int, budget_per_branch: float | None, autonomy: str, limit_usd: float,
                     total_budget: float | None = None) -> None:
        from .cli import make_providers
        from .memory import LabMemory
        from .permissions import Permissions
        from .tree import iterate

        def build_child(path, budget):
            orch = build(path, budget, None)
            ap = WebApprover(orch.store, autonomy, limit_usd, orch.gateway)
            orch.approver, orch.log = ap, (lambda m: None)
            self.approvers[path.name] = ap
            self.threads[path.name] = threading.current_thread()
            return orch

        parent_store = open_store(pid)
        gate = WebApprover(parent_store, autonomy, limit_usd)
        self.approvers[pid] = gate
        try:
            iterate(HOME, pid, build=build_child, providers=make_providers(), agent_specs=Permissions.load().agents,
                    approve=lambda text, cost: gate.decide("extra_spend", text, cost).approved,
                    max_branches=max_branches, budget_per_branch=budget_per_branch, total_budget=total_budget,
                    memory=LabMemory(HOME.parent / "memory.db"), log=lambda m: None)
        except Exception as exc:
            self.errors[pid] = str(exc)[:500]
            parent_store.append("run.crashed", {"kind": "agent", "role": "director", "model": "quaera/deterministic",
                                                "modelFamily": "quaera"}, {"error": str(exc)[:500]})


RUNNER = Runner()
MANAGER_PROVIDERS: dict | None = None     # tests and the e2e server inject fake providers; None means make_providers()


def manager_for(pid: str):
    from .memory import LabMemory
    from .manager import Manager
    open_store(pid).close()
    return Manager(HOME / pid, MANAGER_PROVIDERS, memory=LabMemory(HOME.parent / "memory.db"))


def name_project(pid: str) -> None:
    """The short project title is generated in the background; project creation does not wait for it."""
    def work():
        m = manager_for(pid)
        try:
            m.name()
        finally:
            m.close()
    threading.Thread(target=work, daemon=True, name=f"quaera-name-{pid}").start()


# --- helpers ------------------------------------------------------------------------

def project_ids() -> list[str]:
    HOME.mkdir(parents=True, exist_ok=True)
    return sorted((p.name for p in HOME.iterdir() if (p / "quaera.db").exists()), reverse=True)


def open_store(pid: str) -> Store:
    path = HOME / pid / "quaera.db"
    if not path.exists() or "/" in pid or pid.startswith("."):
        raise FileNotFoundError(pid)
    return Store(path)


def states(store: Store) -> dict:
    out = {}
    for e in store.events("state"):
        out[e["payload"]["key"]] = e["payload"]["value"]
    return out


def summary(pid: str) -> dict:
    s = open_store(pid)
    calls = [e["payload"] for e in s.events("model.call")]
    done = [e["payload"]["stage"] for e in s.events("stage.done")]
    domain = s.meta("domain", "math")
    stages = stages_for(s)
    hyps = s.latest("hypothesis")
    chosen = next((h for h in hyps if h["status"] not in ("draft", "rejected")), None)
    st = states(s)
    parent = s.meta("branchOf")
    from .manager import fallback_title
    out = {"id": pid, "mode": s.meta("mode", "verify"), "families": s.meta("families"), "title": s.meta("title"), "shortTitle": s.meta("shortTitle") or fallback_title(s.meta("title") or pid),
           "domain": domain, "stages": stages, "done": done,
           "current": None if s.events("report.written") else next((x for x in stages if x not in done), None),
           "running": RUNNER.running(pid),
           "error": RUNNER.errors.get(pid), "stopped": st.get("stopped"),
           "spentUsd": round(sum(c["costUsd"] for c in calls), 4), "capUsd": s.meta("budgetCapUsd"),
           "calls": len(calls), "managerUsd": round(sum(e["payload"].get("costUsd", 0) for k in ("manager.call", "manager.error") for e in s.events(k)), 4),
           "hypothesis": chosen and {k: chosen.get(k) for k in ("id", "status", "statement")},
           "branchOf": parent and Path(parent["project"]).parent.name, "branchAt": parent and parent["atSeq"], "createdAt": (s.events() or [{"at": None}])[0]["at"]}
    s.close()
    return out


def graph(store: Store) -> dict:
    """Evidence graph: research objects are nodes, references are edges."""
    objs = store.latest()
    ids = {o["id"] for o in objs}
    nodes, edges = [], []
    label = {"question": "title", "hypothesis": "statement", "result": "summary", "critique": "body",
             "experiment": "method", "preregistration": "successCriterion"}
    for o in objs:
        if o["type"] in ("artifact", "message") and not (o["type"] == "artifact" and o.get("kind") == "paper"):
            continue
        nodes.append({"id": o["id"], "type": o["type"], "status": o.get("status") or o.get("reproduced") or o.get("relation"),
                      "label": (o.get(label.get(o["type"], ""), "") or o.get("uri") or o.get("kind") or "")[:160]})
    refs = [("questionId", "question"), ("hypothesisId", "hypothesis"), ("experimentId", "experiment"), ("resultId", "result"),
            ("targetId", "critique"), ("preregistrationId", "preregistration")]
    node_ids = {n["id"] for n in nodes}
    for o in objs:
        if o["id"] not in node_ids:
            continue
        for f, rel in refs:
            if o.get(f) in ids and o[f] in node_ids:
                edges.append({"from": o["id"], "to": o[f], "label": o.get("relation") if o["type"] == "evidence_link" else rel})
        for h in o.get("hypothesisIds", []):
            if h in node_ids:
                edges.append({"from": o["id"], "to": h, "label": "tests"})
        for r in o.get("derivedFrom", []):
            if r in node_ids:
                edges.append({"from": o["id"], "to": r, "label": "derived from"})
    return {"nodes": nodes, "edges": edges}


# --- endpoints ----------------------------------------------------------------------

async def api_projects(request: Request):
    return JSONResponse(sorted((summary(p) for p in project_ids()), key=lambda x: x["createdAt"] or "", reverse=True))


async def api_create(request: Request):
    body = await request.json()
    question = (body.get("question") or "").strip()
    domain = body.get("domain", "math")
    mode = body.get("mode", "verify")
    budget = float(body.get("budget", 0))
    # Discovery mode is for open problems: a higher cap is allowed (still a hard cap; the human sets it).
    max_budget = 500 if mode == "discover" else 50
    if len(question) < 8 or domain not in ("math", "ml") or mode not in ("verify", "discover") or not (0 < budget <= max_budget):
        return JSONResponse({"error": f"a question (≥8 characters), a domain (math/ml), a mode (verify/discover) and a budget of 0–{max_budget} USD are required"}, 400)
    if mode == "discover" and domain != "math":
        return JSONResponse({"error": "discovery mode is available for mathematics (Lean-verifiable results)"}, 400)
    families = body.get("families")
    if families is not None and (not isinstance(families, list) or not families or not all(isinstance(f, str) for f in families)):
        return JSONResponse({"error": "families must be a non-empty list of model families"}, 400)
    if domain == "ml" and not body.get("dataDir"):
        return JSONResponse({"error": "ML research requires a data directory"}, 400)
    if len(question) > 1500:
        return JSONResponse({"error": f"the question can be at most 1500 characters (currently {len(question)}); paste only the question"}, 400)
    pid = f"{now()[:10]}-{slugify(question)}"
    if (HOME / pid).exists():
        return JSONResponse({"error": "a project for this question already exists today"}, 409)
    scope = body.get("scope") or ("Formal proof in Lean 4 + Mathlib" if domain == "math" else "Data under /data.")
    from . import contracts
    errors = contracts.schema_errors("question", {"type": "question", "id": "Q-0001", "revision": 1, "createdAt": now(),
                                                  "createdBy": {"kind": "human", "userId": WebApprover.user},
                                                  "title": question, "domain": domain, "scope": scope}, "question")
    if errors:   # rejected before the project directory exists: no half-created project is left behind
        return JSONResponse({"error": "; ".join(errors)}, 400)
    path = HOME / pid
    path.mkdir(parents=True, exist_ok=True)
    pre = Store(path / "quaera.db")          # mode and families are written before build so the right orchestrator and providers are chosen
    pre.set_meta("mode", mode)
    if body.get("keepTrying"):
        pre.set_meta("keepTrying", True)     # after an inconclusive run: new branches until the budget is spent
    if families:
        pre.set_meta("families", families)
    pre.close()
    orch = build(path, budget, None, domain=domain)
    orch.store.set_meta("title", question)
    if body.get("dataDir"):
        orch.store.set_meta("dataDir", str(Path(body["dataDir"]).expanduser().resolve()))
    orch.store.put({"type": "question", "createdBy": orch.human(), "title": question, "domain": domain, "scope": scope})
    orch.tools.close()
    RUNNER.start(pid, body.get("autonomy", "manual"), float(body.get("autoLimit", 0)))
    name_project(pid)
    return JSONResponse({"id": pid}, 201)


async def api_manager(request: Request):
    """Chat with the Project manager. GET: history and chat budget. POST {text}: reply (does not affect the research)."""
    pid = request.path_params["pid"]
    try:
        m = manager_for(pid)
    except FileNotFoundError:
        return JSONResponse({"error": "no such project"}, 404)
    try:
        if request.method == "GET":
            return JSONResponse({"history": m.history(), "usage": m.usage()})
        text = ((await request.json()).get("text") or "").strip()
        if not text or len(text) > 4000:
            return JSONResponse({"error": "message must be 1–4000 characters"}, 400)
        reply = await asyncio.to_thread(m.ask, text)
        return JSONResponse({"reply": reply, "usage": m.usage()})
    finally:
        m.close()


async def api_rename(request: Request):
    pid = request.path_params["pid"]
    title = ((await request.json()).get("shortTitle") or "").strip()
    if not (1 <= len(title) <= 60):
        return JSONResponse({"error": "short title must be 1–60 characters"}, 400)
    try:
        s = open_store(pid)
    except FileNotFoundError:
        return JSONResponse({"error": "no such project"}, 404)
    s.set_meta("shortTitle", title)
    s.close()
    return JSONResponse({"shortTitle": title})


async def api_project(request: Request):
    pid = request.path_params["pid"]
    try:
        s = open_store(pid)
    except FileNotFoundError:
        return JSONResponse({"error": "no such project"}, 404)
    data = {**summary(pid), "objects": s.latest(), "states": states(s),
            "pending": [p.__dict__ | {"event": None} for p in RUNNER.approvers.get(pid, WebApprover(s)).pending.values()]
            if pid in RUNNER.approvers else [],
            "costs": [e["payload"] for e in s.events("model.call")],
            "report": report_file(HOME / pid).read_text(encoding="utf-8") if report_file(HOME / pid).exists() else None}
    for p in data["pending"]:
        p.pop("event", None)
        p["decision"] = None
    s.close()
    return JSONResponse(data)


async def api_events(request: Request):
    pid = request.path_params["pid"]
    after = int(request.query_params.get("after", 0))
    s = open_store(pid)
    evs = [e for e in s.events() if e["seq"] > after]
    s.close()
    return JSONResponse(evs)


async def api_stream(request: Request):
    """SSE: forwards new events with at most ~1 s delay."""
    pid = request.path_params["pid"]
    after = int(request.query_params.get("after", 0))

    async def gen():
        last = after
        while True:
            if await request.is_disconnected():
                break
            s = open_store(pid)
            evs = [e for e in s.events() if e["seq"] > last]
            s.close()
            for e in evs:
                last = e["seq"]
                yield f"id: {e['seq']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            if not evs:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def api_graph(request: Request):
    s = open_store(request.path_params["pid"])
    g = graph(s)
    s.close()
    return JSONResponse(g)


async def api_approve(request: Request):
    pid, aid = request.path_params["pid"], request.path_params["aid"]
    body = await request.json()
    approver = RUNNER.approvers.get(pid)
    p = approver and approver.pending.get(aid)
    if not p:
        return JSONResponse({"error": "no such pending approval"}, 404)
    p.decision = Decision(bool(body.get("approved")), "manual", int(body.get("choice", 0)), body.get("note", ""))
    p.event.set()
    return JSONResponse({"ok": True})


async def api_message(request: Request):
    """The human writes to an agent (@role). The message is given as context on the agent's next call."""
    pid = request.path_params["pid"]
    body = await request.json()
    to, text = body.get("to"), (body.get("text") or "").strip()
    roles = {"director", "literature", "hypothesis", "experiment_designer", "engineer", "analyst", "critic", "verifier", "writer", "all"}
    if to not in roles or not text:
        return JSONResponse({"error": "a valid recipient and message are required"}, 400)
    s = open_store(pid)
    subject = (s.latest("hypothesis") or s.latest("question"))[-1]["id"]
    msg = s.put({"type": "message", "createdBy": {"kind": "human", "userId": WebApprover.user}, "kind": "proposal",
                 "to": to, "subjectId": subject, "body": text})
    s.close()
    return JSONResponse(msg, 201)


async def api_run(request: Request):
    pid = request.path_params["pid"]
    body = await request.json() if request.headers.get("content-length") else {}
    open_store(pid).close()
    RUNNER.start(pid, body.get("autonomy", "manual"), float(body.get("autoLimit", 0)))
    return JSONResponse({"running": True})


async def api_branch(request: Request):
    """Two forms: {"at": seq} branches off unchanged at an event (Phase 3);
    {"kind": hypothesis|approach|note, "reason", "hypothesis"?, "note"?, "atStage"?, "autonomy"?, "autoLimit"?}
    opens a modified branch and runs it right away (research tree)."""
    pid = request.path_params["pid"]
    body = await request.json()
    if body.get("kind"):
        from .tree import branch_project
        try:
            child = branch_project(HOME, pid, body["kind"], body.get("reason") or "", hypothesis=body.get("hypothesis"),
                                   note=body.get("note"), at_stage=body.get("atStage"),
                                   by={"kind": "human", "userId": WebApprover.user},
                                   budget=float(body["budget"]) if body.get("budget") else None)
        except (ValueError, FileNotFoundError) as exc:
            return JSONResponse({"error": str(exc)}, 400)
        RUNNER.start(child, body.get("autonomy", "manual"), float(body.get("autoLimit", 0)))
        return JSONResponse({"id": child}, 201)
    at = int(body["at"])
    name = f"{pid}-branch-{at}"
    s = open_store(pid)
    s.branch(HOME / name / "quaera.db", at)
    if (HOME / pid / "work").exists():
        import shutil
        shutil.copytree(HOME / pid / "work", HOME / name / "work", dirs_exist_ok=True)
    s.close()
    return JSONResponse({"id": name}, 201)


async def api_report_md(request: Request):
    path = report_file(HOME / request.path_params["pid"])
    if not path.exists():
        return PlainTextResponse("no report yet", 404)
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


async def api_export(request: Request):
    from .package import build_package
    pid = request.path_params["pid"]
    data = build_package(HOME / pid)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{pid}-evidence-package.zip"'})


async def api_replay(request: Request):
    """Event log + package for replay: needs no API key."""
    pid = request.path_params["pid"]
    s = open_store(pid)
    data = {"quaeraVersion": __version__, "project": pid, "title": s.meta("title"), "domain": s.meta("domain"),
            "stages": stages_for(s), "mode": s.meta("mode", "verify"), "events": s.events(),
            "report": report_file(HOME / pid).read_text(encoding="utf-8") if report_file(HOME / pid).exists() else None}
    s.close()
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{pid}-replay.json"'})


async def api_blob(request: Request):
    """Text from the project's content-addressed store: an agent's full reply, experiment code, a Lean file. Only that project's store."""
    import re
    pid, sha = request.path_params["pid"], request.path_params["sha"]
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        return JSONResponse({"error": "invalid hash"}, 400)
    try:
        s = open_store(pid)
    except FileNotFoundError:
        return JSONResponse({"error": "no such project"}, 404)
    try:
        data = s.blob(sha)
    except FileNotFoundError:
        return JSONResponse({"error": "no content"}, 404)
    finally:
        s.close()
    return PlainTextResponse(data[:400_000].decode("utf-8", "replace"))


async def api_tree(request: Request):
    from .tree import family
    pid = request.path_params["pid"]
    open_store(pid).close()
    data = family(HOME, pid)
    for n in data["nodes"]:
        n["running"] = RUNNER.running(n["id"])
    return JSONResponse(data)


async def api_iterate(request: Request):
    """Retries inconclusive branches as the Director suggests; every new branch passes web approval (or the autonomy rule)."""
    pid = request.path_params["pid"]
    open_store(pid).close()
    body = await request.json()
    try:
        max_branches, per = int(body.get("maxBranches", 2)), float(body["budgetPerBranch"])
    except (KeyError, ValueError):
        return JSONResponse({"error": "budgetPerBranch (USD) is required"}, 400)
    if not (1 <= max_branches <= 5 and 0 < per <= 20):
        return JSONResponse({"error": "1–5 branches and 0–20 USD per branch"}, 400)
    if RUNNER.running(pid):
        return JSONResponse({"error": "this branch is still running"}, 409)
    RUNNER.start_iterate(pid, max_branches, per, body.get("autonomy", "manual"), float(body.get("autoLimit", 0)))
    return JSONResponse({"started": True}, 202)


async def api_triage(request: Request):
    """"Report a problem" from the UI: the case is written locally under ~/.quaera/triage/; nothing is sent out."""
    from . import triage
    pid = request.path_params["pid"]
    open_store(pid).close()
    body = await request.json()
    try:
        path = triage.add_case(HOME, pid, body.get("note") or "", body.get("expect") or {})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, 400)
    return JSONResponse({"case": path.stem}, 201)


async def api_memory(request: Request):
    """Search (q), or without q every project in memory (newest first)."""
    from .memory import EVAL_PREFIXES, LabMemory, outcome
    q = request.query_params.get("q", "").strip()
    mem = LabMemory(HOME.parent / "memory.db")
    if q:
        items = mem.recall(q, k=10)
    else:
        with mem.lock:
            rows = mem.db.execute("SELECT summary FROM memory ORDER BY rowid DESC LIMIT 200").fetchall()
        items = [json.loads(r[0]) for r in rows]
        if request.query_params.get("evals") != "1":
            items = [e for e in items if not e["project"].startswith(EVAL_PREFIXES)]
    mem.close()
    return JSONResponse([{"project": e["project"], "title": e["title"], "domain": e["domain"], "outcome": outcome(e),
                          "answer": e.get("answer"), "reproduced": e.get("reproduced")} for e in items])


async def api_learnings(request: Request):
    """Contextual memory: lessons learned across projects (newest first)."""
    from .memory import LabMemory
    qp = request.query_params
    mem = LabMemory(HOME.parent / "memory.db")
    try:
        items = mem.learnings(limit=min(int(qp.get("limit", 60)), 300), project=qp.get("project") or None,
                              q=qp.get("q") or None, include_evals=qp.get("evals") == "1")
    finally:
        mem.close()
    titles: dict[str, str] = {}
    for it in items:   # the project may have been renamed after the lesson was saved: use the current short title
        if it["project"] not in titles:
            try:
                s = open_store(it["project"])
                titles[it["project"]] = s.meta("shortTitle") or it["title"]
                s.close()
            except FileNotFoundError:
                titles[it["project"]] = it["title"]
        it["title"] = titles[it["project"]]
    return JSONResponse(items)


async def api_settings(request: Request):
    import shutil
    from .providers import detect, enabled_ids
    allow = enabled_ids()
    found = await asyncio.to_thread(detect)
    for d in found:
        d["enabled"] = d["ready"] and (allow is None or d["id"] in allow)
    return JSONResponse({
        "version": __version__, "home": str(HOME),
        "providers": ["anthropic (claude CLI)"] + (["litellm"] if os.environ.get("QUAERA_LITELLM_MODELS") else []),
        "models": found, "managerCapUsd": float(os.environ.get("QUAERA_MANAGER_CAP_USD", "0.5")),
        "sandbox": {"mem": os.environ.get("QUAERA_SANDBOX_MEM", "2G"), "cpu": os.environ.get("QUAERA_SANDBOX_CPU", "200%"),
                    "leanMem": os.environ.get("QUAERA_LEAN_MEM", "7G"), "systemd": bool(shutil.which("systemd-run"))},
    })


PROVIDER_PROBES: dict = {}     # tests inject fake providers: {"codex": provider}


async def api_provider_test(request: Request):
    """Settings → "Test": asks the provider a small real question (Codex/OpenCode subscription; ~$0.01 on Claude)."""
    from .gateway import ClaudeCLIProvider
    from .providers import AntigravityCLIProvider, CodexCLIProvider, OpenCodeCLIProvider, detect, probe
    pid = request.path_params["id"]
    prov = PROVIDER_PROBES.get(pid)
    if prov is None:
        d = next((x for x in await asyncio.to_thread(detect) if x["id"] == pid), None)
        if not d or not d["ready"]:
            return JSONResponse({"ok": False, "error": (d or {}).get("note") or "unknown provider"}, 400)
        prov = {"claude": lambda: ClaudeCLIProvider(), "codex": lambda: CodexCLIProvider(d["binary"]),
                "opencode": lambda: OpenCodeCLIProvider(d["binary"]), "agy": lambda: AntigravityCLIProvider(d["binary"])}[pid]()
    return JSONResponse(await asyncio.to_thread(probe, prov))


async def index(request: Request):
    page = WEB_DIST / "index.html"
    if not page.exists():
        return PlainTextResponse("The web UI is not built: run `npm install && npm run build` in web/.", 503)
    return FileResponse(page)


# --- local security -------------------------------------------------------------------
# The server runs without authentication, on 127.0.0.1 only. To stop another site open in the browser
# (CSRF) or a DNS rebinding attack from starting research and spending budget:
#   - the Host header may only be 127.0.0.1 / localhost (DNS rebinding),
#   - if Origin is present it must be same-origin; requests the browser marks Sec-Fetch-Site: cross-site (img, form, link) are rejected,
#   - the POST body must be application/json (cross-origin requests then need a preflight, which is rejected).
LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}
# Behind a private reverse proxy (e.g. `tailscale serve`) the proxy's host name can be allowed explicitly:
# QUAERA_ALLOWED_HOSTS=myhost.tailnet.ts.net. The same-origin rules still apply.


class LocalOnly:
    def __init__(self, app):
        self.app = app
        extra = os.environ.get("QUAERA_ALLOWED_HOSTS", "")
        self.hosts = LOCAL_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        origin = headers.get("origin")
        problem = None
        if host.lower() not in self.hosts:
            problem = "only local access is allowed"
        elif headers.get("sec-fetch-site") == "cross-site":
            problem = "cross-site request rejected"
        elif origin and origin.split("://", 1)[-1].rsplit(":", 1)[0].lower() not in self.hosts:
            problem = "cross-site request rejected"
        elif scope["method"] not in ("GET", "HEAD") and not headers.get("content-type", "").startswith("application/json"):
            problem = "request body must be application/json"
        if problem:
            return await JSONResponse({"error": problem}, 403)(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app() -> Starlette:
    routes = [
        Route("/api/projects", api_projects),
        Route("/api/projects", api_create, methods=["POST"]),
        Route("/api/projects/{pid}", api_project),
        Route("/api/projects/{pid}/events", api_events),
        Route("/api/projects/{pid}/stream", api_stream),
        Route("/api/projects/{pid}/graph", api_graph),
        Route("/api/projects/{pid}/approvals/{aid}", api_approve, methods=["POST"]),
        Route("/api/projects/{pid}/messages", api_message, methods=["POST"]),
        Route("/api/projects/{pid}/run", api_run, methods=["POST"]),
        Route("/api/projects/{pid}/triage", api_triage, methods=["POST"]),
        Route("/api/projects/{pid}/tree", api_tree),
        Route("/api/projects/{pid}/blob/{sha}", api_blob),
        Route("/api/projects/{pid}/iterate", api_iterate, methods=["POST"]),
        Route("/api/projects/{pid}/branch", api_branch, methods=["POST"]),
        Route("/api/projects/{pid}/report.md", api_report_md),
        Route("/api/projects/{pid}/export.zip", api_export),
        Route("/api/projects/{pid}/replay.json", api_replay),
        Route("/api/settings", api_settings),
        Route("/api/providers/{id}/test", api_provider_test, methods=["POST"]),
        Route("/api/memory", api_memory),
        Route("/api/memory/learnings", api_learnings),
        Route("/api/projects/{pid}/manager", api_manager, methods=["GET", "POST"]),
        Route("/api/projects/{pid}/rename", api_rename, methods=["POST"]),
    ]
    if (WEB_DIST / "assets").exists():
        routes.append(Mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets"))
    routes.append(Route("/{path:path}", index))
    from starlette.middleware import Middleware
    return Starlette(routes=routes, middleware=[Middleware(LocalOnly)])


def serve(port: int = 8765) -> None:
    import uvicorn
    print(f"QuaeraLabs UI: http://127.0.0.1:{port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


__all__ = ["create_app", "serve", "WebApprover", "graph", "io", "zipfile"]
