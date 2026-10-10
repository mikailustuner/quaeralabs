"""Discovery mode (ADR 0017): a multi-model attack on an open problem, with results verifiable in Lean.

Differences from verification mode:
- The target is not narrowed: the question becomes a single precise claim and stays as it is. A restricted result
  does not replace the target; it is only reported as a "discovery".
- Idea generation is parallel and multi-model. Lanes are spread over different model families (Claude, Codex,
  OpenCode…) and different perspectives; the first lane is always free. Each strategy is cross-reviewed by a
  family other than its author's.
- The human picks a strategy (the others are fallbacks). The strategy becomes a chain of lemmas with Lean
  statements; the lemmas are attacked in rounds:
  - numerical testing,
  - proof search with different models,
  - a refutation attempt.
  A refuted lemma gets a repair request; if it cannot be repaired, the next strategy is tried.
- Synthesis: if every lemma is verified, the main theorem is assembled from these lemmas in Lean.

Honesty: the main claim counts as proved or refuted only if Lean accepts it (no sorry, standard axioms, a clean
recompilation by the Verifier). Verified lemmas, refuted intermediate claims and dead strategies are reported as
"discoveries" and written to the lab memory; they are not evidence for the main claim.
"""

from __future__ import annotations

import json
import os
import re

from . import prompts
from .gateway import BudgetExceeded, ModelError
from .orchestrator import (THEOREM, Orchestrator, SearchBudget, StopResearch, extract_lean, feedback_text, similar)
from .lean import statement_of
from .store import Store, now

DISCOVERY_STAGES = [
    "literature", "landscape", "target", "design", "ideation", "cross_review", "strategy_approval",
    "formalize", "statement_review", "program", "attack", "synthesis", "analysis", "result_review",
    "verification", "conclude", "report",
]
ROUNDS = int(os.environ.get("QUAERA_DISCOVERY_ROUNDS", "3"))
# Capacity plan R2: after the minimum rounds the attack continues while its budget share remains, up to this cap
# (subscription CLIs report $0, so the cap and the call/time budget bound them).
MAX_ROUNDS = max(ROUNDS, int(os.environ.get("QUAERA_DISCOVERY_MAX_ROUNDS", str(ROUNDS * 2))))
ATTACK_PARALLEL = max(1, int(os.environ.get("QUAERA_ATTACK_PARALLEL", "2")))   # open lemmas attacked at once (S4)
LANES = int(os.environ.get("QUAERA_IDEATION_LANES", "4"))
ATTACK_SHARE = 0.7          # attack rounds use at most 70% of the measured budget left at stage start
SORRY = "Proof contains `sorry`."
WEIGHTS = {"plausibility": 0.35, "barrierAwareness": 0.25, "novelty": 0.25, "testability": 0.15}


def lemma_file(lean: str, name: str) -> str:
    """Turns the lemma statement the model wrote into a compilable file under our name, with a `sorry` proof."""
    body = re.sub(r"^\s*import[^\n]*\n", "", lean.strip(), flags=re.M).strip()
    body = re.sub(r"^\s*(theorem|lemma)\s+[^\s(:{\[]+", f"theorem {name}", body, count=1)
    if not body.startswith("theorem"):
        body = f"theorem {name} : {body}"
    body = re.sub(r":=\s*(by)?[\s\S]*$", "", body).rstrip()
    return f"import Mathlib\n\n{body} := by\n  sorry\n"


def score_of(value) -> float:
    """Review score 0–10; if the model writes "7/10" or text, the first number is taken, otherwise 0."""
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))
    m = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return max(0.0, min(10.0, float(m.group()))) if m else 0.0


def strip_header(source: str) -> str:
    return re.sub(r"^\s*import[^\n]*\n", "", source, flags=re.M).strip()


