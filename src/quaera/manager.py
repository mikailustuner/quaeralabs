"""Project manager: an agent that only watches the research and chats with the human.

The point is to keep the Director undistracted: the human asks questions like "what is happening now, why this hypothesis,
how much have we spent" here; the reply is built from the project's event log and nothing enters the research loop.
- The manager cannot write objects, run tools or message the team (agents/manager.yaml).
- If the human wants to tell the team something, the manager proposes a *draft* note (FORWARD line); sending it to the
  Director takes the human's click and is recorded as a normal human message.
- The chat has its own budget (QUAERA_MANAGER_CAP_USD, default $0.50 per project); it is not taken from the research
  budget and is tracked separately through `manager.call` events. The short project title also comes from this budget.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .gateway import BudgetExceeded, Gateway, ModelError
from .permissions import Permissions
from .store import Store, now

MANAGER_CAP_USD = float(os.environ.get("QUAERA_MANAGER_CAP_USD", "0.5"))
HIDDEN = {"agent.said", "activity", "tool.started", "tool.call", "model.call", "state", "lean.output", "manager.chat",
          "manager.call", "message.delivered"}

SYSTEM = """You are the Project Manager of QuaeraLabs, an open AI research lab where a team of agents (Director, Literature,
Hypothesis, Experiment designer, Engineer, Analyst, Critic, Verifier, Writer) investigates a question under human oversight.

You only OBSERVE. You never interrupt the Director or the team. Your job is to keep the human informed so the team can work
undisturbed. Answer using only the project record given below; if something is not in the record, say you don't know yet.
Never invent results, numbers, proofs or citations. Distinguish clearly between proved, refuted, inconclusive and not-yet-done.

Style: concise (normally under 180 words), plain and friendly. Markdown is allowed. Write mathematics in LaTeX between $...$
(display: $$...$$) and Lean code in backticks. Reply in the same language as the human's latest message.

