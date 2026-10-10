"""Proof strategies. The orchestrator (stage_prove) and the evals (evals/putnam_run.py) use the same code,
so the eval measures the product's real component.

  baseline     write the whole proof in one go; fix it with compiler feedback at most `attempts` times (Phase 1 behaviour)
  search       (#2) free automation → error-tolerant whole-proof attempts → sketch + lemmas (each lemma separately:
               automation first, then the model) → assemble; if a lemma fails, one new sketch
  interactive  (capacity plan K2) one open goal at a time through the Lean REPL: the model sees the goal state and
               proposes one to three tactic lines; the loop applies them, backtracks on errors

Capacity plan additions to `prove_search`:
- `rounds` + `more()`: the whole ladder is repeated with fresh hints while the stage's budget share remains (R2);
- `heavy`: a second model-free rung with slower automation (`exact?`, normalisation chains) (K3);
- `samples`: N parallel candidates in the first round, each with a different hint, all checked by Lean (K5);
- `depth`: a lemma that resists direct attempts is itself sketched into smaller lemmas (K6);
- `interactive`: the REPL-driven rung above (K2), first when `interactive_first` (weak models, S2).

`ask(system, prompt, max_tokens[, lane=…]) -> text` is the model call (the caller does the budget check).
`check(source, name=None, approved=None) -> dict` is the Lean compilation: {"verified", "compiled", "errors", "problems", ...}
(LeanReport.__dict__ shape). Without name/approved, the main theorem and its approved statement are used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from . import prompts
from .gateway import ModelError
from .lean import statement_of
from .orchestrator import extract_lean, feedback_text

Ask = Callable[[str, str, int], str]
Check = Callable[[str], dict]


@dataclass
class ProofResult:
    solved: bool
    source: str | None
    attempts: int                                   # number of model calls
    log: list[dict] = field(default_factory=list)   # each attempt: {"kind", "source", "verified", "errors"}


@dataclass
class Interactive:
    """Access to the Lean REPL's proof states (K2). `goals(source)` → [{"proofState", "goal", "line"}] for every
    `sorry`; `tactic(state, text)` → {"proofState", "goals": [str], "error": str | None}."""
    goals: Callable[[str], list[dict]]
    tactic: Callable[[int, str], dict]
    max_steps: int = 12


def prove_baseline(statement_file: str, name: str, ask: Ask, check: Check, attempts: int = 4,
                   hints: str = "", max_tokens: int = 12000) -> ProofResult:
    system = prompts.PROVE.replace("`quaera_main`", f"`{name}`")
    prompt = (f"Approved statement (do not change; keep the theorem name `{name}`):\n```lean\n{statement_file}\n```\n"
              + (f"Hints: {hints}\n" if hints else ""))
    res = ProofResult(False, None, 0)
    for attempt in range(attempts):
        source = extract_lean(ask(system, prompt, max_tokens))
        res.attempts += 1
        rep = check(source)
        res.log.append({"kind": "whole", "source": source, "verified": rep.get("verified", False), "errors": rep.get("errors", [])[:3]})
        if rep.get("verified"):
            res.solved, res.source = True, source
            return res
        prompt += f"\n\nAttempt {attempt + 1} failed:\n```lean\n{source}\n```\nCompiler feedback:\n{feedback_text(rep)}\nFix the proof."
    return res


# Tactics tried without calling a model (tested on real Lean: tests/test_lean.py::test_automation_chain).
# `first` takes the first option that does not fail; tactics that simplify without closing the goal (norm_num, simp…) are guarded with `done`.
AUTOMATION = ("first | decide | omega | linarith | positivity | nlinarith | aesop | (norm_num; done) | (simp; done)"
              " | (intros; omega) | (intros; nlinarith) | (simp_all; done) | (norm_num [Finset.sum_range_succ]; done)")
# K3: slower model-free rung, tried once after the fast chain (library search and normalisation chains).
AUTOMATION_HEAVY = ("first | exact? | (field_simp; ring_nf; done) | (push_cast; ring_nf; done) | (norm_num at *; omega)"
                    " | (simp_all; nlinarith) | (intro n; induction n <;> simp_all <;> ring_nf <;> omega)")
LEMMA_RE = re.compile(r"((?:lemma|theorem)\s+(quaera_step_\d+)\b.*?):=\s*by\s+sorry", re.S)
SORRY_ONLY = "Proof contains `sorry`."
# A top-level declaration (or command) starts the next block of the file.
DECL_START = re.compile(r"\n(?=(?:@\[[^\n]*\]\s*)?(?:private\s+|protected\s+|noncomputable\s+)*"
                        r"(?:lemma|theorem|def|abbrev|instance|example|structure|inductive|class|open|section|namespace|end|/--)\b)")
FORBIDDEN_TACTIC = re.compile(r"\b(sorry|admit|native_decide|run_tac|unsafe|implemented_by)\b|#")
ROUND_HINTS = [
    "",
    "Earlier rounds failed: try a genuinely different proof idea (e.g. induction or cases instead of direct computation, or "
    "a key Mathlib lemma by name).",
    "Earlier rounds failed: split the proof into very small `have` steps, each closed by one short tactic.",
    "Earlier rounds failed: look for a reformulation (rewrite the goal into an equivalent standard form first).",
]
SAMPLE_HINTS = [
    "",
    "\n\nStrategy for THIS candidate: structure the proof explicitly — state the key intermediate facts as "
    "`have` steps (or small helper lemmas above the theorem) and close each with a short tactic.",
    "\n\nStrategy for THIS candidate: start with induction or case analysis on the main variable.",
    "\n\nStrategy for THIS candidate: find the Mathlib lemma that does most of the work and apply it with `exact`/`apply`/`rw`.",
    "\n\nStrategy for THIS candidate: prefer automation (`simp`, `omega`, `nlinarith`, `positivity`, `norm_num`) with the right lemma list.",
]
PARALLEL_HINT = SAMPLE_HINTS[1]


def decl_span(file_text: str, name: str) -> tuple[int, int, int] | None:
    """(start of the declaration, end of its `:=`, end of the declaration) for theorem/lemma `name`."""
    m = re.search(rf"((?:theorem|lemma)\s+{re.escape(name)}\b.*?):=", file_text, re.S)
    if not m:
        return None
    nxt = DECL_START.search(file_text, m.end())
    return m.start(), m.end(), (nxt.start() if nxt else len(file_text))


def with_proof(statement_file: str, name: str, proof: str) -> str | None:
    """Replaces the proof of theorem `name` with `proof`; the header and every other declaration are kept."""
    span = decl_span(statement_file, name)
    if not span:
        return None
    _start, head_end, end = span
    tail = statement_file[end:]
    return statement_file[:head_end] + f" by\n  {proof}\n" + (tail if tail.startswith("\n") or not tail else "\n" + tail)


def _ask_safe(ask: Ask, system: str, prompt: str, max_tokens: int, log: list, lane: str | None = None) -> str | None:
    """A model error (e.g. output limit exceeded) does not break the loop: it is logged and None is returned."""
    try:
        return ask(system, prompt, max_tokens, lane=lane) if lane else ask(system, prompt, max_tokens)
    except ModelError as exc:
        log.append({"kind": "model_error", "error": str(exc)[:200]})
        return None


def prove_search(statement_file: str, name: str, ask: Ask, check: Check, attempts: int = 2,
                 hints: str = "", max_tokens: int = 16000, sketches: int = 2,
                 progress: Callable | None = None, parallel: int = 1, *, rounds: int = 1,
                 more: Callable[[], bool] | None = None, samples: int | None = None, heavy: bool = False,
                 depth: int = 1, prefix: str = "quaera_step_", interactive: Interactive | None = None,
                 interactive_first: bool = False) -> ProofResult:
    """`progress(step, status, detail="", lane=None)`: for the live view (status: start/done/fail).
    `parallel`/`samples`: candidates written concurrently in the first round (lanes A, B, …); Lean checks are sequential."""
    res = ProofResult(False, None, 0)
    note = progress or (lambda *a, **k: None)
    n_samples = max(1, samples or parallel)

    def done(source: str) -> ProofResult:
        res.solved, res.source = True, source
        return res

    # 1) free automation (fast chain, then the heavy rung once)
    for kind, tactic in [("automation", AUTOMATION)] + ([("automation_heavy", AUTOMATION_HEAVY)] if heavy else []):
        auto = with_proof(statement_file, name, tactic)
        if not auto:
            break
        label = "Trying automation tactics (no model)" if kind == "automation" else "Trying library search and normalisation (no model)"
        note(label, "start")
        rep = check(auto)
        res.log.append({"kind": kind, "verified": rep.get("verified", False), "errors": rep.get("errors", [])[:2]})
        note(label, "done" if rep.get("verified") else "fail")
        if rep.get("verified"):
            return done(auto)

    system = prompts.PROVE.replace("`quaera_main`", f"`{name}`")
    base = (f"Approved statement (do not change; keep the theorem name `{name}`):\n```lean\n{statement_file}\n```\n"
            + (f"Hints: {hints}\n" if hints else ""))
    distinct: list[str] = []          # distinct compiler errors across rounds: the next round learns from all of them
    sk_system = prompts.SKETCH.replace("`quaera_main`", f"`{name}`").replace("quaera_step_", prefix)
    lemma_re = re.compile(rf"((?:lemma|theorem)\s+({re.escape(prefix)}\d+)\b.*?):=\s*by\s+sorry", re.S)

    def remember(rep: dict) -> None:
        for e in rep.get("errors", [])[:2]:
            short = re.sub(r"^line \d+: ", "", e)[:200]
            if short not in distinct:
                distinct.append(short)

    def interactive_rung() -> ProofResult | None:
        if interactive is None:
            return None
        note("Proving step by step in the Lean REPL (goal states)", "start")
        src = prove_interactive(statement_file, name, ask, check, interactive, hints, res, note)
        note("Proving step by step in the Lean REPL (goal states)", "done" if src else "fail")
        return done(src) if src else None

    for rnd in range(max(1, rounds)):
        if rnd > 0:
            if more is None or not more():
                break
            res.log.append({"kind": "round", "round": rnd + 1, "verified": False})
            note(f"Search round {rnd + 1}: budget remains, trying again with what failed", "start")
        round_hint = ROUND_HINTS[min(rnd, len(ROUND_HINTS) - 1)]
        prompt = base + (f"\n{round_hint}\n" if round_hint else "") + (
            "Compiler errors seen in earlier rounds (avoid them):\n- " + "\n- ".join(distinct[-8:]) + "\n" if rnd > 0 and distinct else "")
        if interactive_first and (r := interactive_rung()):
            return r

        # 2) whole-proof attempts (a model error does not break the loop); several parallel candidates in the first round
        for attempt in range(attempts):
            if attempt == 0 and rnd == 0 and n_samples > 1:
                from concurrent.futures import ThreadPoolExecutor
                lanes = [chr(65 + i) for i in range(n_samples)]
                label = lambda i: "direct" if i == 0 else ("step-by-step" if i == 1 else f"variant {i + 1}")  # noqa: E731
                for i, lane in enumerate(lanes):
                    note(f"Writing candidate proof ({label(i)})", "start", lane=lane)
                with ThreadPoolExecutor(max_workers=n_samples) as pool:
                    futures = [pool.submit(_ask_safe, ask, system, prompt + SAMPLE_HINTS[i % len(SAMPLE_HINTS)],
                                           max_tokens, res.log, lane) for i, lane in enumerate(lanes)]
                    texts = [f.result() for f in futures]
                for i, (lane, text) in enumerate(zip(lanes, texts)):
                    note(f"Writing candidate proof ({label(i)})", "done" if text else "fail", lane=lane)
            else:
                lane = "B" if rnd % 2 == 1 and n_samples > 1 else None      # later rounds alternate the model family
                note(f"Writing whole-proof attempt {attempt + 1}/{attempts}" + (f" (round {rnd + 1})" if rnd else ""), "start")
                texts = [_ask_safe(ask, system, prompt, max_tokens, res.log, lane)]
                note(f"Writing whole-proof attempt {attempt + 1}/{attempts}" + (f" (round {rnd + 1})" if rnd else ""),
                     "done" if texts[0] else "fail")
                lanes = [lane]
            feedback = []
            for i, text in enumerate(texts):
                lane = lanes[i] if len(texts) > 1 else None
                res.attempts += 1
                if text is None:
                    note("Model exceeded the output limit; a shorter proof will be requested", "fail", lane=lane)
                    feedback.append("Your previous answer was cut off (too long). Write a SHORTER proof: factor work into small "
                                    "helper lemmas and prefer automation tactics over long manual case analysis.")
                    continue
                source = extract_lean(text)
                note("Compiling with Lean", "start", lane=lane)
                rep = check(source)
                res.log.append({"kind": "whole", "source": source, "verified": rep.get("verified", False),
                                "errors": rep.get("errors", [])[:3], **({"lane": lane} if lane else {}),
                                **({"round": rnd + 1} if rnd else {})})
                note("Compiling with Lean", "done" if rep.get("verified") else "fail", "; ".join(rep.get("errors", [])[:1])[:200], lane=lane)
                if rep.get("verified"):
                    return done(source)
                remember(rep)
                feedback.append(f"Attempt failed:\n```lean\n{source}\n```\nCompiler feedback:\n{feedback_text(rep)}")
            prompt += "\n\n" + "\n\n".join(feedback[-2:]) + "\nFix the proof."

        if not interactive_first and (r := interactive_rung()):
            return r

        # 3) sketch + lemmas
        sk_prompt = (f"Approved statement (keep name `{name}` and statement exactly):\n```lean\n{statement_file}\n```\n"
                     + (f"Hints: {hints}\n" if hints else "") + (f"{round_hint}\n" if round_hint else ""))
        for k in range(sketches):
            label = f"Writing proof sketch ({k + 1}/{sketches}): splitting main steps into lemmas" + (f" · depth {depth}" if depth > 1 else "")
            note(label, "start")
            text = _ask_safe(ask, sk_system, sk_prompt, max_tokens, res.log)
            res.attempts += 1
            if text is None:
                note(label, "fail", "model exceeded the output limit")
                continue
            sketch = extract_lean(text)
            rep = check(sketch)
            lemmas = lemma_re.findall(sketch)
            valid = rep.get("compiled") and set(rep.get("problems", [])) <= {SORRY_ONLY} and lemmas and not rep.get("errors")
            note(label, "done" if valid else "fail", f"{len(lemmas)} lemma" if valid else "; ".join(rep.get("errors", [])[:1])[:200])
            res.log.append({"kind": "sketch", "source": sketch, "verified": False, "depth": depth,
                            "lemmas": [{"name": n, "statement": statement_of(sketch, n)} for _, n in lemmas],
                            "errors": rep.get("errors", [])[:3]})
            if not valid:
                sk_prompt += f"\n\nThat sketch is not usable:\n```lean\n{sketch}\n```\n{feedback_text(rep)}\nFix it (only lemma sorries allowed)."
                continue
            assembled, failed = sketch, None
            for i, (_header_sig, lname) in enumerate(lemmas, 1):
                note(f"Proving lemma {i}/{len(lemmas)}: {lname}", "start")
                proved = _prove_lemma(assembled, lname, ask, check, res, max_tokens, depth=depth, heavy=heavy,
                                      interactive=interactive, hints=hints, progress=progress)
                note(f"Proving lemma {i}/{len(lemmas)}: {lname}", "done" if proved else "fail")
                if proved is None:
                    failed = lname
                    break
                assembled = proved
            if failed is None:
                note("Lemmas assembled; compiling the full proof in Lean", "start")
                rep = check(assembled)
                note("Lemmas assembled; compiling the full proof in Lean", "done" if rep.get("verified") else "fail")
                res.log.append({"kind": "assembled", "source": assembled, "verified": rep.get("verified", False), "errors": rep.get("errors", [])[:3]})
                if rep.get("verified"):
                    return done(assembled)
                sk_prompt += f"\n\nThe assembled proof failed:\n{feedback_text(rep)}\nWrite a new sketch."
            else:
                sk_prompt += (f"\n\nIn your sketch, lemma `{failed}` could not be proved (it may be false or too hard). "
                              "Write a different sketch that avoids it or splits it further.")
    return res


def _prove_lemma(file_text: str, lname: str, ask: Ask, check: Check, res: ProofResult, max_tokens: int, *,
                 depth: int = 1, heavy: bool = False, interactive: Interactive | None = None, hints: str = "",
                 progress: Callable | None = None) -> str | None:
    """Proves one lemma of the sketch: automation first, then the model (two attempts), then — with depth left — its own
    sketch (K6). On success returns the file with the lemma proved. Other sorries may remain; only this lemma is checked."""
    m = re.search(rf"((?:lemma|theorem)\s+{re.escape(lname)}\b.*?):=\s*by\s+sorry", file_text, re.S)
    if not m:
        return None
    stmt = statement_of(file_text, lname)

    def replace(proof_block: str) -> str:
        return file_text[:m.start()] + m.group(1) + ":= by\n  " + proof_block.strip().replace("\n", "\n  ") + file_text[m.end():]

    for kind, tactic in [("lemma_automation", AUTOMATION)] + ([("lemma_automation_heavy", AUTOMATION_HEAVY)] if heavy else []):
        candidate = replace(tactic)
        rep = check(candidate, lname, stmt)
        res.log.append({"kind": "lemma_automation", "lemma": lname, "verified": rep.get("verified", False),
                        **({"heavy": True} if kind.endswith("heavy") else {})})
        if rep.get("verified"):
            return candidate
    system = prompts.PROVE.replace("`quaera_main`", f"`{lname}`")
    prompt = (f"Prove ONLY the lemma `{lname}` in this file (other lemmas may stay `sorry`; do not change any statement). "
              f"Return the complete file.\n```lean\n{file_text}\n```")
    for attempt in range(2):
        text = _ask_safe(ask, system, prompt, max_tokens, res.log)
        res.attempts += 1
        if text is None:
            continue
        source = extract_lean(text)
        rep = check(source, lname, stmt)
        res.log.append({"kind": "lemma", "lemma": lname, "verified": rep.get("verified", False), "errors": rep.get("errors", [])[:2]})
        if rep.get("verified") and statement_of(source, lname) == stmt:
            # take only this lemma's proof; the rest of the file stays as in the sketch
            mm = re.search(rf"((?:lemma|theorem)\s+{re.escape(lname)}\b.*?):=(.*?)(?=\n(?:lemma|theorem|def|abbrev|noncomputable|open|/--)\s|\Z)",
                           source, re.S)
            if mm:
                return file_text[:m.start()] + m.group(1) + ":=" + mm.group(2).rstrip() + "\n" + file_text[m.end():]
        prompt += f"\n\nAttempt {attempt + 1} failed:\n{feedback_text(rep)}\nFix the proof of `{lname}`."
    if depth > 1:
        # K6: the lemma is attacked as a theorem of its own, one level deeper (its helpers get a distinct prefix).
        def lemma_check(source: str, name: str | None = None, approved: str | None = None) -> dict:
            return check(source, name or lname, approved or stmt)
        sub = prove_search(file_text, lname, ask, lemma_check, attempts=1, sketches=1, max_tokens=max_tokens, hints=hints,
                           depth=depth - 1, prefix=f"quaera_d{depth - 1}_step_", heavy=False, interactive=interactive,
                           progress=progress)
        res.attempts += sub.attempts
        res.log.extend({**e, "depth": depth - 1, "parent": lname} for e in sub.log)
        if sub.solved and statement_of(sub.source, lname) == stmt:
            return sub.source
    return None


# --- interactive proving (K2) -----------------------------------------------------------------------

def _target_sorry(states: list[dict], file_text: str, name: str) -> dict | None:
    span = decl_span(file_text, name)
    if not span:
        return None
    first = file_text[:span[0]].count("\n") + 1
    last = file_text[:span[2]].count("\n") + 1
    inside = [s for s in states if first <= int(s.get("line") or 0) <= last]
    return inside[0] if inside else (states[-1] if len(states) == 1 else None)


def _parse_tactics(text: str) -> list[str]:
    from .structured import try_parse
    out, _ = try_parse(text, "PROVE_STEP")
    items = out.get("tactics", []) if out else re.findall(r"```(?:lean)?\s*\n(.*?)```", text, re.S)[:3]
    tactics = []
    for t in items:
        t = str(t).strip().strip("`").strip()
        if t and len(t) < 600 and not FORBIDDEN_TACTIC.search(t):
            tactics.append(t)
    return tactics[:3]


def prove_interactive(statement_file: str, name: str, ask: Ask, check: Check, inter: Interactive, hints: str,
                      res: ProofResult, note: Callable) -> str | None:
    """Depth-first search over tactic steps: the model sees the current goal (and what failed there), proposes up to
    three single tactic steps, the REPL applies them; no progress after six tries at a state backtracks one step.
    The finished script is compiled as a normal proof file (the same gate as every other proof)."""
    base = with_proof(statement_file, name, "sorry")
    if not base:
        return None
    try:
        start = _target_sorry(inter.goals(base), base, name)
    except Exception as exc:        # REPL unavailable (no Lean, restarted process): the rung is skipped
        res.log.append({"kind": "interactive", "verified": False, "errors": [f"REPL unavailable: {str(exc)[:160]}"]})
        return None
    if not start:
        return None
    stack = [(start["proofState"], start["goal"], [])]
    tried: dict[int, list[tuple[str, str]]] = {}
    steps = 0
    system = prompts.PROVE_STEP.replace("`quaera_main`", f"`{name}`")

    def finish(script: list[str]) -> str | None:
        proof = "\n".join(script).replace("\n", "\n  ")
        source = with_proof(statement_file, name, proof)
        rep = check(source)
        res.log.append({"kind": "interactive", "source": source, "verified": rep.get("verified", False),
                        "steps": len(script), "errors": rep.get("errors", [])[:2]})
        return source if rep.get("verified") else None

    while stack and steps < inter.max_steps:
        state, goal, script = stack[-1]
        fails = tried.setdefault(state, [])
        if not fails:                                    # model-free first at every new state
            r = inter.tactic(state, AUTOMATION)
            fails.append((AUTOMATION, r.get("error") or ("goals remain" if r.get("goals") else "")))
            if not r.get("error") and not r.get("goals") and (src := finish(script + [AUTOMATION])):
                return src
        prompt = (f"Theorem `{name}` in this file:\n```lean\n{statement_file}\n```\n"
                  + (f"Hints: {hints}\n" if hints else "")
                  + (f"Tactics applied so far:\n```lean\n" + "\n".join(script) + "\n```\n" if script else "")
                  + f"Current goal state:\n```\n{goal}\n```\n"
                  + ("Tactics that FAILED at this state (do not repeat):\n" + "\n".join(f"- `{t[:120]}`: {e[:160]}" for t, e in fails[-6:]) + "\n"
                     if fails else ""))
        text = _ask_safe(ask, system, prompt, 2000, res.log)
        res.attempts += 1
        steps += 1
        moved = False
        for t in _parse_tactics(text or ""):
            if any(t == f for f, _ in fails):
                continue
            r = inter.tactic(state, t)
            if r.get("error"):
                fails.append((t, r["error"]))
                continue
            if not r.get("goals"):
                src = finish(script + [t])
                if src:
                    return src
                fails.append((t, "the finished script did not compile as a file"))
                continue
            note(f"Tactic step {len(script) + 1}: {t[:60]}", "done")
            stack.append((r["proofState"], "\n\n".join(r["goals"]), script + [t]))
            moved = True
            break
        if not moved and len(fails) >= 6:
            stack.pop()                                  # dead end: back to the previous state
    res.log.append({"kind": "interactive", "verified": False, "steps": steps})
    return None


STRATEGIES = {"baseline": prove_baseline, "search": prove_search}
