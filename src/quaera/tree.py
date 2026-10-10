"""Research tree: branches, the changes between them and their outcomes (Phase 4+).

A branch is a project's event log copied up to a given stage and rerun with one **change**:
  hypothesis   hypothesis changed   → branches after literature; the given statement replaces the Hypothesis agent
  approach     experiment/proof approach changed → branches after hypothesis approval; instructions go to the relevant agents
  note         free-form note       → branches after the chosen stage; the note goes to the relevant agents
The change and its reason are written to the child project's `branch` meta and event log (`branch.change`), and to the
parent's (`branch.spawned`). The tree view is built from these records: what changed (text diff), outcome, primary
metric and its delta from the parent, cost and the branches' success rates.

Iterative research (#5): when a branch ends without a conclusive result (no proof found, experiment inconclusive,
research stopped), the Director proposes what to change and — within the approval rules — a new branch is opened.
A refuted hypothesis is a result, not a failure; only inconclusive branches are iterated.

Tree search (capacity plan T1–T4, S3):
- every inconclusive node gets a progress score (verified lemmas, live strategies, review score, stages reached,
  open objections); the best `beam` nodes are expanded next, not simply the last branch (T1),
  each node at most MAX_CHILDREN times, so a dead end is left and an earlier, better node is tried again;
- `continue` branches keep a discovery program's lemmas and attack the open ones differently (T2);
- the Director may stop a line only when the logical frame is covered (every creative lens tried in discovery, both
  hypothesis and approach changes tried in verification) or a second model family agrees (T3, S3);
- the remaining budget is split across the selected nodes by their scores (T4); the line stops when any budget
  dimension (USD, calls, wall-clock) is spent.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .prompts import LANGUAGE
from .store import Store

CHANGE_STAGE = {"hypothesis": "literature", "approach": "hypothesis_approval", "continue": "program"}
NOTE_ROLES = {"hypothesis": ["hypothesis"], "approach": ["experiment_designer", "engineer"], "note": ["all"],
              "continue": ["engineer", "hypothesis"]}
KINDS = ("hypothesis", "approach", "note", "continue")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stage_seq(store: Store, stage: str) -> int:
    for e in store.events("stage.done"):
        if e["payload"]["stage"] == stage:
            return e["seq"]
    raise ValueError(f"stage '{stage}' was not completed in this project; cannot branch from here")


def branch_project(home: Path, pid: str, kind: str, reason: str, *, hypothesis: str | None = None,
                   note: str | None = None, at_stage: str | None = None, by: dict | None = None,
                   budget: float | None = None) -> str:
    """Opens a new branch with a change and returns the child project's name. Does not run it."""
    if kind not in KINDS:
        raise ValueError("change kind must be hypothesis, approach, note or continue")
    if len(reason.strip()) < 5:
        raise ValueError("a reason for the branch is required (what changed, and why?)")
    if kind == "hypothesis" and not (hypothesis and len(hypothesis.strip()) >= 8):
        raise ValueError("new hypothesis text is required")
    if kind in ("approach", "note", "continue") and not (note and note.strip()):
        raise ValueError("approach, note or continue instructions are required")
    src = Store(home / pid / "quaera.db")
    try:
        stage = CHANGE_STAGE.get(kind) or at_stage
        if kind == "approach" and src.meta("mode") == "discover":
            stage = "design"          # discovery keeps the target and reruns ideation with the history (see stage_ideation)
        if kind == "continue":        # T2: keep the lemma program (verified lemmas stay), re-attack the open ones
            if src.meta("mode") != "discover" or "program" not in [e["payload"]["stage"] for e in src.events("stage.done")]:
                raise ValueError("a continue branch needs a discovery project whose lemma program exists")
        if not stage:
            raise ValueError("a note branch requires the stage to branch from")
        at = stage_seq(src, stage)
        root = src.meta("root") or pid
        pattern = re.compile(rf"^{re.escape(pid)}-d(\d+)$")
        n = 1 + max((int(m.group(1)) for p in home.iterdir() if (m := pattern.match(p.name))), default=0)
        child = f"{pid}-d{n}"
        if (home / child).exists():
            raise ValueError(f"{child} already exists")
        src.branch(home / child / "quaera.db", at)
        if (home / pid / "work").exists():
            shutil.copytree(home / pid / "work", home / child / "work", dirs_exist_ok=True)
        by = by or {"kind": "human", "userId": "user"}
        info = {"parent": pid, "root": root, "atSeq": at, "atStage": stage, "kind": kind, "reason": reason.strip(),
                "hypothesis": hypothesis.strip() if hypothesis else None, "note": note.strip() if note else None,
                "by": by, "createdAt": _now()}
        src.append("branch.spawned", by, {"child": child, "kind": kind, "reason": info["reason"], "atStage": stage})
    finally:
        src.close()
    dst = Store(home / child / "quaera.db")
    try:
        dst.set_meta("root", root)
        dst.set_meta("branch", info)
        if budget is not None:
            dst.set_meta("budgetCapUsd", budget)
        dst.append("branch.change", by, info)
        subject = (dst.latest("hypothesis") or dst.latest("question"))[-1]["id"]
        if kind == "hypothesis":
            text = (f"The hypothesis was changed in this branch. Reason: {reason.strip()}" if by["kind"] == "human" else
                    f"The Director proposes this revised hypothesis: «{hypothesis.strip()}». Reason: {reason.strip()} "
                    "Write it in a testable form; make it more precise if needed, but do not change its essence.")
        else:
            text = note
        for role in NOTE_ROLES[kind]:
            dst.put({"type": "message", "createdBy": by, "kind": "proposal", "to": role, "subjectId": subject,
                     "body": f"[Branch change] {text}"})
    finally:
        dst.close()
    return child


