"""Orkestratör: kalıcı araştırma döngüsü (ADR 0005). Faz 1 kapsamı: matematik.

Her aşama tamamlandığında olay kaydına `stage.done` yazılır. Süreç herhangi bir anda
kesilirse `resume` tamamlanmış aşamaları atlar ve kaldığı aşamadan devam eder.

Faz 1'de LLM ile çalışan roller: Literatür, Hipotez, Mühendis, Eleştirmen.
Direktör (aşama geçişleri) ve matematikte şablonla çalışan Deney tasarımcısı, Analist,
Doğrulayıcı ve Yazar deterministiktir; aktör kaydında model `quaera/deterministic` olarak görünür.
"""

from __future__ import annotations

import getpass
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import prompts
from .contracts import prereg_hash
from .gateway import BudgetExceeded, Gateway, ModelError, parse_json
from .lean import DEFAULT_WORKSPACE, normalize, statement_of
from .permissions import Permissions
from .store import IntegrityError, Store, now
from .tools import ToolRegistry

STAGES = [
    "literature", "hypothesis", "hypothesis_approval", "explore", "design", "experiment_approval",
    "formalize", "statement_review", "prove", "analysis", "result_review", "verification", "conclude", "report",
]
DETERMINISTIC = {"model": "quaera/deterministic", "modelFamily": "quaera"}
THEOREM = "quaera_main"


PROVE_BUDGET_SHARE = 0.5


class SearchBudget(Exception):
    """İspat araması kendi bütçe payını kullandı; araştırma sürer (sonuç: ispat bulunamadı)."""


class StopResearch(Exception):
    """Araştırma kontrollü olarak sonlanıyor (ör. bütçe bitti, insan durdurdu)."""


# --- insan onayı ------------------------------------------------------------------

@dataclass
class Decision:
    approved: bool
    autonomy: str = "manual"
    choice: int = 0
    note: str = ""


class Approver:
    user = getpass.getuser()

    def decide(self, action: str, summary: str, cost_usd: float, options: list[str] | None = None) -> Decision:
        raise NotImplementedError


class InteractiveApprover(Approver):
    def decide(self, action, summary, cost_usd, options=None):
        print(f"\n[ONAY] {action} · tahmini maliyet ${cost_usd:.2f}\n{summary}")
        if options:
            for i, o in enumerate(options, 1):
                print(f"  {i}. {o}")
            raw = input("Seçiminiz (numara, boş = 1, h = reddet): ").strip().lower()
            if raw == "h":
                return Decision(False)
            return Decision(True, choice=int(raw) - 1 if raw else 0)
        return Decision(input("Onaylıyor musunuz? [e/h]: ").strip().lower().startswith("e"))


class AutoApprover(Approver):
    """Otonomi seviyesi 'auto_under_limit': insan, belirlediği tutarın altındaki onayları önceden vermiştir."""

    def __init__(self, limit_usd: float):
        self.limit_usd = limit_usd

    def decide(self, action, summary, cost_usd, options=None):
        if cost_usd > self.limit_usd:
            return Decision(False, "auto_under_limit", note=f"${cost_usd:.2f} > pre-approved ${self.limit_usd:.2f}")
        return Decision(True, "auto_under_limit", 0, f"within the pre-approved ${self.limit_usd:.2f} limit")


class CapApprover(Approver):
    """Otonomi seviyesi 3 'auto_to_budget_cap': bütçe tavanına kadar her harcama otomatik onaylanır.
    Yayınlama ve tavanı yükseltme bu kipte de insana kalır (döngüde zaten bu eylemler yoktur)."""

    def __init__(self, gateway):
        self.gateway = gateway

    def decide(self, action, summary, cost_usd, options=None):
        if action in ("publish", "raise_budget_cap"):
            return Decision(False, "auto_to_budget_cap", note="this action always belongs to a human")
        if cost_usd > self.gateway.remaining() + 1e-9:
            return Decision(False, "auto_to_budget_cap", note="exceeds the remaining budget")
        return Decision(True, "auto_to_budget_cap", 0, "auto-approved within the budget cap")


# --- orkestratör -------------------------------------------------------------------

