"""Keşif kipi (ADR 0017): açık bir probleme çok modelli saldırı, Lean ile doğrulanabilir sonuçlar.

Doğrulama kipinden farkları:
- Hedef daraltılmaz: soru tek bir kesin iddiaya çevrilir ve olduğu gibi kalır. Kısıtlı bir sonuç hedefin yerine
  geçmez; yalnızca "keşif" olarak raporlanır.
- Fikir üretimi paralel ve çok modellidir. Şeritler farklı model ailelerine (Claude, Codex, OpenCode…) ve farklı
  bakış açılarına dağıtılır; ilk şerit her zaman serbesttir. Her strateji, yazarından farklı bir aileye çapraz
  inceletilir.
- İnsan bir strateji seçer (diğerleri yedektir). Strateji Lean ifadeleri olan bir lemma zincirine çevrilir;
  lemmalara turlar halinde saldırılır:
  - sayısal sınama,
  - farklı modellerle ispat araması,
  - çürütme denemesi.
  Çürütülen bir lemma için onarım istenir; onarılamazsa sıradaki stratejiye geçilir.
- Sentez: bütün lemmalar doğrulanırsa ana teorem bu lemmalarla Lean'de kurulmaya çalışılır.

Dürüstlük: ana iddia yalnızca Lean (sorry yok, standart aksiyomlar, Doğrulayıcı'nın temiz yeniden derlemesi)
kabul ederse ispatlanmış ya da çürütülmüş sayılır. Doğrulanan lemmalar, çürütülen ara iddialar ve ölen stratejiler
"keşif" olarak raporlanır ve laboratuvar hafızasına yazılır; bunlar ana iddianın kanıtı değildir.
"""

from __future__ import annotations

import json
import os
import re

from . import prompts
from .gateway import BudgetExceeded, ModelError
from .orchestrator import (THEOREM, Orchestrator, SearchBudget, StopResearch, extract_lean, feedback_text, similar)
from .lean import statement_of
from .store import now

DISCOVERY_STAGES = [
    "literature", "landscape", "target", "design", "ideation", "cross_review", "strategy_approval",
    "formalize", "statement_review", "program", "attack", "synthesis", "analysis", "result_review",
    "verification", "conclude", "report",
]
ROUNDS = int(os.environ.get("QUAERA_DISCOVERY_ROUNDS", "3"))
LANES = int(os.environ.get("QUAERA_IDEATION_LANES", "4"))
ATTACK_SHARE = 0.7          # saldırı turları, aşama başında kalan ölçülen bütçenin en fazla %70'ini kullanır
SORRY = "Proof contains `sorry`."
WEIGHTS = {"plausibility": 0.35, "barrierAwareness": 0.25, "novelty": 0.25, "testability": 0.15}


def lemma_file(lean: str, name: str) -> str:
    """Modelin yazdığı lemma ifadesini bizim adımızla, ispatı `sorry` olan derlenebilir bir dosyaya çevirir."""
    body = re.sub(r"^\s*import[^\n]*\n", "", lean.strip(), flags=re.M).strip()
    body = re.sub(r"^\s*(theorem|lemma)\s+[^\s(:{\[]+", f"theorem {name}", body, count=1)
    if not body.startswith("theorem"):
        body = f"theorem {name} : {body}"
    body = re.sub(r":=\s*(by)?[\s\S]*$", "", body).rstrip()
    return f"import Mathlib\n\n{body} := by\n  sorry\n"


def score_of(value) -> float:
    """İnceleme puanı 0–10; model "7/10" ya da metin yazarsa ilk sayı alınır, yoksa 0."""
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))
    m = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return max(0.0, min(10.0, float(m.group()))) if m else 0.0


def strip_header(source: str) -> str:
    return re.sub(r"^\s*import[^\n]*\n", "", source, flags=re.M).strip()