# --- tree view -------------------------------------------------------------------------------------

def _states(store: Store) -> dict:
    return {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}


def node_summary(home: Path, pid: str) -> dict:
    s = Store(home / pid / "quaera.db")
    try:
        st = _states(s)
        objs = s.latest()
        hyp = next((o for o in objs if o["type"] == "hypothesis" and o["id"] == st.get("hypothesis_id")), None)
        prereg = next((o for o in reversed(objs) if o["type"] == "preregistration"), None)
        ver = next((o for o in reversed(objs) if o["type"] == "verification"), None)
        result = next((o for o in reversed(objs) if o["type"] == "result"), None)
        metric = None
        if prereg and isinstance(st.get("analysis"), dict):
            row = (st["analysis"].get("table") or {}).get(prereg.get("primaryMetric"))
            if isinstance(row, dict) and "mean" in row:
                metric = {"name": prereg["primaryMetric"], "mean": row["mean"], "ci95": row.get("ci95")}
        proof_ok = bool(result and (result.get("leanProof") or {}).get("verified"))
        answer = next((e["payload"].get("answer") for e in reversed(s.events("writer.output"))), None)
        stages_done = [e["payload"]["stage"] for e in s.events("stage.done")]
        status = hyp["status"] if hyp else None
        finished = bool(s.events("report.written"))
        outcome = ("running" if not finished else "stopped" if st.get("stopped") else
                   "supported" if status == "supported" else "refuted" if status == "refuted" else "inconclusive")
        return {"id": pid, "title": s.meta("title"), "domain": s.meta("domain", "math"), "branch": s.meta("branch"),
                "hypothesis": hyp and {"id": hyp["id"], "statement": hyp["statement"], "status": status,
                                       "scope": (hyp.get("scopeRelation") or {}).get("relation")},
                "design": prereg and {k: prereg.get(k) for k in ("primaryMetric", "successCriterion", "seeds")},
                "method": (st.get("plan") or {}).get("method") if isinstance(st.get("plan"), dict) else None,
                "formal": (st.get("formal") or {}).get("statement") if isinstance(st.get("formal"), dict) else None,
                "outcome": outcome, "answer": answer, "proofVerified": proof_ok,
                "reproduced": ver and ver.get("reproduced"), "metric": metric, "stopped": st.get("stopped"),
                "stagesDone": len(stages_done),
                # A branch copies the parent's events up to the branch point (seq 1..atSeq); only what follows is this branch's spend.
                "costUsd": round(sum(e["payload"]["costUsd"] for e in s.events("model.call")
                                     if e["seq"] > ((s.meta("branch") or {}).get("atSeq") or 0)), 4)}
    finally:
        s.close()