@dataclass
class Orchestrator:
    stages = STAGES
    store: Store
    gateway: Gateway
    tools: ToolRegistry
    approver: Approver
    permissions: Permissions
    log: Callable[[str], None] = print
    reports_dir: Path | None = None
    actors: dict = field(default_factory=dict)
    memory: object | None = None          # memory.LabMemory; None ise hafıza kullanılmaz (testler, değerlendirmeler)

    # yardımcılar ------------------------------------------------------------
    def human(self) -> dict:
        return {"kind": "human", "userId": self.approver.user}

    def det(self, role: str) -> dict:
        return {"kind": "agent", "role": role, **DETERMINISTIC}

    def human_notes(self, role: str) -> str:
        """İnsanın bu role (@rol) ya da herkese yazdığı ve henüz iletilmemiş notlar; iletildikleri kayda geçer."""
        delivered = {(e["payload"]["msg"], e["payload"]["role"]) for e in self.store.events("message.delivered")}
        # İnsanın notları ve Direktör'ün dal revizyon önerileri (kind=proposal) ilgili role bir kez iletilir.
        notes = [m for m in self.store.latest("message")
                 if (m["createdBy"]["kind"] == "human" or (m["createdBy"].get("role") == "director" and m["kind"] == "proposal"))
                 and m["to"] in (role, "all") and (m["id"], role) not in delivered]
        for m in notes:
            self.store.append("message.delivered", self.det("director"), {"msg": m["id"], "role": role})
        if not notes:
            return ""
        return "\n\nNotes from the human researcher or the Director (consider them; they cannot override safety or budget rules):\n" + \
            "\n".join(f"- [{m['id']}] {m['body']}" for m in notes)

    def activity(self, role: str, step: str, status: str = "start", detail: str = "", lane: str | None = None) -> None:
        """Canlı görünüm: bir ajanın şu an ne yaptığı (başladı / bitti / başarısız). Arayüz aktif ajan kartında gösterir."""
        self.store.append("activity", self.det(role), {"role": role, "step": step, "status": status,
                                                         "detail": detail[:400], "lane": lane})

    def said(self, actor: dict, system: str, prompt: str, text: str, lane: str | None = None) -> None:
        """Ajanın söylediği her şey kayda geçer: tam metin içerik adresli depoda, önizleme olayda. Ajana tıklayınca görünür."""
        sha = self.store.put_blob(text.encode("utf-8"))
        self.store.append("agent.said", actor, {"role": actor["role"], "purpose": purpose_of(system), "lane": lane,
                                                "sha256": sha, "chars": len(text), "preview": text[:800],
                                                "promptPreview": prompt[:600]})

    def llm(self, role: str, system: str, prompt: str, max_output_tokens: int = 8000, lane: str | None = None,
            family: str | None = None) -> tuple[str, dict]:
        prompt = prompt + self.human_notes(role)
        purpose = purpose_of(system)
        self.activity(role, purpose, "start", lane=lane)
        for attempt in (1, 2):
            try:
                completion, cross = self.gateway.call(role, system, prompt, max_output_tokens, family=family or self.lane_family(lane))
                break
            except ModelError as exc:
                if attempt == 2:
                    self.activity(role, purpose, "fail", str(exc)[:200], lane=lane)
                    raise StopResearch(f"{role} model failed to respond in two attempts: {exc}") from exc
        actor = {"kind": "agent", "role": role, "model": completion.model, "modelFamily": completion.family}
        self.actors[role] = actor
        self.said(actor, system, prompt, completion.text, lane=lane)
        self.activity(role, purpose, "done", lane=lane)
        return completion.text, actor

    def lane_family(self, lane: str | None) -> str | None:
        """Paralel ikinci şerit (B), birden fazla sağlayıcı varsa farklı model ailesiyle çalışır (çapraz model çeşitliliği)."""
        families = list(self.gateway.providers)
        return families[1] if lane == "B" and len(families) > 1 else None

    def parallel(self, *calls):
        """Bağımsız işleri aynı anda çalıştırır (ör. iki Hipotez ajanı). Bütçe tavanı paralelde de kesin (Gateway rezervasyonu)."""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = [pool.submit(fn) for fn in calls]
            return [f.result() for f in futures]

    def ask_json(self, role: str, system: str, prompt: str, max_output_tokens: int = 8000, lane: str | None = None,
                 family: str | None = None) -> tuple[dict, dict]:
        """JSON bekleyen çağrı: geçersiz çıktıda bir kez düzeltme istenir, olmazsa araştırma kontrollü durur."""
        text, actor = self.guarded(self.llm, role, system, prompt, max_output_tokens, lane=lane, family=family)
        try:
            return as_object(parse_json(text)), actor
        except (ModelError, ValueError) as first:
            self.store.append("model.invalid_json", actor, {"role": role, "preview": text[:300]})
            retry = (prompt + "\n\nYour previous answer was not valid JSON (possibly cut off). "
                     "Return ONLY the JSON object, complete and shorter.")
            text, actor = self.guarded(self.llm, role, system, retry, max_output_tokens, lane=lane, family=family)
            try:
                return as_object(parse_json(text)), actor
            except (ModelError, ValueError) as exc:
                raise StopResearch(f"{role} did not return valid JSON in two attempts: {first}") from exc

    def done_stages(self) -> list[str]:
        return [e["payload"]["stage"] for e in self.store.events("stage.done")]

    def mark(self, stage: str, **info) -> None:
        self.store.append("stage.done", self.det("director"), {"stage": stage, **info})

    def state(self, key: str, default=None):
        evs = self.store.events("state")
        for e in reversed(evs):
            if e["payload"]["key"] == key:
                return e["payload"]["value"]
        return default

    def set_state(self, key: str, value) -> None:
        self.store.append("state", self.det("director"), {"key": key, "value": value})

    def message(self, actor: dict, kind: str, to: str, subject: str, body: str, reply_to: str | None = None,
                round_: int | None = None, approval: dict | None = None) -> dict:
        msg = {"type": "message", "createdBy": actor, "kind": kind, "to": to, "subjectId": subject, "body": body}
        if reply_to:
            msg["inReplyTo"] = reply_to
        if round_:
            msg["round"] = round_
        if kind in ("question", "objection"):
            msg["requiresResponse"] = True
        if approval:
            msg["approvalRequest"] = approval
        return self.store.put(msg)

    def ask_human(self, actor: dict, action: str, subject: str, summary: str, cost: float,
                  options: list[str] | None = None) -> Decision:
        decision = self.approver.decide(action, summary, cost, options)
        record = {"by": self.human(), "at": now(), "decision": "approved" if decision.approved else "rejected",
                  "autonomyLevel": decision.autonomy}
        if decision.note:
            record["note"] = decision.note
        self.message(actor, "approval_request", "human", subject, summary,
                     approval={"action": action, "estimatedUsd": round(cost, 4), "decision": record})
        return decision

    def question(self) -> dict:
        return self.store.latest("question")[0]

    def selected_hypothesis(self) -> dict:
        return self.store.get(self.state("hypothesis_id"))

    # ana döngü ---------------------------------------------------------------
    def run(self) -> str:
        done = set(self.done_stages())
        try:
            for stage in self.stages:
                if stage in done:
                    continue
                if self.state("stopped") and stage not in ("conclude", "report"):
                    continue
                self.log(f"▶ {stage}")
                getattr(self, f"stage_{stage}")()
                self.mark(stage)
                if stage in ("analysis", "verification", "conclude", "report"):
                    self.learn()
        except StopResearch as exc:
            self.log(f"■ durduruldu: {exc}")
            self.set_state("stopped", str(exc))
            for stage in ("conclude", "report"):
                if stage not in self.done_stages():
                    getattr(self, f"stage_{stage}")()
                    self.mark(stage)
            self.learn()
        return self.state("report_path", "")

    def guarded(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BudgetExceeded as exc:
            raise StopResearch(f"budget cap: {exc}") from exc
        except ModelError as exc:  # ayrıştırılamayan model çıktısı vb.
            raise StopResearch(f"model error: {exc}") from exc

    # aşamalar ---------------------------------------------------------------
    def stage_literature(self) -> None:
        from .literature import run_literature
        q = self.question()
        llm = lambda system, prompt, n: self.guarded(self.llm, "literature", system, prompt, n)  # noqa: E731
        lit = run_literature(q["title"], q["scope"], q["domain"], llm, self.tools)
        for r in lit.rejected:
            self.store.append("citation.rejected", lit.actor, r)
        for e in lit.tool_errors:
            self.store.append("tool.error", lit.actor, e)
        papers = []
        for v in lit.verified:
            art = self.store.put({"type": "artifact", "createdBy": lit.actor, "kind": "paper", "uri": v["ref"],
                                  "provider": "arxiv" if v["kind"] == "arxiv" else "doi", "citationVerified": True})
            papers.append(art["id"])
        self.set_state("literature", {"summary": lit.summary, "verdict": lit.verdict, "basis": lit.basis,
                                      "papers": papers, "mathlib": lit.mathlib})

    def stage_hypothesis(self) -> None:
        q, lit = self.question(), self.state("literature")
        branch = self.store.meta("branch") or {}
        if branch.get("kind") == "hypothesis" and branch.get("hypothesis") and (branch.get("by") or {}).get("kind") == "human":
            # İnsanın dal değişikliği: hipotezi insan verdi, kendi adıyla kayda geçer; Hipotez ajanı çağrılmaz.
            # (Direktör'ün önerdiği revizyon ise Hipotez ajanına mesaj olarak gider; hipotezi ajan yazar.)
            self.store.put({"type": "hypothesis", "createdBy": branch["by"], "questionId": q["id"], "statement": branch["hypothesis"],
                            "falsifiabilityNote": f"Branch change: {branch.get('reason', '')}", "status": "draft"})
            return
        system = prompts.HYPOTHESIS_ML if q["domain"] == "ml" else prompts.HYPOTHESIS
        base = (f"Question: {q['title']}\nScope: {q['scope']}\nLiterature summary: {lit['summary']}"
                + self._profile_text() + self.recall_memory(q))
        # #4: iki bakış açısıyla üret, neredeyse aynı olanları ayıkla, Eleştirmen'e puanlat, en iyileri insana sun.
        cands, actor = [], None
        # İki Hipotez ajanı aynı anda, iki farklı bakış açısıyla çalışır (şerit A ve B).
        outs = self.parallel(*[(lambda lens=lens, lane=lane: self.ask_json("hypothesis", system, base + lens, 4000, lane=lane))
                               for lens, lane in zip(prompts.HYPOTHESIS_LENSES, "AB")])
        for out, actor in outs:
            for h in out.get("hypotheses", []):
                if h.get("statement") and not any(similar(h["statement"], c["statement"]) for c in cands):
                    cands.append(h)
        if not cands:
            raise StopResearch("the Hypothesis agent produced no testable hypothesis")
        keep = self.permissions.spec("hypothesis")["limits"].get("maxItemsPerTurn", 5)
        written = 0
        for h in self._rank_hypotheses(q, lit, cands):
            if written >= min(keep, 3):
                break
            obj = {"type": "hypothesis", "createdBy": actor, "questionId": q["id"], "statement": h["statement"],
                   "falsifiabilityNote": h.get("falsifiabilityNote", ""), "expectedSignal": h.get("expectedSignal", ""), "status": "draft"}
            scope = h.get("scope") or {}
            if scope.get("relation") in ("full", "restricted", "related"):
                obj["scopeRelation"] = {"relation": scope["relation"], "note": scope.get("note", "")}
            try:
                self.store.put(obj)
                written += 1
            except IntegrityError as exc:   # sözleşmeye uymayan tek bir aday araştırmayı çökertmesin; sıradaki aday denenir
                self.store.append("hypothesis.invalid", actor, {"statement": h["statement"][:300], "error": str(exc)[:300]})
        if not written:
            raise StopResearch("none of the Hypothesis agent's candidates satisfied the contract")

    def _rank_hypotheses(self, q: dict, lit: dict, cands: list[dict]) -> list[dict]:
        """Adayları Eleştirmen'e puanlatır; puan ve gerekçe hem kayda (hypotheses.ranked) hem onay kartına girer."""
        if len(cands) < 2:
            return cands
        listing = "\n".join(f"[{i}] {c['statement']} (scope: {(c.get('scope') or {}).get('relation', '?')})" for i, c in enumerate(cands))
        try:
            out, critic = self.ask_json("critic", prompts.RANK_HYPOTHESES,
                                        f"Question: {q['title']}\nScope: {q['scope']}\nLiterature summary: {lit['summary']}"
                                        + self._profile_text() + f"\n\nCandidates:\n{listing}", 2500)
        except StopResearch:
            return cands
        weights = {"testability": 0.3, "plausibility": 0.2, "novelty": 0.15, "scope": 0.25, "cost": 0.1}
        scored = []
        for r in out.get("ranking", []):
            i = r.get("index")
            if isinstance(i, int) and 0 <= i < len(cands) and i not in {x[1] for x in scored}:
                score = round(sum(float(r.get(k, 0)) * w for k, w in weights.items()), 2)
                scored.append((score, i, r))
        if not scored:
            return cands
        scored.sort(key=lambda x: -x[0])
        ranked = [cands[i] for _, i, _ in scored] + [c for j, c in enumerate(cands) if j not in {i for _, i, _ in scored}]
        for score, i, r in scored:
            cands[i]["_score"], cands[i]["_reason"] = score, r.get("reason", "")
        self.store.append("hypotheses.ranked", critic, {"weights": weights, "candidates": [
            {"statement": cands[i]["statement"], "score": score, **{k: r.get(k) for k in weights}, "reason": r.get("reason", "")}
            for score, i, r in scored]})
        return ranked

    def _profile_text(self) -> str:
        prof = self.state("data_profile")
        return f"\n\nData profile (computed deterministically from the files under /data):\n{json.dumps(prof, ensure_ascii=False)[:4000]}" if prof else ""

    def _exploration_text(self) -> str:
        ex = self.state("exploration")
        if not ex:
            return ""
        return ("\n\nNumerical exploration of the hypothesis (Python, small cases; evidence, not proof): "
                + json.dumps(ex, ensure_ascii=False)[:2500])

    def stage_explore(self) -> None:
        """#3 deneysel matematik: ispattan önce hipotezi küçük durumlarda sınar ve karşı örnek arar."""
        h = self.selected_hypothesis()
        workdir = str(self.store.path.parent / "work")
        prompt = f"Hypothesis: {h['statement']}\nQuestion: {self.question()['title']}"
        for attempt in range(2):
            text, actor = self.guarded(self.llm, "engineer", prompts.EXPLORE_MATH, prompt, 5000)
            m = re.search(r"```python\s*\n(.*?)```", text, re.S)
            code = (m.group(1) if m else text).strip() + "\n"
            try:
                self.tools.call_json("engineer", "sandbox.write", workdir=workdir, path="explore.py", content=code,
                                     message=f"exploration {attempt}")
                res = self.tools.call_json("engineer", "sandbox.exec", workdir=workdir, command=["python", "explore.py"], timeout_s=120)
            except Exception as exc:   # ML ortamı kurulu değilse keşif atlanır; araştırma sürer
                self.store.append("tool.error", actor, {"tool": "sandbox.exec", "error": str(exc)[:300]})
                return
            found = re.search(r"QUAERA_EXPLORE\s+(\{.*\})", res.get("stdout", ""))
            digest = self.store.put_blob((code + "\n# --- output ---\n" + res.get("stdout", "")[-4000:] + res.get("stderr", "")[-2000:]).encode())
            art = self.store.put({"type": "artifact", "createdBy": actor, "kind": "log", "uri": f"cas:{digest}", "provider": "local",
                                  "sha256": digest, "mediaType": "text/plain"})
            ok = res.get("returncode") == 0 and bool(found)
            # Deney nesnesi bu aşamada henüz yok: kayıt, betik+çıktı artefaktı ve exploration.done olayıyla tutulur.
            if ok:
                try:
                    data = json.loads(found.group(1))
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    data["artifact"] = art["id"]
                    self.set_state("exploration", data)
                    self.store.append("exploration.done", actor, {"checked": data.get("checked"),
                                                                  "counterexample": data.get("counterexample"),
                                                                  "observations": data.get("observations", [])[:6]})
                    return
            prompt += f"\n\nYour previous script failed or printed no QUAERA_EXPLORE line:\n{res.get('stderr', '')[-1500:]}\nFix it."

    def stage_hypothesis_approval(self) -> None:
        drafts = [h for h in self.store.latest("hypothesis") if h["status"] == "draft"]
        ranked = self.store.events("hypotheses.ranked")
        scores = {c["statement"]: c for c in ranked[-1]["payload"]["candidates"]} if ranked else {}
        label = {"full": "fully answers the question", "restricted": "RESTRICTED: only part of the question", "related": "related"}
        decision = self.ask_human(drafts[0]["createdBy"], "accept_hypothesis", drafts[0]["id"],
                                  "Which hypothesis should be tested?", 0.0,
                                  [f"{h['statement']} [{label.get(h.get('scopeRelation', {}).get('relation'), 'scope not stated')}]"
                                   + (f" · Critic score {scores[h['statement']]['score']}/10: {scores[h['statement']]['reason']}"
                                      if h["statement"] in scores else "") for h in drafts])
        if not decision.approved:
            for h in drafts:
                self.store.put({**h, "status": "rejected"}, by=self.human())
            raise StopResearch("the human approved no hypothesis")
        chosen = drafts[decision.choice]
        approval = {"by": self.human(), "at": now(), "decision": "approved", "autonomyLevel": decision.autonomy}
        self.store.put({**chosen, "status": "accepted", "approval": approval}, by=self.human())
        self.set_state("hypothesis_id", chosen["id"])

    def stage_design(self) -> None:
        h, lit = self.selected_hypothesis(), self.state("literature")
        actor = self.det("experiment_designer")
        toolchain = (DEFAULT_WORKSPACE / "lean-toolchain").read_text().strip()
        mathlib_rev = re.search(r'rev = "([^"]+)"', (DEFAULT_WORKSPACE / "lakefile.toml").read_text()).group(1)
        prereg = {"type": "preregistration", "createdBy": actor, "hypothesisId": h["id"],
                  "primaryMetric": "lean_verified",
                  "successCriterion": "The quaera_main theorem compiles with the approved statement, without sorry or non-standard axioms.",
                  "analysisPlan": "Compiler output and #print axioms result; the Verifier recompiles in a clean environment.",
                  "lockedAt": now()}
        prereg["contentHash"] = prereg_hash(prereg)
        prereg = self.store.put(prereg)
        estimate = round(self.gateway.remaining(), 2)
        self.store.put({
            "type": "experiment", "createdBy": actor, "hypothesisIds": [h["id"]], "domain": "math",
            "method": "Formalize the hypothesis in Lean 4 + Mathlib (pilot: the statement compiles with sorry), Critic review of the statement, then proof and recompilation in a clean environment.",
            "metrics": [{"name": "lean_verified", "direction": "binary"}],
            "lean": {"toolchain": toolchain, "mathlibRev": mathlib_rev},
            "budget": {"estimatedUsd": estimate},
            "noveltyCheck": {"verdict": lit["verdict"], "basis": lit.get("basis", "search"),
                             "summary": lit["summary"] or "No literature summary.",
                             "priorWork": [self.store.get(p)["uri"] for p in lit["papers"]]},
            "preregistrationId": prereg["id"], "status": "awaiting_approval",
        })

    def stage_experiment_approval(self) -> None:
        exp = self.store.latest("experiment")[-1]
        exp = {**exp, "budget": {**exp["budget"], "estimatedUsd": math.floor(self.gateway.remaining() * 10000) / 10000}}
        verdict = exp["noveltyCheck"]["verdict"]
        options = None
        if verdict not in ("novel", "unknown"):
            options = ["Proceed", "Replicate the known result", "Change direction (stop the research)"]
        summary = (f"{exp['id']}: {exp['method']}\nNovelty: {verdict} — {exp['noveltyCheck']['summary']}\n"
                   f"Budget: ${exp['budget']['estimatedUsd']:.2f}")
        decision = self.ask_human(exp["createdBy"], "approve_experiment", exp["id"], summary, exp["budget"]["estimatedUsd"], options)
        if not decision.approved or (options and decision.choice == 2):
            raise StopResearch("the human did not approve the experiment")
        choice = ["proceed", "replicate", "change_direction"][decision.choice] if options else "proceed"
        approval = {"by": self.human(), "at": now(), "decision": "approved", "autonomyLevel": decision.autonomy}
        self.store.put({**exp, "status": "approved", "approval": approval,
                        "noveltyCheck": {**exp["noveltyCheck"], "userChoice": choice},
                        "budget": {**exp["budget"], "approvedUsd": exp["budget"]["estimatedUsd"]}}, by=self.human())

    def _experiment(self) -> dict:
        return self.store.latest("experiment")[-1]

    def _compile(self, role: str, source: str, approved: str | None = None) -> dict:
        return self.tools.call_json(role, "lean.compile", source=source, theorem=THEOREM, approved_statement=approved)

    def _lean_artifact(self, actor: dict, source: str, report: dict) -> dict:
        digest = self.store.put_blob(source.encode())
        out_digest = self.store.put_blob(report["output"].encode())
        self.store.append("lean.output", actor, {"sourceSha256": digest, "outputSha256": out_digest})
        return self.store.put({"type": "artifact", "createdBy": actor, "kind": "lean_output",
                               "uri": f"cas:{digest}", "provider": "local", "sha256": digest, "mediaType": "text/x-lean"})

    def _run(self, actor: dict, kind: str, attempt: int, report: dict, art: dict, started: str) -> dict:
        return self.store.put({"type": "run", "createdBy": actor, "experimentId": self._experiment()["id"], "kind": kind,
                               "status": "succeeded" if report.get("_ok") else "failed", "seed": attempt,
                               "config": {"attempt": attempt}, "startedAt": started, "endedAt": now(), "logs": art["id"]})

    def _formalize(self, feedback: str = "") -> tuple[str, dict, dict]:
        h, lit = self.selected_hypothesis(), self.state("literature")
        limit = self.permissions.spec("engineer")["limits"].get("maxFixAttempts", 3)
        prompt = (f"Hypothesis: {h['statement']}\nUseful Mathlib declarations: {', '.join(lit['mathlib']) or '—'}\n{feedback}"
                  + self._exploration_text())
        for attempt in range(limit + 1):
            started = now()
            text, actor = self.guarded(self.llm, "engineer", prompts.FORMALIZE, prompt, 4000)
            source = extract_lean(text)
            report = self._compile("engineer", source)
            sorry_only = report["compiled"] and set(report["problems"]) <= {"Proof contains `sorry`."}
            report["_ok"] = sorry_only and statement_of(source, THEOREM) is not None
            art = self._lean_artifact(actor, source, report)
            run = self._run(actor, "pilot", attempt, report, art, started)
            if report["_ok"]:
                return source, actor, run
            prompt += f"\n\nYour previous file:\n```lean\n{source}\n```\nCompiler feedback:\n{feedback_text(report)}\nFix the statement."
        raise StopResearch("the Engineer could not turn the hypothesis into a compilable Lean statement")

    def stage_formalize(self) -> None:
        source, actor, run = self._formalize()
        self.set_state("formal", {"source": source, "statement": statement_of(source, THEOREM), "pilotRun": run["id"]})

    def stage_statement_review(self) -> None:
        h, q = self.selected_hypothesis(), self.question()
        rounds = self.permissions.spec("critic")["limits"].get("maxRounds", 3)
        for round_ in range(1, rounds + 1):
            formal = self.state("formal")
            review, critic = self.ask_json("critic", prompts.CRITIC_STATEMENT,
                                        f"Original question: {q['title']}\nHypothesis: {h['statement']}\n\nLean statement file:\n```lean\n{formal['source']}\n```"
                                        + self._backtranslation(formal["source"]), 2500)
            self.record_scope(review, critic)
            serious = [i for i in review.get("issues", []) if i.get("severity") in ("high", "blocking")]
            if review.get("faithful") and not serious:
                self.store.append("statement.approved", critic, {"summary": review.get("summary", ""), "round": round_})
                return
            body = "; ".join(i["body"] for i in serious or review.get("issues", [])) or review.get("summary", "The statement does not match the hypothesis.")
            cr = self.store.put({"type": "critique", "createdBy": critic, "targetId": h["id"], "category": "methodology",
                                 "severity": "blocking" if any(i.get("severity") == "blocking" for i in serious) else "high",
                                 "body": f"The formal statement does not match the hypothesis: {body}", "blindReview": False, "status": "open"})
            obj = self.message(critic, "objection", "engineer", cr["id"], body, round_=round_)
            source, engineer, _ = self._formalize(f"\nThe Critic objected to your previous statement:\n{body}\nPrevious statement file:\n```lean\n{formal['source']}\n```")
            self.set_state("formal", {**formal, "source": source, "statement": statement_of(source, THEOREM)})
            self.message(engineer, "response", "critic", cr["id"], "Statement rewritten to address the objection.", reply_to=obj["id"], round_=round_)
            again, critic = self.ask_json("critic", prompts.CRITIC_STATEMENT,
                                        f"Original question: {q['title']}\nHypothesis: {h['statement']}\n\nRevised Lean statement file:\n```lean\n{source}\n```"
                                        + self._backtranslation(source), 2500)
            self.record_scope(again, critic)
            still = [i for i in again.get("issues", []) if i.get("severity") in ("high", "blocking")]
            if again.get("faithful") and not still:
                self.store.put({**cr, "status": "resolved", "createdBy": critic,
                                "resolution": {"by": critic, "at": now(), "text": "The revised statement encodes the hypothesis correctly."}})
                self.store.append("statement.approved", critic, {"summary": again.get("summary", ""), "round": round_})
                return
            self.store.put({**cr, "status": "rejected_with_reason", "createdBy": critic,
                            "resolution": {"by": critic, "at": now(), "text": "The revision did not resolve the objection; starting a new round."}})
        decision = self.ask_human(self.det("director"), "accept_hypothesis", h["id"],
                                  f"The Critic and the Engineer did not agree in {rounds} rounds. Final statement:\n{self.state('formal')['source']}\nProceed with this statement?", 0.0)
        if not decision.approved:
            raise StopResearch("no agreement was reached on the formal statement")
        self.store.append("statement.approved", self.human(), {"summary": "by human decision", "round": rounds})

    def _backtranslation(self, source: str) -> str:
        """#6: hipotezi görmeyen bir model Lean dosyasını Türkçeye çevirir; Eleştirmen bu bağımsız okumayla karşılaştırır."""
        try:
            out, reader = self.ask_json("verifier", prompts.BACKTRANSLATE, f"Lean file:\n```lean\n{source}\n```", 1500)
        except StopResearch:
            return ""
        self.store.append("statement.backtranslated", reader, {"translation": out.get("translation", ""), "oddities": out.get("oddities", [])})
        odd = "; ".join(out.get("oddities", [])) or "—"
        return ("\n\nIndependent back-translation by a model that saw ONLY the Lean file (if it differs from the hypothesis in "
                f"domain, quantifiers, ranges or assumptions, that is a fidelity issue):\n{out.get('translation', '')}\nPitfalls it noticed: {odd}")

    def record_scope(self, review: dict, critic: dict) -> None:
        """Eleştirmen'in, biçimsel ifadenin sorudan zayıf olup olmadığına dair bağımsız görüşü."""
        if "weakerThanQuestion" in review:
            self.store.append("scope.review", critic, {"weakerThanQuestion": bool(review["weakerThanQuestion"]),
                                                      "note": review.get("scopeNote", "")})

    def _fidelity_check(self, kind: str, source: str, theorem: str, stmt: str, actor: dict) -> bool:
        started = now()
        report = self.tools.call_json("engineer", "lean.compile", source=source, theorem=theorem, approved_statement=stmt)
        report["_ok"] = report["verified"]
        art = self._lean_artifact(actor, source, report)
        self.store.put({"type": "run", "createdBy": actor, "experimentId": self._experiment()["id"], "kind": "pilot",
                        "status": "succeeded" if report["verified"] else "failed", "seed": 0,
                        "config": {"check": kind}, "startedAt": started, "endedAt": now(), "logs": art["id"]})
        self.store.append("fidelity.check", actor, {"check": kind, "verified": report["verified"], "artifact": art["id"]})
        if report["verified"]:
            self.set_state(kind, {"source": source, "statement": stmt, "artifact": art["id"]})
        return report["verified"]

    def _pre_proof_checks(self) -> bool:
        """#6: varsayımlar çelişkili mi (boşuna doğru)? İfade kolayca çürütülebiliyor mu? True dönerse ispata gerek yok."""
        from .fidelity import refutation_file, vacuity_file
        formal, auto = self.state("formal"), self.det("engineer")
        vac = vacuity_file(formal["source"], THEOREM)
        if vac and self._fidelity_check("vacuity", vac[0], "quaera_vacuous", vac[1], auto):
            h = self.selected_hypothesis()
            self.store.put({"type": "critique", "createdBy": self.det("critic"), "targetId": h["id"], "category": "methodology",
                            "severity": "blocking", "status": "open", "blindReview": False,
                            "body": "The formal statement's assumptions contradict each other: Lean derived False from them. The statement is vacuously true; "
                                    "its proof says nothing about the hypothesis."})
            raise StopResearch("the formal statement is vacuously true (contradictory assumptions); it must be re-formalized")
        ref = refutation_file(formal["source"], THEOREM)
        return bool(ref and self._fidelity_check("refutation", ref[0], "quaera_refute", ref[1], auto))

    def _try_refute(self, hint: str = "") -> bool:
        """Bir kez karşı örnek aranır (model): ispat bulunamadığında ya da keşif aday bir karşı örnek bulduğunda.
        Başarılıysa hipotez Lean'de çürütülmüş olur."""
        from .fidelity import refutation_file
        formal = self.state("formal")
        ref = refutation_file(formal["source"], THEOREM, proof="by\n  sorry")
        if not ref or self.state("refutation"):
            return bool(self.state("refutation"))
        text, actor = self.guarded(self.llm, "engineer", prompts.REFUTE,
                                   f"Original file:\n```lean\n{formal['source']}\n```\nState and prove exactly:\n```lean\n{ref[1]} := by\n```"
                                   + (f"\n{hint}" if hint else ""), 12000)
        if "```" not in text:
            return False
        return self._fidelity_check("refutation", extract_lean(text), "quaera_refute", ref[1], actor)

    def stage_prove(self) -> None:
        formal, lit = self.state("formal"), self.state("literature")
        if self._pre_proof_checks():
            self.set_state("proof", None)
            return
        ex = self.state("exploration") or {}
        if ex.get("counterexample") and self._try_refute(f"Numerical exploration found a candidate counterexample: {json.dumps(ex['counterexample'], ensure_ascii=False)}"):
            self.set_state("proof", None)
            return
        # #2 ispat araması: otomasyon → hataya dayanıklı bütün ispat denemeleri → taslak + lemmalar (src/quaera/prover.py)
        from .prover import prove_search
        limit = self.permissions.spec("engineer")["limits"].get("maxFixAttempts", 3)
        verified_main: dict = {}
        # İspat araması zor problemlerde bütçenin tamamını yiyebilir (Putnam ölçümünde tek problem 2,52 $ harcadı).
        # İnceleme, doğrulama ve rapor için pay kalsın diye arama, aşama başındaki kalan bütçenin yarısıyla sınırlanır.
        search_cap = self.gateway.spent_usd + PROVE_BUDGET_SHARE * self.gateway.remaining()

        def ask(system: str, prompt: str, n: int, lane: str | None = None) -> str:
            if self.gateway.spent_usd >= search_cap:
                raise SearchBudget(f"proof search used its budget share ({PROVE_BUDGET_SHARE:.0%})")
            prompt = prompt + self.human_notes("engineer")
            try:
                completion, _cross = self.gateway.call("engineer", system, prompt, n, family=self.lane_family(lane))
            except BudgetExceeded as exc:
                raise StopResearch(f"budget cap: {exc}") from exc
            actor = {"kind": "agent", "role": "engineer", "model": completion.model, "modelFamily": completion.family}
            self.actors["engineer"] = actor
            self.said(actor, system, prompt, completion.text, lane=lane)
            return completion.text   # ModelError (ör. çıktı sınırı) aramaya iletilir; arama istemi değiştirip sürer

        def check(source: str, name: str | None = None, approved: str | None = None) -> dict:
            name = name or THEOREM
            started, actor = now(), self.actors.get("engineer") or self.det("engineer")
            report = self.tools.call_json("engineer", "lean.compile", source=source, theorem=name,
                                          approved_statement=formal["statement"] if name == THEOREM else approved)
            report["_ok"] = report["verified"]
            art = self._lean_artifact(actor, source, report)
            if name == THEOREM:
                run = self._run(actor, "full", len(self.store.latest("run")), report, art, started)
                if report["verified"]:
                    verified_main.update(run=run["id"], artifact=art["id"])
            return report

        hints = (f"Useful Mathlib declarations: {', '.join(lit['mathlib']) or '—'}" + self._exploration_text())
        progress = lambda step, status, detail="", lane=None: self.activity("engineer", step, status, detail, lane)  # noqa: E731
        try:
            res = prove_search(formal["source"], THEOREM, ask, check, attempts=limit + 1, hints=hints,
                               progress=progress, parallel=2)
        except SearchBudget as exc:
            self.store.append("proof.search", self.det("engineer"), {"solved": False, "stopped": str(exc)})
            self.set_state("proof", None)
            self._try_refute()
            return
        steps = []
        for e in res.log:   # kaynak metinler içerik adresli depoya; arayüz ispat yapısını (taslak, lemmalar) buradan çizer
            step = {k: v for k, v in e.items() if k != "source"}
            if e.get("source"):
                step["sha256"] = self.store.put_blob(e["source"].encode("utf-8"))
            steps.append(step)
        self.store.append("proof.search", self.det("engineer"), {"solved": res.solved, "modelCalls": res.attempts, "steps": steps})
        if res.solved and verified_main:
            self.set_state("proof", {"source": res.source, **verified_main})
            return
        self.set_state("proof", None)
        self._try_refute()

    def stage_analysis(self) -> None:
        h, proof, refutation = self.selected_hypothesis(), self.state("proof"), self.state("refutation")
        analyst = self.det("analyst")
        runs = [r for r in self.store.latest("run") if r["kind"] == "full"]
        if refutation and not proof:
            res = self.store.put({"type": "result", "createdBy": analyst, "experimentId": self._experiment()["id"],
                                  "runIds": [r["id"] for r in self.store.latest("run") if (r.get("config") or {}).get("check") == "refutation"
                                             and r["status"] == "succeeded"][-1:],
                                  "negative": True,
                                  "summary": "The negation of the formal statement was proved in Lean 4 without sorry or non-standard axioms: the statement is false (a counterexample exists).",
                                  "limitations": ["The refutation relies on the formal statement encoding the hypothesis correctly (Critic and independent back-translation)."],
                                  "leanProof": {"verified": True, "sorryFree": True, "compilerOutput": refutation["artifact"], "theoremName": "quaera_refute"}})
            relation = "contradicts"
        elif proof:
            res = self.store.put({"type": "result", "createdBy": analyst, "experimentId": self._experiment()["id"],
                                  "runIds": [proof["run"]], "summary": f"The {THEOREM} theorem compiled in Lean 4 without sorry or non-standard axioms.",
                                  "limitations": ["That the formal statement encodes the hypothesis correctly rests on the Critic's review."],
                                  "leanProof": {"verified": True, "sorryFree": True, "compilerOutput": proof["artifact"], "theoremName": THEOREM}})
            relation = "supports"
        else:
            if not runs:
                raise StopResearch("no proof attempt could be made")
            res = self.store.put({"type": "result", "createdBy": analyst, "experimentId": self._experiment()["id"],
                                  "runIds": [r["id"] for r in runs], "negative": True,
                                  "summary": f"No proof found in {len(runs)} attempts. This does not show the hypothesis is false.",
                                  "limitations": ["Failing to find a proof does not refute the hypothesis; it only shows none was found with this budget and these attempts."],
                                  "leanProof": {"verified": False}})
            relation = "inconclusive"
        self.store.put({"type": "evidence_link", "createdBy": analyst, "resultId": res["id"], "hypothesisId": h["id"],
                        "relation": relation, "independence": "L0", "context": "Formal proof; recompilation of the same file."})
        self.set_state("result_id", res["id"])

    def stage_result_review(self) -> None:
        proof = self.state("proof") or self.state("refutation")
        if not proof:
            return
        h = self.selected_hypothesis()
        what = "Proof file" if self.state("proof") else "REFUTATION file (proves the NEGATION of the approved statement)"
        out, critic = self.ask_json("critic", prompts.CRITIC_RESULT,
                                    f"Hypothesis: {h['statement']}\nApproved statement: {self.state('formal')['statement']}\n\n{what}:\n```lean\n{proof['source']}\n```", 3000)
        for concern in out.get("concerns", []):
            if concern.get("severity") in ("high", "blocking"):
                cr = self.store.put({"type": "critique", "createdBy": critic, "targetId": self.state("result_id"),
                                     "category": "overclaim", "severity": concern["severity"], "body": concern["body"],
                                     "blindReview": False, "status": "open"})
                self.message(critic, "objection", "human", cr["id"], concern["body"], round_=1)

    def stage_verification(self) -> None:
        proof, refutation = self.state("proof"), self.state("refutation")
        if not proof and not refutation:
            return
        verifier = self.det("verifier")
        started = now()
        if proof:
            report = self._compile("verifier", proof["source"], self.state("formal")["statement"])
        else:
            proof = refutation
            report = self.tools.call_json("verifier", "lean.compile", source=refutation["source"], theorem="quaera_refute",
                                          approved_statement=refutation["statement"])
        report["_ok"] = report["verified"]
        art = self._lean_artifact(verifier, proof["source"], report)
        run = self._run(verifier, "verification", 0, report, art, started)
        self.store.put({"type": "verification", "createdBy": verifier, "resultId": self.state("result_id"),
                        "verificationRunIds": [run["id"]], "reproduced": "yes" if report["verified"] else "no",
                        "crossModel": False, "tolerance": "exact: compilation and axiom list"})

    def stage_conclude(self) -> None:
        h = self.state("hypothesis_id") and self.selected_hypothesis()
        if not h:
            return
        open_crit = [c for c in self.store.latest("critique") if c["status"] == "open"]
        verified = any(v["reproduced"] == "yes" for v in self.store.latest("verification"))
        ok = "refuted" if self.state("refutation") and not self.state("proof") else "supported"
        status = ok if verified and not open_crit else ("under_critique" if open_crit else "inconclusive")
        if h["status"] in ("accepted", "testing") or h["status"] != status:
            if h["status"] != "rejected":
                self.store.put({**h, "status": status, "createdBy": h["createdBy"]}, by=self.det("hypothesis"))
        # Cevapsız itiraz kalmasın: insana yöneltilmiş itirazlar rapora taşınır ve kayıt altına alınır.
        replied = {m.get("inReplyTo") for m in self.store.latest("message")}
        for m in self.store.latest("message"):
            if m.get("requiresResponse") and m["id"] not in replied:
                self.message(self.det("director"), "response", m["createdBy"]["role"], m["subjectId"],
                             "Objection handed over to human review; shown in the report as an open objection.", reply_to=m["id"])

    def stage_report(self) -> None:
        from .report import write_report
        path = write_report(self.store, self.gateway, self.reports_dir or self.store.path.parent)
        self.set_state("report_path", str(path))
        if self.memory is not None:
            self.memory.index_project(self.store.path.parent)

    def recall_memory(self, q: dict) -> str:
        """Benzer geçmiş projeleri Hipotez ajanına yönlendirme bağlamı olarak verir; ne verildiği kayda geçer."""
        if self.memory is None:
            return ""
        from .memory import context_block, outcome
        items = self.memory.recall(f"{q['title']} {q.get('scope', '')}", k=4, exclude=self.store.path.parent.name)
        if items:
            self.store.append("memory.recalled", self.det("director"),
                              {"items": [{"project": e["project"], "title": e["title"], "outcome": outcome(e)} for e in items]})
        return context_block(items, {e["project"]: self.memory.learnings(limit=6, project=e["project"], include_evals=True)
                                     for e in items})

    def learn(self) -> None:
        """Deney sonucu ya da sonuç aşamasından sonra öğrenilenler laboratuvar hafızasına yazılır (projeler arası)."""
        if self.memory is not None:
            try:
                self.memory.learn(self.store.path.parent)
            except Exception as exc:   # hafıza yardımcıdır: yazılamaması araştırmayı durdurmamalı, ama kayda geçer
                self.store.append("memory.error", self.det("director"), {"error": str(exc)[:300]})


PROMPT_NAMES = {v: k for k, v in vars(prompts).items() if k.isupper() and isinstance(v, str) and len(v) > 200}
# Teorem adı değiştirilerek kullanılan istemler (ör. PROVE ile bir lemmayı ispatlamak): `quaera_main` yerine herhangi bir ad.
PROMPT_PATTERNS = [(k, re.compile(re.escape(v).replace(re.escape("`quaera_main`"), r"`[\w.']+`")))
                   for v, k in PROMPT_NAMES.items() if "`quaera_main`" in v]


def purpose_of(system: str) -> str:
    """Sistem isteminden çağrının amacı (ör. PROVE, CRITIC_EXPERIMENT)."""
    return PROMPT_NAMES.get(system) or next((k for k, rx in PROMPT_PATTERNS if rx.fullmatch(system)), "OTHER")


def as_object(value):
    """ask_json her zaman bir JSON nesnesi bekler; liste gelirse geçersiz yanıt sayılır (bir kez yeniden sorulur)."""
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return value


def similar(a: str, b: str, threshold: float = 0.85) -> bool:
    """Neredeyse aynı iki hipotez (kelime düzeyinde benzerlik) tek aday sayılır."""
    import difflib
    return difflib.SequenceMatcher(a=a.lower().split(), b=b.lower().split()).ratio() >= threshold


def extract_lean(text: str) -> str:
    m = re.search(r"```lean4?\s*\n(.*?)```", text, re.S) or re.search(r"```\s*\n(.*?)```", text, re.S)
    source = (m.group(1) if m else text).strip()
    if not source.startswith("import"):
        source = "import Mathlib\n\n" + source
    return source + "\n"


def feedback_text(report: dict) -> str:
    lines = [f"ERROR: {e}" for e in report.get("errors", [])[:8]] + [f"PROBLEM: {p}" for p in report.get("problems", [])]
    if report.get("timed_out"):
        lines.append("Compilation timed out.")
    return "\n".join(lines) or "unknown failure"