class DiscoveryOrchestrator(Orchestrator):
    stages = DISCOVERY_STAGES

    # --- yardımcılar ---------------------------------------------------------------------
    def families(self) -> list[str]:
        return list(self.gateway.providers)

    def other_family(self, fam: str | None) -> str:
        fams = self.families()
        return next((f for f in fams if f != fam), fams[0])

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

    # --- aşamalar --------------------------------------------------------------------------
    def stage_landscape(self) -> None:
        q, lit = self.question(), self.state("literature")
        out, _ = self.ask_json("literature", prompts.LANDSCAPE,
                               f"Question: {q['title']}\nScope: {q['scope']}\nLiterature summary: {lit['summary']}\n"
                               f"Mathlib declarations found: {', '.join(lit['mathlib']) or '—'}" + self.recall_memory(q), 6000)
        self.set_state("landscape", {k: out.get(k) or [] for k in ("approaches", "barriers", "partialResults", "openAngles")})

    def stage_target(self) -> None:
        """Hedef iddia: soru olduğu gibi (daraltılmadan). İnsan hedefi onaylar; stratejiler onu ispat ya da çürütmeye çalışır."""
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
        base = (f"Question: {q['title']}\n{self._target_text()}\n\n{self._landscape_text()}" + self.recall_memory(q))
        lanes = [(chr(65 + i), prompts.DISCOVERY_LENSES[i % len(prompts.DISCOVERY_LENSES)], fams[i % len(fams)]) for i in range(n)]

        def lane_job(lane, lens, fam):
            def run():
                try:
                    out, actor = self.ask_json("hypothesis", prompts.IDEATE,
                                               base + f"\n\nYour lens ({lens['name']}): {lens['text']}", 5000, lane=lane, family=fam)
                    items = out.get("strategies") if isinstance(out.get("strategies"), list) else []
                    return [(s, lane, lens, actor) for s in items[:2] if isinstance(s, dict) and s.get("title") and s.get("idea")]
                except StopResearch as exc:   # bir sağlayıcının düşmesi diğer şeritleri durdurmaz
                    self.store.append("ideation.lane_failed", self.det("hypothesis"), {"lane": lane, "family": fam, "error": str(exc)[:300]})
                    return []
            return run

        found = [x for res in self.parallel(*[lane_job(*l) for l in lanes]) for x in res]
        strategies = []
        for s, lane, lens, actor in found:
            if any(similar(s["title"] + " " + s["idea"], o["title"] + " " + o["idea"], 0.8) for o in strategies):
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

    def stage_cross_review(self) -> None:
        strategies = self.state("strategies")

        def review(s):
            def run():
                fam = self.other_family(s["family"])
                try:
                    out, actor = self.ask_json("critic", prompts.CROSS_REVIEW,
                                               f"{self._target_text()}\n\n{self._landscape_text()}\n\nStrategy {s['id']}:\n"
                                               + json.dumps({k: s[k] for k in ('title', 'idea', 'keySteps', 'barrierCheck', 'killTest', 'novelty', 'direction')},
                                                            ensure_ascii=False), 2500, lane=s["id"], family=fam)
                except StopResearch as exc:
                    return s, None, str(exc)
                return s, (out, actor), None
            return run

        reviewed = []
        for s, res, err in self.parallel(*[review(s) for s in strategies]):
            if res is None:
                s = {**s, "score": 0.0, "review": {"summary": f"review failed: {err[:200]}"}}
            else:
                out, actor = res
                vals = {k: score_of(out.get(k)) for k in WEIGHTS}
                score = sum(vals[k] * w for k, w in WEIGHTS.items())
                if out.get("fatalFlaw"):
                    score *= 0.5
                s = {**s, "score": round(score, 2), "review": {**vals, **{k: out.get(k) for k in ("fatalFlaw", "strongestPoint", "suggestedKillTest", "summary")},
                                                               "reviewerFamily": actor["modelFamily"], "reviewerModel": actor["model"]}}
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
        """Stratejiyi Lean ifadeli lemma zincirine çevirir; derlenmeyen ifade bir kez düzeltilir, olmazsa atılır."""
        s, formal = self._strategy(sid), self.state("formal")
        out, actor = self.ask_json("engineer", prompts.PROGRAM,
                                   f"Approved main theorem file:\n```lean\n{formal['source']}\n```\n\nStrategy {sid}:\n"
                                   + json.dumps({k: s.get(k) for k in ("title", "idea", "keySteps", "barrierCheck", "direction")}, ensure_ascii=False)
                                   + f"\n\n{self._landscape_text()}\n{note}", 6000)
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

    # --- saldırı ------------------------------------------------------------------------------
    def _ask(self, fam: str, cap: float, role: str = "engineer"):
        def ask(system: str, prompt: str, n: int, lane: str | None = None) -> str:
            if self.gateway.spent_usd >= cap:
                raise SearchBudget("the attack used its budget share")
            prompt = prompt + self.human_notes(role)
            family = self.other_family(fam) if lane == "B" else fam
            try:
                completion, _ = self.gateway.call(role, system, prompt, n, family=family)
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
        """Lemmayı küçük durumlarda Python ile sınar (keşif betiği). Ortam yoksa None."""
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
        except (StopResearch, Exception) as exc:   # sandbox/ML ortamı yoksa sayısal sınama atlanır
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
        except ModelError:   # model yanıt veremediyse çürütme bu tur atlanır; bütçe sınırı (SearchBudget) yukarı iletilir
            return None
        source = extract_lean(text)
        rep = self.tools.call_json("engineer", "lean.compile", source=source, theorem="quaera_refute", approved_statement=ref[1])
        return source if rep["verified"] else None

    def _attack_lemma(self, lem: dict, fam: str, rnd: int, cap: float) -> str:
        from .prover import prove_search
        ask = self._ask(fam, cap)
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
        res = prove_search(lem["file"], lem["name"], ask, check, attempts=2, progress=progress, parallel=2,
                           hints=f"Research program step {lem['id']} ({lem['kind']}): {lem['statement']}")
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
        """Çürütülen lemmayı onarır (aynı ad, yeni ifade) ya da stratejiyi ölü ilan eder."""
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
        cap = self.gateway.spent_usd + ATTACK_SHARE * self.gateway.remaining()
        queue = self.state("strategy_queue")
        prog = self.state("program")
        stop = None
        for rnd in range(1, ROUNDS + 1):
            open_ = [l for l in prog["lemmas"] if l["status"] == "open"]
            if not open_:
                break
            self.store.append("attack.round", self.det("director"), {"round": rnd, "open": [l["id"] for l in open_], "strategy": prog["strategy"]})
            for i, lem in enumerate(open_):
                fam = fams[(rnd - 1 + i) % len(fams)]          # her turda lemma başka bir model ailesine gider
                try:
                    status = self._attack_lemma(lem, fam, rnd, cap)
                except SearchBudget as exc:
                    stop = str(exc)
                    break
                self.set_state("program", prog)
                if status == "refuted" and not self._repair(prog, lem, fam):
                    nxt = [sid for sid in queue if sid != prog["strategy"] and sid not in {e["payload"]["id"] for e in self.store.events("strategy.dead")}]
                    new = next((p for p in (self._program(sid, "The previous strategy died: " + lem["statement"]) for sid in nxt[:1]) if p), None)
                    if not new:
                        stop = "every strategy in the queue died"
                        break
                    prog = new
                    break
                self.set_state("program", prog)
            if stop:
                break
        self.set_state("program", prog)
        counts = {k: sum(1 for l in prog["lemmas"] if l["status"] == k) for k in ("verified", "refuted", "open", "unformalized")}
        self.store.append("attack.done", self.det("director"), {"strategy": prog["strategy"], **counts, "stopped": stop})

    def stage_synthesis(self) -> None:
        """Bütün lemmalar doğrulandıysa ana teorem (ya da olumsuzu) onlarla Lean'de kurulmaya çalışılır."""
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
        cap = self.gateway.spent_usd + 0.5 * self.gateway.remaining()
        ask = self._ask(self.families()[0], cap)
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
            res = prove_search(file, THEOREM, ask, check, attempts=2, parallel=2,
                               hints="The lemmas quaera_L* above are already proved; combine them. " + prog.get("assembly", ""),
                               progress=lambda step, status, detail="", lane=None: self.activity("engineer", f"synthesis: {step}", status, detail, lane))
        except SearchBudget:
            res = None
        solved = bool(res and res.solved and verified_main)
        self.store.append("synthesis.done", self.det("engineer"), {"direction": direction, "solved": solved})
        self.set_state("proof", {"source": res.source, **verified_main} if solved else None)

    # --- analiz ve doğrulama: ana iddia çözülmediyse "keşifler" raporlanır ---------------------
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