def word_diff(a: str | None, b: str | None) -> list[list]:
    """[["=", text] | ["-", text] | ["+", text]] — shown inline in the UI."""
    a_words, b_words = (a or "").split(), (b or "").split()
    out: list[list] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a_words, b=b_words, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(["=", " ".join(a_words[i1:i2])])
        if op in ("delete", "replace"):
            out.append(["-", " ".join(a_words[i1:i2])])
        if op in ("insert", "replace"):
            out.append(["+", " ".join(b_words[j1:j2])])
    return out


def family(home: Path, pid: str) -> dict:
    """The whole tree that pid belongs to: nodes, diffs against the parent, and rates."""
    s = Store(home / pid / "quaera.db")
    root = s.meta("root") or pid
    s.close()
    members = []
    for p in sorted(home.iterdir()):
        if not (p / "quaera.db").exists():
            continue
        if p.name == root:
            members.append(p.name)
            continue
        st = Store(p / "quaera.db")
        try:
            if st.meta("root") == root:
                members.append(p.name)
        finally:
            st.close()
    nodes = {m: node_summary(home, m) for m in members}
    for n in nodes.values():
        b = n["branch"]
        parent = nodes.get(b["parent"]) if b else None
        n["parent"] = b["parent"] if b else None
        if parent:
            ph, ch = (parent["hypothesis"] or {}).get("statement"), (n["hypothesis"] or {}).get("statement")
            n["diff"] = {
                "hypothesis": word_diff(ph, ch) if ph != ch and ch else None,
                "method": word_diff(parent["method"], n["method"]) if n["method"] and parent["method"] != n["method"] else None,
                "formal": word_diff(parent["formal"], n["formal"]) if n["formal"] and parent["formal"] != n["formal"] else None,
                "successCriterion": word_diff((parent["design"] or {}).get("successCriterion"), (n["design"] or {}).get("successCriterion"))
                if n["design"] and (parent["design"] or {}).get("successCriterion") != n["design"].get("successCriterion") else None,
            }
            pm, cm = parent["metric"], n["metric"]
            n["metricDelta"] = round(cm["mean"] - pm["mean"], 6) if pm and cm and pm["name"] == cm["name"] else None
        else:
            n["diff"], n["metricDelta"] = None, None
    for n in nodes.values():       # tree search view (T1): how promising each node is and whether it is exhausted
        n["progress"] = progress_score(home, n["id"])
        st = Store(home / n["id"] / "quaera.db")
        try:
            n["closed"] = bool(st.events("branch.closed"))
            dec = st.events("tree.decision")
            n["selections"] = len(dec)
            n["stopRejected"] = len(st.events("branch.stop_rejected"))
        finally:
            st.close()
    done = [n for n in nodes.values() if n["outcome"] != "running"]
    count = lambda o: sum(1 for n in done if n["outcome"] == o)  # noqa: E731
    successes = count("supported")
    best = max((n for n in done if n["metric"]), key=lambda n: n["metric"]["mean"], default=None)
    stats = {"branches": len(nodes), "finished": len(done),
             "supported": successes, "refuted": count("refuted"), "inconclusive": count("inconclusive"), "stopped": count("stopped"),
             "successRate": round(successes / len(done), 3) if done else None,
             "conclusiveRate": round((successes + count("refuted")) / len(done), 3) if done else None,
             "totalCostUsd": round(sum(n["costUsd"] for n in nodes.values()), 4),
             "costPerConclusive": round(sum(n["costUsd"] for n in nodes.values()) / (successes + count("refuted")), 4)
             if successes + count("refuted") else None,
             "bestMetric": best and {"project": best["id"], **best["metric"]}}
    return {"root": root, "nodes": list(nodes.values()), "stats": stats}


# --- iterative research (#5) -----------------------------------------------------------------------