class DiscoveryOrchestrator(Orchestrator):
    stages = DISCOVERY_STAGES

    # --- helpers ---------------------------------------------------------------------
    def families(self) -> list[str]:
        """Route ids, one per distinct family first (lanes and rounds rotate over them)."""
        keys = list(self.gateway.providers)
        seen, first, rest = set(), [], []
        for k in keys:
            (rest if self.gateway.family_of(k) in seen else first).append(k)
            seen.add(self.gateway.family_of(k))
        return first + rest

    def other_family(self, fam: str | None) -> str:
        """A route whose family differs from `fam` (a family name or a route id)."""
        keys = self.families()
        own = self.gateway.family_of(fam) if fam in self.gateway.providers else fam
        return next((k for k in keys if self.gateway.family_of(k) != own), keys[0])

    def _compile(self, role: str, source: str, approved: str | None = None, name: str = THEOREM) -> dict:
        return self.tools.call_json(role, "lean.compile", source=source, theorem=name, approved_statement=approved)

    def _landscape_text(self) -> str:
        land = self.state("landscape") or {}
        parts = []
        if land.get("approaches"):
            parts.append("Known approaches:\n" + "\n".join(f"- {a.get('name')}: {a.get('idea')} | best: {a.get('bestResult')} | stuck: {a.get('obstruction')}"
                                                           for a in land["approaches"][:8]))
        if land.get("barriers"):
            parts.append("Known barriers:\n" + "\n".join(f"- {b.get('name')}: {b.get('body')}" for b in land["barriers"][:6]))
        if land.get("openAngles"):
            parts.append("Open angles:\n" + "\n".join(f"- {x}" for x in land["openAngles"][:6]))
        return "\n\n".join(parts)

    def _target_text(self) -> str:
        h = self.selected_hypothesis()
        return f"Target claim: {h['statement']}\n{h.get('falsifiabilityNote', '')}"

    # --- stages --------------------------------------------------------------------------
    def stage_landscape(self) -> None:
        q, lit = self.question(), self.state("literature")
        out, _ = self.ask_json("literature", prompts.LANDSCAPE,
                               f"Question: {q['title']}\nScope: {q['scope']}\nLiterature summary: {lit['summary']}\n"
                               f"Mathlib declarations found: {', '.join(lit['mathlib']) or '—'}" + self.recall_memory(q), 6000,
                               default={"approaches": [], "barriers": [], "partialResults": [], "openAngles": []})
        self.set_state("landscape", {k: out.get(k) or [] for k in ("approaches", "barriers", "partialResults", "openAngles")})

    def stage_target(self) -> None:
        """Target claim: the question as it is (not narrowed). The human approves the target; strategies try to prove or refute it."""
        q = self.question()
        out, actor = self.ask_json("hypothesis", prompts.TARGET, f"Question: {q['title']}\nScope: {q['scope']}", 2000)
        if not out.get("statement"):
            raise StopResearch("the Hypothesis agent could not restate the question as a claim")
        h = self.store.put({"type": "hypothesis", "createdBy": actor, "questionId": q["id"], "statement": out["statement"],
                            "falsifiabilityNote": f"Negation: {out.get('negation') or '—'}",
                            "expectedSignal": "A Lean-verified proof of the claim or of its negation.", "status": "draft",
                            "scopeRelation": {"relation": "full", "note": out.get("note") or "The question itself, not narrowed."}})
        decision = self.ask_human(actor, "accept_hypothesis", h["id"], "Target claim for the discovery program (not narrowed):", 0.0,
                                  [f"{h['statement']} [fully answers the question]"])
        if not decision.approved:
            self.store.put({**h, "status": "rejected"}, by=self.human())
            raise StopResearch("the human did not approve the target claim")
        approval = {"by": self.human(), "at": now(), "decision": "approved", "autonomyLevel": decision.autonomy}
        self.store.put({**h, "status": "accepted", "approval": approval}, by=self.human())
        self.set_state("hypothesis_id", h["id"])

    def stage_design(self) -> None:
        super().stage_design()
        exp = self.store.latest("experiment")[-1]
        fams = ", ".join(self.families())
        self.store.put({**exp, "method": (
            f"Discovery program. Parallel ideation by {max(LANES, len(self.families()))} lanes across model families ({fams}) "
            "with creative lenses (one lane always free); cross-review of each strategy by a different family; the human picks a strategy; "
            f"the strategy becomes a chain of Lean lemmas attacked for up to {ROUNDS} rounds (numerical tests, proof search with "
            "rotating model families, refutation attempts, repair); finally synthesis of the main theorem from verified lemmas. "
            "Only Lean-verified statements count; the Verifier recompiles every verified file in a clean process.")},
            by=exp["createdBy"])

    def stage_ideation(self) -> None:
        q, fams = self.question(), self.families()
        n = max(LANES, len(fams))
        tried = self._tried_strategies()
        base = (f"Question: {q['title']}\n{self._target_text()}\n\n{self._landscape_text()}" + self.recall_memory(q)
                + self._branch_brief(tried))
        lanes = [(chr(65 + i), prompts.DISCOVERY_LENSES[i % len(prompts.DISCOVERY_LENSES)], fams[i % len(fams)]) for i in range(n)]

        def lane_job(lane, lens, fam):
            def run():
                try:
                    out, actor = self.ask_json("hypothesis", prompts.IDEATE,
                                               base + f"\n\nYour lens ({lens['name']}): {lens['text']}", 5000, lane=lane, family=fam)
                    items = out.get("strategies") if isinstance(out.get("strategies"), list) else []
                    return [(s, lane, lens, actor) for s in items[:2] if isinstance(s, dict) and s.get("title") and s.get("idea")]
                except StopResearch as exc:   # one provider going down does not stop the other lanes
                    self.store.append("ideation.lane_failed", self.det("hypothesis"), {"lane": lane, "family": fam, "error": str(exc)[:300]})
                    return []
            return run

        found = [x for res in self.parallel(*[lane_job(*l) for l in lanes], label="ideation lanes") for x in res]
        strategies = []
        for s, lane, lens, actor in found:
            if any(similar(s["title"] + " " + s["idea"], o["title"] + " " + o["idea"], 0.8) for o in strategies):
                continue
            if any(similar(s["title"], t, 0.8) for t in tried):          # already tried in an earlier branch of this line
                self.store.append("strategy.repeat_skipped", actor, {"title": s["title"]})
                continue
            rec = {"id": f"S{len(strategies) + 1}", "lane": lane, "lens": lens["id"], "lensName": lens["name"],
                   "family": actor["modelFamily"], "model": actor["model"], **{k: s.get(k) for k in
                   ("title", "idea", "keySteps", "barrierCheck", "killTest", "novelty", "direction")}}
            rec["direction"] = rec["direction"] if rec["direction"] in ("prove", "disprove") else "prove"
            strategies.append(rec)
            self.store.append("strategy.proposed", actor, rec)
        if not strategies:
            raise StopResearch("no lane proposed a usable strategy")
        self.set_state("strategies", strategies)

    def _tried_strategies(self) -> list[str]:
        """Strategy titles from earlier attempts of this research line (empty for a fresh project)."""
        from .tree import lineage
        home, me = self.store.path.parent.parent, self.store.path.parent.name
        if not (self.store.meta("branch") or {}).get("parent"):
            return []
        titles = []
        for pid in lineage(home, me)[:-1]:
            s = Store(home / pid / "quaera.db")
            try:
                titles += [e["payload"]["title"] for e in s.events("strategy.proposed")]
            finally:
                s.close()
        return list(dict.fromkeys(titles))

    def _branch_brief(self, tried: list[str]) -> str:
        br = self.store.meta("branch") or {}
        if br.get("kind") != "approach" and not tried:
            return ""
        return ("\n\nThis is a new attempt in a longer research line. Earlier attempts failed; do NOT propose these strategies "
                "again or cosmetic variants of them:\n" + "\n".join(f"- {t}" for t in tried[-30:])
                + (f"\nDirector's instructions for this attempt (what was learned, what to try instead): {br['note']}" if br.get("note") else ""))

    def stage_cross_review(self) -> None:
        """Each strategy is reviewed by a family other than its author's. With three or more families a committee of two
        independent reviewers scores it (capacity plan S3): one reviewer's judgment alone does not rank a strategy."""
        strategies = self.state("strategies")
        committee = len(set(self.gateway.families())) >= 3

        def reviewers(s) -> list[str]:
            own = s["family"]
            others = [k for k in self.families() if self.gateway.family_of(k) != own]
            return (others[:2] if committee else others[:1]) or [self.other_family(own)]

        def review(s, fam, tag):
            def run():
                try:
                    out, actor = self.ask_json("critic", prompts.CROSS_REVIEW,
                                               f"{self._target_text()}\n\n{self._landscape_text()}\n\nStrategy {s['id']}:\n"
                                               + json.dumps({k: s[k] for k in ('title', 'idea', 'keySteps', 'barrierCheck', 'killTest', 'novelty', 'direction')},
                                                            ensure_ascii=False), 2500, lane=tag, family=fam)
                except StopResearch as exc:
                    return s, None, str(exc)
                return s, (out, actor), None
            return run

        jobs = [review(s, fam, s["id"] + ("" if i == 0 else f"·{i + 1}")) for s in strategies for i, fam in enumerate(reviewers(s))]
        by_id: dict[str, list] = {}
        for s, res, err in self.parallel(*jobs, label="cross-reviews"):
            by_id.setdefault(s["id"], []).append((res, err))
        reviewed = []
        for s in strategies:
            done = [r for r, _ in by_id.get(s["id"], []) if r is not None]
            if not done:
                err = next((e for _, e in by_id.get(s["id"], []) if e), "no reviewer answered")
                reviewed.append({**s, "score": 0.0, "review": {"summary": f"review failed: {err[:200]}"}})
                continue
            scored = []
            for out, actor in done:
                vals = {k: score_of(out.get(k)) for k in WEIGHTS}
                sc = sum(vals[k] * w for k, w in WEIGHTS.items()) * (0.5 if out.get("fatalFlaw") else 1.0)
                scored.append((sc, vals, out, actor))
            sc, vals, out, actor = scored[0]
            score = round(sum(x[0] for x in scored) / len(scored), 2)
            members = [{"family": a["modelFamily"], "model": a["model"], "score": round(x, 2), "fatalFlaw": o.get("fatalFlaw")}
                       for x, _v, o, a in scored]
            s = {**s, "score": score, "review": {**vals, **{k: out.get(k) for k in ("fatalFlaw", "strongestPoint", "suggestedKillTest", "summary")},
                                                 "reviewerFamily": actor["modelFamily"], "reviewerModel": actor["model"],
                                                 **({"committee": members} if len(members) > 1 else {})}}
            self.store.append("strategy.reviewed", actor, {"id": s["id"], "score": s["score"], "crossFamily": actor["modelFamily"] != s["family"],
                                                           **s["review"]})
            reviewed.append(s)
        reviewed.sort(key=lambda x: -x["score"])
        self.set_state("strategies", reviewed)

    def stage_strategy_approval(self) -> None:
        exp, strategies = self.store.latest("experiment")[-1], self.state("strategies")[:6]
        remaining = round(self.gateway.remaining(), 4)
        options = [f"{s['title']} — aims to {s['direction']} · score {s['score']}/10 · proposed by {s['family']} ({s['lensName']})"
                   + (f" · reviewer: {s['review'].get('summary')}" if s.get("review", {}).get("summary") else "") for s in strategies]
        summary = (f"{exp['id']}: {exp['method']}\nBudget: ${remaining:.2f} metered; subscription CLIs report no per-call charge "
                   f"and are limited to {ROUNDS} attack rounds.")
        decision = self.ask_human(exp["createdBy"], "approve_experiment", exp["id"], summary, remaining, options)
        if not decision.approved:
            raise StopResearch("the human did not approve a strategy")
        chosen = strategies[min(decision.choice, len(strategies) - 1)]
        queue = [chosen["id"]] + [s["id"] for s in strategies if s["id"] != chosen["id"]]
        self.set_state("strategy_queue", queue)
        self.store.append("strategy.chosen", self.human(), {"id": chosen["id"], "fallbacks": queue[1:], "note": decision.note})
        approval = {"by": self.human(), "at": now(), "decision": "approved", "autonomyLevel": decision.autonomy}
        self.store.put({**exp, "status": "approved", "approval": approval,
                        "noveltyCheck": {**exp["noveltyCheck"], "userChoice": "proceed"},
                        "budget": {**exp["budget"], "estimatedUsd": remaining, "approvedUsd": remaining}}, by=self.human())

    def _strategy(self, sid: str) -> dict:
        return next(s for s in self.state("strategies") if s["id"] == sid)

    def _program(self, sid: str, note: str = "") -> dict | None:
        """Turns the strategy into a chain of lemmas with Lean statements; a statement that fails to compile is fixed once, else dropped."""
        s, formal = self._strategy(sid), self.state("formal")
        out, actor = self.ask_json("engineer", prompts.PROGRAM,
                                   f"Approved main theorem file:\n```lean\n{formal['source']}\n```\n\nStrategy {sid}:\n"
                                   + json.dumps({k: s.get(k) for k in ("title", "idea", "keySteps", "barrierCheck", "direction")}, ensure_ascii=False)
                                   + f"\n\n{self._landscape_text()}\n{note}"
                                   + self.bank_hints(f"{s.get('title')} {s.get('idea')} {formal['statement']}", k=6), 6000)
        lemmas = []
        items = [x for x in (out.get("lemmas") if isinstance(out.get("lemmas"), list) else []) if isinstance(x, dict)]
        for i, item in enumerate(items[:6], 1):
            name = f"quaera_L{i}"
            file = lemma_file(str(item.get("lean") or ""), name)
            rep = self._compile("engineer", file, name=name)
            if not (rep["compiled"] and set(rep["problems"]) <= {SORRY}):
                text, _ = self.guarded(self.llm, "engineer", prompts.FORMALIZE.replace("`quaera_main`", f"`{name}`"),
                                       f"Hypothesis: {item.get('statement')}\nYour previous file:\n```lean\n{file}\n```\n"
                                       f"Compiler feedback:\n{feedback_text(rep)}\nFix the statement (keep the name {name}).", 3000)
                file = lemma_file(extract_lean(text), name)
                rep = self._compile("engineer", file, name=name)
            ok = rep["compiled"] and set(rep["problems"]) <= {SORRY}
            lem = {"id": f"L{i}", "name": name, "statement": item.get("statement") or "", "kind": item.get("kind") or "new",
                   "dependsOn": item.get("dependsOn") or [], "numericCheck": item.get("numericCheck"), "file": file,
                   "lean": statement_of(file, name), "status": "open" if ok else "unformalized", "attempts": [],
                   "strategy": sid}
            lemmas.append(lem)
            self.store.append("program.lemma", actor, {k: v for k, v in lem.items() if k != "attempts"})
        if not any(l["status"] == "open" for l in lemmas):
            self.store.append("strategy.dead", self.det("director"), {"id": sid, "reason": "no lemma could be stated in Lean"})
            return None
        prog = {"strategy": sid, "lemmas": lemmas, "assembly": out.get("assembly") or ""}
        self.set_state("program", prog)
        return prog

    def stage_program(self) -> None:
        for sid in self.state("strategy_queue"):
            if self._program(sid):
                return
        raise StopResearch("no strategy could be turned into Lean lemmas")

    # --- attack ------------------------------------------------------------------------------
    def _ask(self, fam: str, share, role: str = "engineer"):
        """`share`: the stage's budget predicate (Gateway.share_cap). Lane A uses `fam`; other lanes rotate over the other
        families (best-of-N candidates on different models, K5)."""
        def ask(system: str, prompt: str, n: int, lane: str | None = None) -> str:
            if not share():
                raise SearchBudget("the attack used its budget share")
            prompt = prompt + self.human_notes(role)
            family = fam
            if lane and lane != "A":
                others = [k for k in self.families() if self.gateway.family_of(k) != self.gateway.family_of(fam)] or [fam]
                family = others[(ord(lane) - 66) % len(others)]
            try:
                completion, _ = self.gateway.call(role, system, prompt, n, family=family,
                                                  sample=(ord(lane) - 64) if lane and lane != "A" else None)
            except BudgetExceeded as exc:
                raise SearchBudget(f"budget cap: {exc}") from exc
            actor = {"kind": "agent", "role": role, "model": completion.model, "modelFamily": completion.family}
            self.actors[role] = actor
            self.said(actor, system, prompt, completion.text, lane=lane)
            return completion.text
        return ask

    def _record(self, lem: dict, status: str, rnd: int, fam: str, detail: str = "", source: str | None = None) -> None:
        sha = self.store.put_blob(source.encode("utf-8")) if source else None
        lem["attempts"].append({"round": rnd, "family": fam, "status": status, "detail": detail[:300], "sha256": sha})
        if status in ("verified", "refuted"):
            lem["status"] = status
            if sha:
                lem["sha256"] = sha
        self.store.append("lemma.status", self.det("engineer"), {"id": lem["id"], "name": lem["name"], "status": status, "round": rnd,
                                                                 "family": fam, "detail": detail[:300], "sha256": sha,
                                                                 "strategy": lem["strategy"]})

    def _numeric(self, lem: dict, fam: str) -> dict | None:
        """Tests the lemma on small cases with Python (exploration script). None if there is no environment."""
        workdir = str(self.store.path.parent / "work")
        try:
            text, actor = self.guarded(self.llm, "engineer", prompts.EXPLORE_MATH,
                                       f"Hypothesis: {lem['statement']}\nLean form:\n{lem['lean']}\n{lem.get('numericCheck') or ''}", 5000,
                                       family=fam)
            m = re.search(r"```python\s*\n(.*?)```", text, re.S)
            code = (m.group(1) if m else text).strip() + "\n"
            self.tools.call_json("engineer", "sandbox.write", workdir=workdir, path=f"explore_{lem['id']}.py", content=code,
                                 message=f"numeric check {lem['id']}")
            res = self.tools.call_json("engineer", "sandbox.exec", workdir=workdir, command=["python", f"explore_{lem['id']}.py"], timeout_s=120)
        except (StopResearch, Exception) as exc:   # without a sandbox/ML environment, numerical testing is skipped
            self.store.append("tool.error", self.det("engineer"), {"tool": "sandbox.exec", "error": str(exc)[:300]})
            return None
        found = re.search(r"QUAERA_EXPLORE\s+(\{.*\})", res.get("stdout", ""))
        try:
            return json.loads(found.group(1)) if found else None
        except json.JSONDecodeError:
            return None

    def _refute_lemma(self, lem: dict, fam: str, ask, hint: str = "") -> str | None:
        from .fidelity import refutation_file
        ref = refutation_file(lem["file"], lem["name"], proof="by\n  sorry")
        if not ref:
            return None
        try:
            text = ask(prompts.REFUTE, f"Original file:\n```lean\n{lem['file']}\n```\nState and prove exactly:\n```lean\n{ref[1]} := by\n```\n{hint}", 10000)
        except ModelError:   # if the model could not answer, refutation is skipped this round; the budget limit (SearchBudget) propagates up
            return None
        source = extract_lean(text)
        rep = self.tools.call_json("engineer", "lean.compile", source=source, theorem="quaera_refute", approved_statement=ref[1])
        return source if rep["verified"] else None

    def _reuse_from_bank(self, lem: dict, rnd: int) -> bool:
        """K1: a lemma with the same statement already verified in this lab is recompiled here under its own name;
        verified without a model call, recorded with its origin."""
        if self.bank is None:
            return False
        from .bank import rename
        from .lean import mathlib_rev
        hit = self.bank.exact(lem["lean"], mathlib_rev())
        if not hit:
            return False
        source = rename(hit["source"], hit["name"], lem["name"])
        rep = self.tools.call_json("engineer", "lean.compile", source=source, theorem=lem["name"], approved_statement=lem["lean"])
        if not rep.get("verified"):
            return False
        self.bank.mark_reused(hit["id"])
        self._record(lem, "verified", rnd, "bank", f"reused from the lemma bank (verified in {hit['project']}); recompiled here", source)
        self.store.append("bank.reused", self.det("engineer"), {"id": lem["id"], "bankId": hit["id"], "project": hit["project"]})
        return True

    def _attack_lemma(self, lem: dict, fam: str, rnd: int, share) -> str:
        from .prover import prove_search
        if rnd == 1 and self._reuse_from_bank(lem, rnd):
            return "verified"
        ask = self._ask(fam, share)
        if rnd == 1 and not lem.get("plausibleTried"):
            lem["plausibleTried"] = True
            found = self._plausible(lem["file"], lem["name"])        # K3: random testing before any model call
            if found:
                self._record(lem, "numeric", rnd, fam, f"plausible found a counterexample: {found[:200]}")
                src = self._refute_lemma(lem, fam, ask, f"Random testing (plausible) found: {found[:400]}")
                if src:
                    self._record(lem, "refuted", rnd, fam, "negation proved in Lean after a plausible counterexample", src)
                    return "refuted"
        if rnd == 1 and lem.get("numericCheck"):
            ex = self._numeric(lem, fam)
            if ex is not None:
                self._record(lem, "numeric", rnd, fam, f"checked {ex.get('checked')}; counterexample: {json.dumps(ex.get('counterexample'), ensure_ascii=False)}")
                if ex.get("counterexample"):
                    src = self._refute_lemma(lem, fam, ask, f"Numerical search found: {json.dumps(ex['counterexample'], ensure_ascii=False)}")
                    if src:
                        self._record(lem, "refuted", rnd, fam, "negation proved in Lean after a numerical counterexample", src)
                        return "refuted"

        def check(source: str, name: str | None = None, approved: str | None = None) -> dict:
            name = name or lem["name"]
            rep = self.tools.call_json("engineer", "lean.compile", source=source, theorem=name,
                                       approved_statement=lem["lean"] if name == lem["name"] else approved)
            rep["_ok"] = rep["verified"]
            return rep

        progress = lambda step, status, detail="", lane=None: self.activity("engineer", f"{lem['id']}: {step}", status, detail, lane)  # noqa: E731
        settings = self.attack_settings
        res = prove_search(lem["file"], lem["name"], ask, check, attempts=2, progress=progress, samples=settings["samples"],
                           heavy=rnd == 1, depth=settings["depth"], interactive=self.interactive("engineer"),
                           interactive_first=settings["interactive_first"],
                           hints=f"Research program step {lem['id']} ({lem['kind']}): {lem['statement']}"
                           + self.mathlib_hints(lem["lean"] or lem["statement"], 6) + self.bank_hints(lem["statement"], 3))
        if res.solved:
            self._record(lem, "verified", rnd, fam, f"proved after {res.attempts} model call(s)", res.source)
            return "verified"
        self._record(lem, "failed", rnd, fam, f"no proof in {res.attempts} model call(s)")
        if not lem.get("refuteTried"):
            lem["refuteTried"] = True
            src = self._refute_lemma(lem, fam, ask)
            if src:
                self._record(lem, "refuted", rnd, fam, "negation proved in Lean", src)
                return "refuted"
        return "open"

    def _repair(self, prog: dict, lem: dict, fam: str) -> bool:
        """Repairs a refuted lemma (same name, new statement) or declares the strategy dead."""
        out, actor = self.ask_json("hypothesis", prompts.REPAIR,
                                   f"{self._target_text()}\nStrategy: {self._strategy(prog['strategy'])['title']}\nRefuted lemma {lem['id']}: "
                                   f"{lem['statement']}\n```lean\n{lem['file']}\n```", 2500, family=self.other_family(fam))
        new = out.get("lemma") if isinstance(out.get("lemma"), dict) else {}
        if out.get("decision") != "repair" or not new.get("lean"):
            self.store.append("strategy.dead", actor, {"id": prog["strategy"], "reason": out.get("reason") or f"{lem['id']} refuted"})
            return False
        file = lemma_file(new["lean"], lem["name"])
        rep = self._compile("engineer", file, name=lem["name"])
        if not (rep["compiled"] and set(rep["problems"]) <= {SORRY}):
            self.store.append("strategy.dead", actor, {"id": prog["strategy"], "reason": f"repair of {lem['id']} did not compile"})
            return False
        rid = f"{lem['id']}'"
        repaired = {"id": rid, "name": lem["name"], "statement": new.get("statement") or "", "kind": lem["kind"],
                    "dependsOn": lem["dependsOn"], "numericCheck": lem.get("numericCheck"), "file": file,
                    "lean": statement_of(file, lem["name"]), "status": "open", "attempts": [], "strategy": lem["strategy"],
                    "repairOf": lem["id"]}
        prog["lemmas"].append(repaired)
        self.store.append("program.lemma", actor, {k: v for k, v in repaired.items() if k != "attempts"} | {"reason": out.get("reason")})
        return True

    def stage_attack(self) -> None:
        fams = self.families()
        share = self.gateway.share_cap(ATTACK_SHARE)
        self.attack_settings = self.search_settings("engineer")
        queue = self.state("strategy_queue")
        prog = self.state("program")
        stop = None
        for rnd in range(1, MAX_ROUNDS + 1):
            open_ = [l for l in prog["lemmas"] if l["status"] == "open"]
            if not open_:
                break
            if rnd > ROUNDS and not share():            # R2: extra rounds only while the budget share remains
                break
            self.store.append("attack.round", self.det("director"), {"round": rnd, "open": [l["id"] for l in open_], "strategy": prog["strategy"],
                                                                     **({"extra": True} if rnd > ROUNDS else {})})
            for start in range(0, len(open_), ATTACK_PARALLEL):
                batch = open_[start:start + ATTACK_PARALLEL]

                def job(lem, i):
                    def run():
                        try:
                            return lem, self._attack_lemma(lem, fams[(rnd - 1 + i) % len(fams)], rnd, share), None
                        except SearchBudget as exc:
                            return lem, None, str(exc)
                    return run
                # S4: independent lemmas are attacked concurrently (each on its own family this round); Lean checks queue
                # at the REPL, the budget reservation keeps the cap.
                results = self.parallel(*[job(lem, start + j) for j, lem in enumerate(batch)], label=f"lemma attacks (round {rnd})")
                stop = next((err for _, _, err in results if err), None)
                replaced = False
                for lem, status, _ in results:
                    if status != "refuted" or stop:
                        continue
                    fam = fams[(rnd - 1 + open_.index(lem)) % len(fams)]
                    if self._repair(prog, lem, fam):
                        continue                    # the repaired lemma joins the open ones in the next round
                    nxt = [sid for sid in queue if sid != prog["strategy"] and sid not in {e["payload"]["id"] for e in self.store.events("strategy.dead")}]
                    new = next((p for p in (self._program(sid, "The previous strategy died: " + lem["statement"]) for sid in nxt[:1]) if p), None)
                    if not new:
                        stop = "every strategy in the queue died"
                    else:
                        prog, replaced = new, True
                    break
                self.set_state("program", prog)
                if stop or replaced:
                    break           # stopped, or a new strategy's program: the next round plans afresh
            if stop:
                break
        self.set_state("program", prog)
        counts = {k: sum(1 for l in prog["lemmas"] if l["status"] == k) for k in ("verified", "refuted", "open", "unformalized")}
        self.store.append("attack.done", self.det("director"), {"strategy": prog["strategy"], **counts, "stopped": stop})

    def stage_synthesis(self) -> None:
        """If every lemma is verified, the main theorem (or its negation) is assembled from them in Lean."""
        from .prover import prove_search
        prog, formal = self.state("program"), self.state("formal")
        live = [l for l in prog["lemmas"] if l["status"] != "unformalized" and not any(x.get("repairOf") == l["id"] for x in prog["lemmas"])]
        if not live or any(l["status"] != "verified" for l in live):
            self.store.append("synthesis.skipped", self.det("director"), {"reason": "not every lemma of the program is verified",
                                                                         "verified": [l["id"] for l in live if l["status"] == "verified"]})
            self.set_state("proof", None)
            return
        lemma_text = "\n\n".join(strip_header(self.store.blob(l["sha256"]).decode()) for l in live)
        direction = self._strategy(prog["strategy"]).get("direction", "prove")
        ask = self._ask(self.families()[0], self.gateway.share_cap(0.5))
        if direction == "disprove":
            ok = self._try_refute(f"These lemmas are already verified; copy them (with proofs) above `quaera_refute`:\n```lean\n{lemma_text}\n```")
            self.store.append("synthesis.done", self.det("engineer"), {"direction": direction, "solved": ok})
            self.set_state("proof", None)
            return
        header, main = formal["source"].split("theorem " + THEOREM, 1)
        file = f"{header.rstrip()}\n\n{lemma_text}\n\ntheorem {THEOREM}{main}"
        verified_main: dict = {}

        def check(source: str, name: str | None = None, approved: str | None = None) -> dict:
            name = name or THEOREM
            started, actor = now(), self.actors.get("engineer") or self.det("engineer")
            rep = self.tools.call_json("engineer", "lean.compile", source=source, theorem=name,
                                       approved_statement=formal["statement"] if name == THEOREM else approved)
            rep["_ok"] = rep["verified"]
            art = self._lean_artifact(actor, source, rep)
            if name == THEOREM:
                run = self._run(actor, "full", len(self.store.latest("run")), rep, art, started)
                if rep["verified"]:
                    verified_main.update(run=run["id"], artifact=art["id"])
            return rep

        try:
            res = prove_search(file, THEOREM, ask, check, attempts=2, parallel=2, heavy=True,
                               interactive=self.interactive("engineer"),
                               hints="The lemmas quaera_L* above are already proved; combine them. " + prog.get("assembly", ""),
                               progress=lambda step, status, detail="", lane=None: self.activity("engineer", f"synthesis: {step}", status, detail, lane))
        except SearchBudget:
            res = None
        solved = bool(res and res.solved and verified_main)
        self.store.append("synthesis.done", self.det("engineer"), {"direction": direction, "solved": solved})
        self.set_state("proof", {"source": res.source, **verified_main} if solved else None)

    # --- analysis and verification: if the main claim is unresolved, "discoveries" are reported ---------------------
    def stage_analysis(self) -> None:
        if self.state("proof") or self.state("refutation"):
            return super().stage_analysis()
        h, prog = self.selected_hypothesis(), self.state("program") or {"lemmas": []}
        verified = [l for l in prog["lemmas"] if l["status"] == "verified"]
        refuted = [l for l in prog["lemmas"] if l["status"] == "refuted"]
        dead = {e["payload"]["id"] for e in self.store.events("strategy.dead")}
        summary = (f"The main claim was neither proved nor refuted. Discovery program: {len(verified)} lemma(s) verified in Lean, "
                   f"{len(refuted)} intermediate claim(s) refuted, {sum(1 for l in prog['lemmas'] if l['status'] == 'open')} left open; "
                   f"{len(dead)} strategy(ies) abandoned.")
        res = self.store.put({"type": "result", "createdBy": self.det("analyst"), "experimentId": self._experiment()["id"],
                              "runIds": [r["id"] for r in self.store.latest("run")][-1:], "negative": True, "summary": summary,
                              "limitations": ["Verified lemmas are steps of one strategy; they are not evidence for the main claim by themselves.",
                                              "Numerical checks are evidence, not proof."],
                              "leanProof": {"verified": False}})
        self.store.put({"type": "evidence_link", "createdBy": self.det("analyst"), "resultId": res["id"], "hypothesisId": h["id"],
                        "relation": "inconclusive", "independence": "L0", "context": "Discovery program; main claim open."})
        self.set_state("result_id", res["id"])

    def stage_verification(self) -> None:
        super().stage_verification()
        prog = self.state("program") or {"lemmas": []}
        for lem in prog["lemmas"]:
            if lem["status"] == "verified" and lem.get("sha256"):
                rep = self.tools.call_json("verifier", "lean.compile", source=self.store.blob(lem["sha256"]).decode(),
                                           theorem=lem["name"], approved_statement=lem["lean"])
                self.store.append("lemma.reverified", self.det("verifier"), {"id": lem["id"], "name": lem["name"], "verified": rep["verified"]})
                if rep["verified"]:
                    self.bank_add(lem["name"], lem["lean"], lem["statement"], self.store.blob(lem["sha256"]).decode())