You cannot approve or reject decisions: if a decision is pending, tell the human it waits for them on the decision card.
If the human wants to tell the team something (a constraint, a different approach, a question for an agent), do not claim
you passed it on. End your reply with exactly one extra line:
FORWARD: <a short, self-contained note to the Director, in the human's language>
The human decides whether to send it. Omit the FORWARD line otherwise."""

TITLE_SYSTEM = ("You name research projects. Reply with ONLY a short English title of 2 to 5 words that summarizes the "
                "research question (e.g. 'Sum of first n odds', 'Sparse reciprocal sequences'). No quotes, no trailing "
                "punctuation, no explanation.")


def fallback_title(question: str) -> str:
    """When no model is available: the first words of the question (at most ~40 characters)."""
    words, out = question.replace("\n", " ").split(), ""
    for w in words:
        if len(out) + len(w) + 1 > 40:
            break
        out = f"{out} {w}".strip()
    return (out or question[:40]).rstrip("?.,;:") + ("…" if len(out) < len(question.strip().rstrip("?")) else "")


def digest(store: Store) -> str:
    """Project digest given to the manager: question, stages, hypotheses, results, pending decisions, recent events."""
    from .ml_loop import ML_STAGES
    from .orchestrator import STAGES
    objs = store.latest()
    domain = store.meta("domain", "math")
    stages = ML_STAGES if domain == "ml" else STAGES
    done = [e["payload"]["stage"] for e in store.events("stage.done")]
    spent = sum(e["payload"]["costUsd"] for e in store.events("model.call"))
    states = {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}
    lines = [f"Question: {store.meta('title')}", f"Domain: {domain}",
             f"Budget: ${spent:.3f} spent of ${store.meta('budgetCapUsd') or 0:.2f} research cap",
             f"Stages done: {', '.join(done) or 'none'}",
             f"Current stage: {next((s for s in stages if s not in done), 'finished')}"]
    if states.get("stopped"):
        lines.append(f"STOPPED: {states['stopped']}")
    for o in objs:
        t = o["type"]
        if t == "question":
            lines.append(f"Scope: {o.get('scope', '')}")
        elif t == "hypothesis":
            lines.append(f"Hypothesis {o['id']} [{o['status']}]: {o['statement']}")
        elif t == "preregistration":
            lines.append(f"Locked success criterion: {o.get('successCriterion')} (metric {o.get('primaryMetric')})")
        elif t == "experiment":
            lines.append(f"Experiment {o['id']} [{o.get('status')}]: {str(o.get('method', ''))[:400]}")
        elif t == "result":
            lines.append(f"Result {o['id']}{' (negative)' if o.get('negative') else ''}: {o['summary'][:500]}")
        elif t == "verification":
            lines.append(f"Independent verification of {o['resultId']}: reproduced={o['reproduced']}")
        elif t == "critique" and o["status"] == "open":
            lines.append(f"OPEN critique ({o.get('severity')}): {o['body'][:300]}")
    formal = states.get("formal")
    if isinstance(formal, dict) and formal.get("statement"):
        lines.append(f"Formal Lean statement: {formal['statement'][:600]}")
    if store.meta("mode") == "discover":
        lines.append("Mode: DISCOVERY (open problem; only Lean-verified statements count)")
        rev = {e["payload"]["id"]: e["payload"] for e in store.events("strategy.reviewed")}
        for e in store.events("strategy.proposed"):
            p = e["payload"]
            lines.append(f"Strategy {p['id']} by {p['family']} ({p.get('lensName')}): {p['title']} — review {rev.get(p['id'], {}).get('score', '?')}/10")
        for e in store.events("lemma.status")[-12:]:
            p = e["payload"]
            lines.append(f"Lemma {p['id']} round {p['round']} via {p['family']}: {p['status']} {p.get('detail', '')[:120]}")
    resolved = {e["payload"]["id"] for e in store.events("approval.resolved")}
    for e in store.events("approval.pending"):
        if e["payload"]["id"] not in resolved:
            lines.append(f"PENDING human decision ({e['payload']['action']}): {e['payload']['summary'][:400]}")
    recent = [e for e in store.events() if e["kind"] not in HIDDEN][-18:]
    if recent:
        lines.append("Recent events:")
        lines += [f"- {e['at'][11:19]} {e['kind']} by {e['actor'].get('role') or 'human'}: "
                  f"{str({k: v for k, v in e['payload'].items() if k not in ('steps', 'source')})[:220]}" for e in recent]
    said = store.events("agent.said")[-4:]
    if said:
        lines.append("Latest agent outputs (previews):")
        lines += [f"- {e['payload']['role']} ({e['payload'].get('purpose')}): {e['payload'].get('preview', '')[:350]}" for e in said]
    live = [e for e in store.events("activity")][-3:]
    if live:
        lines.append("Latest activity: " + "; ".join(f"{e['payload']['role']} {e['payload']['status']} {e['payload']['step']}" for e in live))
    return "\n".join(lines)


def split_forward(text: str) -> tuple[str, str | None]:
    m = re.search(r"^\s*FORWARD:\s*(.+?)\s*$", text, re.M)
    if not m:
        return text.strip(), None
    return (text[:m.start()] + text[m.end():]).strip(), m.group(1).strip()


class Manager:
    def __init__(self, project: Path, providers: dict | None = None, memory=None, cap_usd: float = MANAGER_CAP_USD):
        self.project, self.memory = project, memory
        self.store = Store(project / "quaera.db")
        if providers is None:
            from .cli import make_providers
            providers = make_providers()
        # Failed calls can be billed too (e.g. output limit exceeded): they count against the budget as well.
        spent = sum(e["payload"].get("costUsd", 0) for kind in ("manager.call", "manager.error") for e in self.store.events(kind))
        kinds = {"model.call": "manager.call", "model.error": "manager.error", "budget.blocked": "manager.blocked"}
        self.gateway = Gateway(providers, cap_usd, Permissions.load().agents,
                               lambda kind, p: self.store.append(kinds.get(kind, f"manager.{kind}"), self.actor, p),
                               spent_usd=spent)
        self.actor = {"kind": "agent", "role": "manager", "model": "quaera/deterministic", "modelFamily": "quaera"}

    def close(self) -> None:
        self.store.close()
        if self.memory is not None:
            self.memory.close()

    def history(self) -> list[dict]:
        return [{"seq": e["seq"], "at": e["at"], **e["payload"]} for e in self.store.events("manager.chat")]

    def usage(self) -> dict:
        return {"spentUsd": round(self.gateway.spent_usd, 4), "capUsd": self.gateway.cap_usd}

    def _memory_text(self) -> str:
        if self.memory is None:
            return ""
        from .memory import context_block
        items = self.memory.recall(self.store.meta("title") or "", k=3, exclude=self.project.name)
        return context_block(items, {e["project"]: self.memory.learnings(limit=4, project=e["project"], include_evals=True)
                                     for e in items})

    def ask(self, text: str) -> dict:
        """Records the human's message and generates the reply. On a budget or model error an explanatory reply is recorded."""
        self.store.append("manager.chat", {"kind": "human", "userId": "local"}, {"from": "human", "text": text})
        turns = self.history()[-10:]
        convo = "\n".join(f"{'Human' if t['from'] == 'human' else 'You'}: {t['text']}" for t in turns)
        prompt = (f"PROJECT RECORD\n{digest(self.store)}{self._memory_text()}\n\nCONVERSATION SO FAR\n{convo}\n\n"
                  "Reply to the human's latest message.")
        try:
            if self.gateway.remaining() <= 0:
                raise BudgetExceeded("manager chat cap reached")
            comp, _ = self.gateway.call("manager", SYSTEM, prompt, max_output_tokens=900)
        except BudgetExceeded:
            reply = {"from": "manager", "text": "The manager chat budget for this project is used up "
                     f"(${self.gateway.spent_usd:.2f} of ${self.gateway.cap_usd:.2f}). The research itself is not affected. "
                     "Raise QUAERA_MANAGER_CAP_USD to keep chatting.", "error": "budget"}
        except ModelError as exc:
            reply = {"from": "manager", "text": f"I could not reach the model: {str(exc)[:200]}", "error": "model"}
        else:
            body, forward = split_forward(comp.text)
            reply = {"from": "manager", "text": body, "forward": forward, "model": comp.model, "costUsd": round(comp.cost_usd, 6)}
        seq = self.store.append("manager.chat", {**self.actor, "model": reply.get("model", self.actor["model"])}, reply)
        return {"seq": seq, "at": now(), **reply}

    def name(self) -> str:
        """Gives the project a short, descriptive title (shortTitle). Without a model: the start of the question."""
        question = self.store.meta("title") or self.project.name
        title = None
        try:   # thinking tokens count toward the output limit: keep it generous even for a short title (only generated tokens are billed)
            comp, _ = self.gateway.call("manager", TITLE_SYSTEM, f"Research question:\n{question}", max_output_tokens=1500)
            line = comp.text.strip().splitlines()[0] if comp.text.strip() else ""
            title = line.strip(" \"'“”*#.").strip()[:48] or None
        except (BudgetExceeded, ModelError):
            pass
        title = title or fallback_title(question)
        self.store.set_meta("shortTitle", title)
        return title