REVISE = """You are the Director of a research team. A research branch ended WITHOUT a conclusive answer.
Decide whether a new branch is worth trying and what exactly to change. A cleanly refuted hypothesis is a result, not a failure.
You get the history of EVERY earlier attempt in this research line. Never repeat an attempt from the history (same hypothesis,
same strategy or a cosmetic variant of it); build on what was learned (which steps failed, which objections were fatal, which lemmas were verified).
Prefer a genuinely different direction once an approach has failed twice. Stop only when no new direction is left.
Options:
- "hypothesis": propose a revised hypothesis (e.g. a weaker/special case that is provable, a corrected formulation, a sharper claim)
- "approach": keep the hypothesis, change the experiment or proof approach (give concrete instructions to the designer/engineer).
  In discovery mode the target statement is fixed: use "approach" and describe the new strategy directions to explore and what to avoid.
- "continue": discovery only — keep the chosen strategy's lemma program (verified lemmas stay) and attack its OPEN lemmas
  differently (say how: other tactics, a reformulation, which lemma to split). Best when the program made progress.
- "stop": no promising change is left (explain why). You may stop only when the directions listed as untried below are
  genuinely hopeless; otherwise propose one of them.
Return only JSON: {"decision": "hypothesis"|"approach"|"continue"|"stop", "newHypothesis": "Turkish, only for hypothesis",
"instructions": "Turkish, concrete, for approach and continue", "reason": "Turkish, one or two sentences: what failed and why this change should help"}""".replace("Turkish", LANGUAGE)


def needs_iteration(summary: dict) -> bool:
    return summary["outcome"] in ("inconclusive", "stopped") and not str(summary.get("stopped") or "").startswith(("the human", "insan"))


def failure_context(home: Path, pid: str) -> str:
    s = Store(home / pid / "quaera.db")
    try:
        st = _states(s)
        crit = [c["body"][:300] for c in s.latest("critique") if c["status"] in ("open", "rejected_with_reason")]
        runs = [{"kind": r.get("kind"), "status": r.get("status"), "metrics": r.get("metrics")} for r in s.latest("run")][-6:]
        lean_errors = []
        for a in s.latest("artifact"):
            if a.get("kind") == "lean_output":
                try:
                    lean_errors.append(s.blob(a["sha256"]).decode("utf-8", "replace")[-600:])
                except FileNotFoundError:
                    pass
        return json.dumps({"stopped": st.get("stopped"), "openCritiques": crit, "lastRuns": runs,
                           "analysis": st.get("analysis"), "lastLeanOutput": lean_errors[-1:] if lean_errors else []},
                          ensure_ascii=False, default=str)[:6000]
    finally:
        s.close()


REVISE_CAP_USD = 0.5   # cap reserved for the Director's revision decision (on top of the branch budget, shown in the approval)
MIN_BRANCH_USD = 0.5   # keep-trying loop: a branch needs at least this much of the remaining total budget
MAX_ITERATIONS = int(os.environ.get("QUAERA_MAX_ITERATIONS", "10"))   # safety cap: subscription CLIs report $0 per call


def lineage(home: Path, pid: str) -> list[str]:
    """Projects from the root of the research line to `pid` (following each branch's parent)."""
    chain, cur = [], pid
    while cur and cur not in chain:
        chain.append(cur)
        s = Store(home / cur / "quaera.db")
        try:
            cur = (s.meta("branch") or {}).get("parent")
        finally:
            s.close()
    return chain[::-1]


