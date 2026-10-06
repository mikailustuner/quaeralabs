"""Araştırma ağacı: dallar, aralarındaki değişiklikler ve sonuçlar (Faz 4+).

Bir dal, bir projenin olay kaydının belli bir aşamaya kadar kopyalanıp bir **değişiklikle** yeniden çalıştırılmasıdır:
  hypothesis   hipotez değişti      → literatürden sonra dallanır; Hipotez ajanı yerine verilen ifade kullanılır
  approach     deney/ispat yaklaşımı değişti → hipotez onayından sonra dallanır; talimat ilgili ajanlara iletilir
  note         serbest not          → seçilen aşamadan sonra dallanır; not ilgili ajanlara iletilir
Değişiklik ve gerekçesi çocuk projenin `branch` meta verisine ve olay kaydına (`branch.change`), ebeveyninkine de
(`branch.spawned`) yazılır. Ağaç görünümü bu kayıtlardan kurulur: neyin değiştiği (metin farkı), sonuç, birincil
metrik ve ebeveyne göre farkı, maliyet ve dalların başarı oranları.

Yinelemeli araştırma (#5): bir dal sonuçsuz kaldığında (ispat bulunamadı, deney belirsiz, araştırma durdu) Direktör
neyin değiştirilmesi gerektiğini önerir ve — onay kuralları içinde — yeni dal açılır. Çürütülmüş bir hipotez
başarısızlık değil sonuçtur; yalnızca sonuçsuz dallar yinelenir.
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

CHANGE_STAGE = {"hypothesis": "literature", "approach": "hypothesis_approval"}
NOTE_ROLES = {"hypothesis": ["hypothesis"], "approach": ["experiment_designer", "engineer"], "note": ["all"]}


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
    """Değişiklikli yeni dal açar ve çocuk projenin adını döner. Çalıştırmaz."""
    if kind not in ("hypothesis", "approach", "note"):
        raise ValueError("change kind must be hypothesis, approach or note")
    if len(reason.strip()) < 5:
        raise ValueError("a reason for the branch is required (what changed, and why?)")
    if kind == "hypothesis" and not (hypothesis and len(hypothesis.strip()) >= 8):
        raise ValueError("new hypothesis text is required")
    if kind in ("approach", "note") and not (note and note.strip()):
        raise ValueError("approach or note text is required")
    src = Store(home / pid / "quaera.db")
    try:
        stage = CHANGE_STAGE.get(kind) or at_stage
        if kind == "approach" and src.meta("mode") == "discover":
            stage = "design"          # discovery keeps the target and reruns ideation with the history (see stage_ideation)
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


# --- ağaç görünümü ---------------------------------------------------------------------------------

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
                # Dal, ebeveynin olaylarını dallanma noktasına kadar kopyalar (seq 1..atSeq); yalnızca sonrası bu dalın harcamasıdır.
                "costUsd": round(sum(e["payload"]["costUsd"] for e in s.events("model.call")
                                     if e["seq"] > ((s.meta("branch") or {}).get("atSeq") or 0)), 4)}
    finally:
        s.close()


def word_diff(a: str | None, b: str | None) -> list[list]:
    """[["=", metin] | ["-", metin] | ["+", metin]] — arayüzde satır içi gösterilir."""
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
    """pid'in ait olduğu ağacın tamamı: düğümler, ebeveyne göre farklar ve oranlar."""
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


# --- yinelemeli araştırma (#5) ---------------------------------------------------------------------

REVISE = """You are the Director of a research team. A research branch ended WITHOUT a conclusive answer.
Decide whether a new branch is worth trying and what exactly to change. A cleanly refuted hypothesis is a result, not a failure.
You get the history of EVERY earlier attempt in this research line. Never repeat an attempt from the history (same hypothesis,
same strategy or a cosmetic variant of it); build on what was learned (which steps failed, which objections were fatal, which lemmas were verified).
Prefer a genuinely different direction once an approach has failed twice. Stop only when no new direction is left.
Options:
- "hypothesis": propose a revised hypothesis (e.g. a weaker/special case that is provable, a corrected formulation, a sharper claim)
- "approach": keep the hypothesis, change the experiment or proof approach (give concrete instructions to the designer/engineer).
  In discovery mode the target statement is fixed: use "approach" and describe the new strategy directions to explore and what to avoid.
- "stop": no promising change (explain why)
Return only JSON: {"decision": "hypothesis"|"approach"|"stop", "newHypothesis": "Turkish, only for hypothesis",
"instructions": "Turkish, concrete, only for approach", "reason": "Turkish, one or two sentences: what failed and why this change should help"}""".replace("Turkish", LANGUAGE)


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