def attempt_history(home: Path, pid: str, memory=None) -> str:
    """What every attempt in the line tried, how it ended and what was learned (input to the Director)."""
    out = []
    for p in lineage(home, pid):
        summ = node_summary(home, p)
        s = Store(home / p / "quaera.db")
        try:
            br = s.meta("branch") or {}
            reviews = {e["payload"]["id"]: e["payload"] for e in s.events("strategy.reviewed")}
            dead = {e["payload"]["id"]: e["payload"].get("reason") for e in s.events("strategy.dead")}
            strategies = [{"title": e["payload"]["title"], "fatalFlaw": (reviews.get(e["payload"]["id"]) or {}).get("fatalFlaw"),
                           "abandoned": dead.get(e["payload"]["id"])}
                          for e in s.events("strategy.proposed") if e["seq"] > (br.get("atSeq") or 0)]
            lemmas = Counter(e["payload"]["status"] for e in s.events("lemma.status") if e["seq"] > (br.get("atSeq") or 0))
            critiques = [c["body"][:200] for c in s.latest("critique") if c["status"] in ("open", "rejected_with_reason")][-3:]
        finally:
            s.close()
        out.append({"attempt": p, "change": br and {k: br.get(k) for k in ("kind", "reason", "hypothesis", "note")},
                    "hypothesis": (summ["hypothesis"] or {}).get("statement"), "outcome": summ["outcome"],
                    "stopped": summ["stopped"] and str(summ["stopped"])[:200], "strategies": strategies,
                    "lemmaStatuses": dict(lemmas), "openCritiques": critiques,
                    "lessons": [l["text"][:200] for l in memory.learnings(limit=6, project=p, include_evals=True)] if memory else []})
    return json.dumps(out, ensure_ascii=False, default=str)[-9000:]


def tried_hypotheses(home: Path, pid: str) -> list[str]:
    return [h for p in lineage(home, pid) if (h := ((node_summary(home, p)["hypothesis"] or {}).get("statement")))]


# --- tree search (T1–T4, S3) ------------------------------------------------------------------------------

MAX_CHILDREN = int(os.environ.get("QUAERA_MAX_CHILDREN", "3"))     # expansions per node before it counts as exhausted
WEIGHTS = {"verifiedLemmas": 1.0, "liveStrategies": 0.3, "bestReview": 0.1, "stages": 0.5, "openCritiques": -0.3, "depth": -0.15}


def _after(s: Store, kind: str) -> list[dict]:
    at = (s.meta("branch") or {}).get("atSeq") or 0
    return [e for e in s.events(kind) if e["seq"] > at]


def progress_score(home: Path, pid: str) -> dict:
    """How promising an inconclusive node is (T1). Inherited progress counts: a `continue` branch starts where its
    parent stood, so its verified lemmas are part of what it stands on."""
    from .cli import stages_for
    s = Store(home / pid / "quaera.db")
    try:
        verified = {e["payload"]["id"] for e in s.events("lemma.status") if e["payload"]["status"] == "verified"}
        proposed = {e["payload"]["id"] for e in _after(s, "strategy.proposed")} or {e["payload"]["id"] for e in s.events("strategy.proposed")}
        dead = {e["payload"]["id"] for e in s.events("strategy.dead")}
        reviews = [float(e["payload"].get("score") or 0) for e in s.events("strategy.reviewed")]
        stages = len({e["payload"]["stage"] for e in s.events("stage.done")}) / max(1, len(stages_for(s)))
        crit = sum(1 for c in s.latest("critique") if c["status"] == "open")
    finally:
        s.close()
    parts = {"verifiedLemmas": len(verified), "liveStrategies": len(proposed - dead), "bestReview": max(reviews, default=0.0),
             "stages": round(stages, 3), "openCritiques": crit, "depth": len(lineage(home, pid)) - 1}
    return {"score": round(sum(WEIGHTS[k] * v for k, v in parts.items()), 3), "parts": parts}


def children(home: Path, pid: str) -> list[str]:
    s = Store(home / pid / "quaera.db")
    try:
        return [e["payload"]["child"] for e in s.events("branch.spawned")]
    finally:
        s.close()


def closed(home: Path, pid: str) -> bool:
    s = Store(home / pid / "quaera.db")
    try:
        return bool(s.events("branch.closed"))
    finally:
        s.close()


def line_usage(home: Path, root: str) -> dict:
    """Spend of the whole research tree under `root`: USD and paid model calls after each branch point."""
    usd, calls = 0.0, 0
    for n in family(home, root)["nodes"]:
        s = Store(home / n["id"] / "quaera.db")
        try:
            paid = [e for e in _after(s, "model.call") if not e["payload"].get("cached")]
            usd += sum(e["payload"]["costUsd"] for e in paid)
            calls += len(paid)
        finally:
            s.close()
    return {"usd": round(usd, 6), "calls": calls}


def sibling_attempts(home: Path, pid: str) -> list[dict]:
    """The branches already opened from `pid`: what changed and how they ended (the Director must not repeat them)."""
    out = []
    for c in children(home, pid):
        if not (home / c / "quaera.db").exists():
            continue
        n = node_summary(home, c)
        br = n["branch"] or {}
        out.append({"branch": c, "kind": br.get("kind"), "reason": br.get("reason"), "instructions": br.get("note"),
                    "hypothesis": br.get("hypothesis"), "outcome": n["outcome"],
                    "progress": progress_score(home, c)["parts"] if n["outcome"] != "running" else None})
    return out


def coverage_gaps(home: Path, pid: str) -> list[str]:
    """Directions of the logical frame not tried yet in this line (T3): unused creative lenses in discovery; the change
    kinds never tried; `continue` when the program has verified lemmas but was never continued."""
    from .prompts import DISCOVERY_LENSES
    kinds, lenses, mode, verified = set(), set(), None, 0
    for p in list(dict.fromkeys(lineage(home, pid) + [c for c in children(home, pid) if (home / c / "quaera.db").exists()])):
        s = Store(home / p / "quaera.db")
        try:
            mode = mode or s.meta("mode") or "verify"
            kinds.add((s.meta("branch") or {}).get("kind"))
            lenses |= {e["payload"].get("lens") for e in s.events("strategy.proposed")}
            verified = max(verified, sum(1 for e in s.events("lemma.status") if e["payload"]["status"] == "verified"))
        finally:
            s.close()
    gaps = []
    if mode == "discover":
        gaps += [f"lens «{l['name']}» ({l['text'][:90]}…)" for l in DISCOVERY_LENSES if l["id"] not in lenses]
        if verified and "continue" not in kinds:
            gaps.append("continue the lemma program (it has verified lemmas) with a different attack on the open lemmas")
    else:
        gaps += [f"a {k} change" for k in ("hypothesis", "approach") if k not in kinds]
    return gaps


def _director(home: Path, pid: str, providers: dict, agent_specs: dict, memory, family_key: str | None = None,
              extra: str = "") -> tuple[dict | None, dict | None, str | None]:
    """One revision decision for node `pid` (with the repeat check). Returns (decision, actor, error)."""
    from .gateway import BudgetExceeded, Gateway, ModelError, parse_json
    from .orchestrator import similar
    summ = node_summary(home, pid)
    parent = Store(home / pid / "quaera.db")
    try:
        record = lambda kind, payload, _s=parent: _s.append(  # noqa: E731
            kind, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, payload)
        gw = Gateway(providers, REVISE_CAP_USD, agent_specs, record)
        hyp = (summ["hypothesis"] or {}).get("statement", "—")
        gaps = coverage_gaps(home, pid)
        ask = (f"Question: {summ['title']}\nMode: {parent.meta('mode') or 'verify'}\nTested hypothesis: {hyp}\nOutcome: {summ['outcome']}\n"
               f"What happened in the last attempt (JSON): {failure_context(home, pid)}\n\n"
               f"History of all attempts in this research line (JSON): {attempt_history(home, pid, memory)}\n\n"
               + (f"Directions NOT tried yet in this line: {'; '.join(gaps)}\n" if gaps else "All standard directions were tried.\n")
               + extra)
        siblings = sibling_attempts(home, pid)
        if siblings:
            ask += ("\nBranches ALREADY opened from this attempt (do not repeat them; build on what they found) (JSON): "
                    + json.dumps(siblings, ensure_ascii=False)[:4000] + "\n")
        tried = tried_hypotheses(home, pid) + [x["hypothesis"] for x in siblings if x.get("hypothesis")]
        decision, completion = None, None
        try:
            for _try in (1, 2):
                completion, _ = gw.call("director", REVISE, ask, 2000, family=family_key)
                decision = parse_json(completion.text)
                if not isinstance(decision, dict):
                    raise ValueError("decision is not an object")
                new = decision.get("newHypothesis") or ""
                if decision.get("decision") != "hypothesis" or not any(similar(new, t, 0.85) for t in tried):
                    break
                parent.append("branch.repeat_rejected", {"kind": "agent", "role": "director", "model": completion.model,
                                                         "modelFamily": completion.family}, {"newHypothesis": new})
                ask += f"\n\nREJECTED: «{new}» repeats an earlier attempt. Propose something genuinely different, or stop."
            else:
                decision = {"decision": "stop", "reason": "the Director only proposed hypotheses that were already tried"}
        except (BudgetExceeded, ModelError, ValueError) as exc:
            parent.append("branch.revision_failed", {"kind": "agent", "role": "director", "model": "quaera/deterministic",
                                                     "modelFamily": "quaera"}, {"error": str(exc)[:300]})
            return None, None, str(exc)
        if decision.get("decision") == "continue" and (parent.meta("mode") != "discover" or
                                                       "program" not in [e["payload"]["stage"] for e in parent.events("stage.done")]):
            decision = {**decision, "decision": "approach"}        # continue needs a lemma program; otherwise an approach change
        actor = {"kind": "agent", "role": "director", "model": completion.model, "modelFamily": completion.family}
        return decision, actor, None
    finally:
        parent.close()