REVISE_CAP_USD = 0.5   # Direktör'ün revizyon kararı için ayrılan üst sınır (dal bütçesine ek, onayda gösterilir)
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


def iterate(home: Path, pid: str, *, build, providers: dict, agent_specs: dict, approve, max_branches: int,
            budget_per_branch: float | None = None, total_budget: float | None = None, memory=None, log=print) -> list[str]:
    """Sonuçsuz dalları yineler. `build(path, budget) -> Orchestrator`, `approve(metin, maliyet) -> bool`.
    Her yeni dal açılmadan önce insan onayı (ya da otonomi kuralı) istenir; Direktör'ün kararı kayda geçer.

    `total_budget`: keep trying until the whole research line (root and all branches) has spent this much; each
    branch then gets the remaining budget (capped by `budget_per_branch` if given)."""
    from .gateway import BudgetExceeded, Gateway, ModelError, parse_json
    from .orchestrator import similar
    created: list[str] = []
    current = pid
    for _ in range(max_branches):
        summ = node_summary(home, current)
        if not needs_iteration(summ):
            log(f"{current}: sonuç {summ['outcome']} — yineleme gerekmiyor")
            break
        branch_budget = budget_per_branch
        if total_budget is not None:
            remaining = total_budget - sum(node_summary(home, p)["costUsd"] for p in lineage(home, current)) - REVISE_CAP_USD
            if remaining < MIN_BRANCH_USD:
                log(f"budget reached: ${remaining + REVISE_CAP_USD:.2f} of ${total_budget:.2f} left")
                break
            branch_budget = min(budget_per_branch or remaining, remaining)
        cost = branch_budget + REVISE_CAP_USD
        if not approve(f"{current} ended without a conclusive result ({summ['outcome']}). The Director will propose a change and the new branch "
                       f"will run with a budget of at most ${branch_budget:.2f}.", cost):
            log("yeni dal onaylanmadı")
            break
        parent = Store(home / current / "quaera.db")
        record = lambda kind, payload, _s=parent: _s.append(  # noqa: E731
            kind, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, payload)
        gw = Gateway(providers, REVISE_CAP_USD, agent_specs, record)
        hyp = (summ["hypothesis"] or {}).get("statement", "—")
        ask = (f"Question: {summ['title']}\nMode: {parent.meta('mode') or 'verify'}\nTested hypothesis: {hyp}\nOutcome: {summ['outcome']}\n"
               f"What happened in the last attempt (JSON): {failure_context(home, current)}\n\n"
               f"History of all attempts in this research line (JSON): {attempt_history(home, current, memory)}")
        tried = tried_hypotheses(home, current)
        decision, completion = None, None
        try:
            for _try in (1, 2):
                completion, _ = gw.call("director", REVISE, ask, 2000)
                decision = parse_json(completion.text)
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
            parent.close()
            break
        director = {"kind": "agent", "role": "director", "model": completion.model, "modelFamily": completion.family}
        parent.append("branch.proposed", director, decision)
        parent.close()
        kind = decision.get("decision")
        if kind not in ("hypothesis", "approach"):
            log(f"Direktör yeni dal önermedi: {decision.get('reason', '')}")
            break
        child = branch_project(home, current, kind, decision.get("reason") or "Director revision",
                               hypothesis=decision.get("newHypothesis"), note=decision.get("instructions"),
                               by=director, budget=branch_budget)
        log(f"yeni dal: {child} ({kind}) — {decision.get('reason', '')}")
        orch = build(home / child, branch_budget)
        try:
            orch.run()
        finally:
            orch.tools.close()
        created.append(child)
        current = child
    return created