def decide(home: Path, pid: str, providers: dict, agent_specs: dict, memory=None) -> tuple[dict | None, dict | None]:
    """The Director's decision with the stop rule (T3) and a second family's opinion (S3)."""
    decision, actor, err = _director(home, pid, providers, agent_specs, memory)
    if decision is None:
        return None, None
    if decision.get("decision") == "stop":
        gaps = coverage_gaps(home, pid)
        if gaps:
            # The frame is not covered: a second opinion, from another family when one exists, sees the untried directions.
            fams = {k: getattr(p, "family", k) for k, p in providers.items()}
            other = next((k for k, f in fams.items() if f != actor["modelFamily"]), None)
            second, actor2, _ = _director(home, pid, providers, agent_specs, memory, family_key=other,
                                          extra=f"\nAnother reviewer wanted to stop: «{decision.get('reason', '')}». Untried directions "
                                                f"remain: {'; '.join(gaps)}. Propose the most promising of them unless all are hopeless.")
            s = Store(home / pid / "quaera.db")
            try:
                if second and second.get("decision") in ("hypothesis", "approach", "continue"):
                    s.append("branch.stop_rejected", actor2, {"firstReason": decision.get("reason"), "gaps": gaps,
                                                              "secondFamily": actor2["modelFamily"]})
                    return second, actor2
                s.append("branch.stop_confirmed", actor2 or actor, {"reason": decision.get("reason"), "gaps": gaps,
                                                                    "secondReason": (second or {}).get("reason")})
            finally:
                s.close()
    return decision, actor


def iterate(home: Path, pid: str, *, build, providers: dict, agent_specs: dict, approve, max_branches: int,
            budget_per_branch: float | None = None, total_budget: float | None = None, memory=None, log=print,
            beam: int = 1, max_calls: int | None = None, deadline: float | None = None) -> list[str]:
    """Tree search over the research line of `pid` (T1). `build(path, budget) -> Orchestrator`, `approve(text, cost) -> bool`.
    Human approval (or an autonomy rule) is required before each new branch; every decision is recorded.

    `total_budget`: keep trying until the whole tree has spent this much (USD); `max_calls` / `deadline`: the same for
    paid model calls and wall-clock (P4). The selected nodes share the remaining budget by their scores (T4);
    `budget_per_branch` caps a single branch. `beam` > 1 runs that many branches concurrently."""
    import math
    import time
    s = Store(home / pid / "quaera.db")
    root = s.meta("root") or pid
    s.close()
    created: list[str] = []
    while len(created) < max_branches:
        tree = family(home, root)
        outcome = {n["id"]: n["outcome"] for n in tree["nodes"]}
        # a branch opened by this search (any of them, with beam > 1) reached a conclusive result: the search stops
        if any(outcome.get(c) in ("supported", "refuted") for c in created):
            log("a conclusive result was reached — the search stops")
            break
        frontier = [n for n in tree["nodes"] if needs_iteration(n) and not closed(home, n["id"])
                    and len(children(home, n["id"])) < MAX_CHILDREN and not any(c in [m["id"] for m in tree["nodes"] if m["outcome"] == "running"] for c in children(home, n["id"]))]
        if not frontier:
            if not created:
                summ = node_summary(home, pid)
                log(f"{pid}: outcome {summ['outcome']} — no iteration needed")
            else:
                log("no node left to expand")
            break
        usage = line_usage(home, root)
        if max_calls is not None and usage["calls"] >= max_calls:
            log(f"call budget reached: {usage['calls']} of {max_calls} model calls")
            break
        if deadline is not None and time.time() >= deadline:
            log("time budget reached")
            break
        remaining = None
        if total_budget is not None:
            remaining = total_budget - usage["usd"] - REVISE_CAP_USD
            if remaining < MIN_BRANCH_USD:
                log(f"budget reached: ${remaining + REVISE_CAP_USD:.2f} of ${total_budget:.2f} left")
                break
        scored = sorted(((progress_score(home, n["id"]), n) for n in frontier), key=lambda x: -x[0]["score"])
        picked = scored[:max(1, min(beam, max_branches - len(created)))]
        weights = [math.exp(sc["score"]) for sc, _ in picked]
        plans = []
        for (sc, n), w in zip(picked, weights):
            branch_budget = budget_per_branch
            if remaining is not None:
                share = max(MIN_BRANCH_USD, remaining * w / sum(weights))        # T4: budget follows promise
                branch_budget = min(budget_per_branch or share, share, remaining)
            if branch_budget is None:
                raise ValueError("budget_per_branch or total_budget is required")
            st = Store(home / n["id"] / "quaera.db")
            st.append("tree.decision", {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"},
                      {"selected": n["id"], "score": sc["score"], "parts": sc["parts"], "budgetUsd": round(branch_budget, 4),
                       "frontier": [{"id": m["id"], "score": x["score"]} for x, m in scored[:6]]})
            st.close()
            if not approve(f"{n['id']} ended without a conclusive result ({n['outcome']}). The Director will propose a change and the new branch "
                           f"will run with a budget of at most ${branch_budget:.2f}.", branch_budget + REVISE_CAP_USD):
                log("new branch not approved")
                return created
            decision, director = decide(home, n["id"], providers, agent_specs, memory)
            if decision is None:
                _close(home, n["id"], "the Director's revision failed")
                continue
            st = Store(home / n["id"] / "quaera.db")
            st.append("branch.proposed", director, decision)
            st.close()
            kind = decision.get("decision")
            if kind not in ("hypothesis", "approach", "continue"):
                log(f"the Director proposed no new branch for {n['id']}: {decision.get('reason', '')}")
                _close(home, n["id"], decision.get("reason") or "stop")
                continue
            try:
                child = branch_project(home, n["id"], kind, decision.get("reason") or "Director revision",
                                       hypothesis=decision.get("newHypothesis"), note=decision.get("instructions"),
                                       by=director, budget=branch_budget)
            except ValueError as exc:
                log(f"branch not opened for {n['id']}: {exc}")
                _close(home, n["id"], str(exc))
                continue
            log(f"new branch: {child} ({kind}) — {decision.get('reason', '')}")
            plans.append((child, branch_budget))
        if not plans:
            continue

        def run(item):
            child, budget = item
            orch = build(home / child, budget)
            try:
                orch.run()
            finally:
                orch.tools.close()
            return child
        if len(plans) == 1:
            created.append(run(plans[0]))
        else:   # S4: parallel branches are scheduler jobs (the global bound applies across projects)
            from .scheduler import SCHEDULER
            created += SCHEDULER.run_all([lambda item=item: run(item) for item in plans], project=root, label="tree branches")
    return created


def _close(home: Path, pid: str, reason: str) -> None:
    s = Store(home / pid / "quaera.db")
    try:
        s.append("branch.closed", {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"},
                 {"reason": str(reason)[:300]})
    finally:
        s.close()
