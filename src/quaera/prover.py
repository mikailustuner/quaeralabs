"""Proof strategies. The orchestrator (stage_prove) and the evals (evals/putnam_run.py) use the same code,
so the eval measures the product's real component.

  baseline  write the whole proof in one go; fix it with compiler feedback at most `attempts` times (Phase 1 behaviour)
  search    (#2) free automation → error-tolerant whole-proof attempts → sketch + lemmas (each lemma separately:
            automation first, then the model) → assemble; if a lemma fails, one new sketch

`ask(system, prompt, max_tokens) -> text` is the model call (the caller does the budget check).
`check(source, name=None, approved=None) -> dict` is the Lean compilation: {"verified", "compiled", "errors", "problems", ...}
(LeanReport.__dict__ shape). Without name/approved, the main theorem and its approved statement are used.
"""

from __future__ import annotations

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
LEMMA_RE = re.compile(r"((?:lemma|theorem)\s+(quaera_step_\d+)\b.*?):=\s*by\s+sorry", re.S)
SORRY_ONLY = "Proof contains `sorry`."


def with_proof(statement_file: str, name: str, proof: str) -> str | None:
    """Replaces the proof of theorem `name` in the file with `proof` (the header and other definitions are kept)."""
    m = re.search(rf"((?:theorem|lemma)\s+{re.escape(name)}\b.*?):=", statement_file, re.S)
    if not m:
        return None
    return statement_file[:m.end()] + f" by\n  {proof}\n"


def _ask_safe(ask: Ask, system: str, prompt: str, max_tokens: int, log: list) -> str | None:
    """A model error (e.g. output limit exceeded) does not break the loop: it is logged and None is returned."""
    try:
        return ask(system, prompt, max_tokens)
    except ModelError as exc:
        log.append({"kind": "model_error", "error": str(exc)[:200]})
        return None


PARALLEL_HINT = ("\n\nStrategy for THIS candidate: structure the proof explicitly — state the key intermediate facts as "
                 "`have` steps (or small helper lemmas above the theorem) and close each with a short tactic.")


def prove_search(statement_file: str, name: str, ask: Ask, check: Check, attempts: int = 2,
                 hints: str = "", max_tokens: int = 16000, sketches: int = 2,
                 progress: Callable | None = None, parallel: int = 1) -> ProofResult:
    """`progress(step, status, detail="", lane=None)`: for the live view (status: start/done/fail).
    `parallel=2`: in the first round two different candidate proofs are written concurrently (direct + split into steps); Lean checks are sequential."""
    res = ProofResult(False, None, 0)
    note = progress or (lambda *a, **k: None)

    def done(source: str) -> ProofResult:
        res.solved, res.source = True, source
        return res

    # 1) free automation
    auto = with_proof(statement_file, name, AUTOMATION)
    if auto:
        note("Trying automation tactics (no model)", "start")
        rep = check(auto)
        res.log.append({"kind": "automation", "verified": rep.get("verified", False), "errors": rep.get("errors", [])[:2]})
        note("Trying automation tactics (no model)", "done" if rep.get("verified") else "fail")
        if rep.get("verified"):
            return done(auto)

    # 2) whole-proof attempts (a model error does not break the loop); optionally two parallel candidates in the first round
    system = prompts.PROVE.replace("`quaera_main`", f"`{name}`")
    prompt = (f"Approved statement (do not change; keep the theorem name `{name}`):\n```lean\n{statement_file}\n```\n"
              + (f"Hints: {hints}\n" if hints else ""))
    for attempt in range(attempts):
        if attempt == 0 and parallel > 1:
            from concurrent.futures import ThreadPoolExecutor
            lanes = ["A", "B"]
            for lane in lanes:
                note(f"Writing candidate proof ({'direct' if lane == 'A' else 'step-by-step'})", "start", lane=lane)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_ask_safe, lambda s, p, n, lane=lane: ask(s, p, n, lane=lane), system,
                                       prompt + ("" if lane == "A" else PARALLEL_HINT), max_tokens, res.log) for lane in lanes]
                texts = [f.result() for f in futures]
            for lane, text in zip(lanes, texts):
                note(f"Writing candidate proof ({'direct' if lane == 'A' else 'step-by-step'})", "done" if text else "fail", lane=lane)
        else:
            note(f"Writing whole-proof attempt {attempt + 1}/{attempts}", "start")
            texts = [_ask_safe(ask, system, prompt, max_tokens, res.log)]
            note(f"Writing whole-proof attempt {attempt + 1}/{attempts}", "done" if texts[0] else "fail")
        feedback = []
        for i, text in enumerate(texts):
            lane = ["A", "B"][i] if len(texts) > 1 else None
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
                            "errors": rep.get("errors", [])[:3], **({"lane": lane} if lane else {})})
            note("Compiling with Lean", "done" if rep.get("verified") else "fail", "; ".join(rep.get("errors", [])[:1])[:200], lane=lane)
            if rep.get("verified"):
                return done(source)
            feedback.append(f"Attempt failed:\n```lean\n{source}\n```\nCompiler feedback:\n{feedback_text(rep)}")
        prompt += "\n\n" + "\n\n".join(feedback) + "\nFix the proof."

    # 3) sketch + lemmas
    sk_system = prompts.SKETCH.replace("`quaera_main`", f"`{name}`")
    sk_prompt = f"Approved statement (keep name `{name}` and statement exactly):\n```lean\n{statement_file}\n```\n" + (f"Hints: {hints}\n" if hints else "")
    for k in range(sketches):
        note(f"Writing proof sketch ({k + 1}/{sketches}): splitting main steps into lemmas", "start")
        text = _ask_safe(ask, sk_system, sk_prompt, max_tokens, res.log)
        res.attempts += 1
        if text is None:
            note(f"Writing proof sketch ({k + 1}/{sketches}): splitting main steps into lemmas", "fail", "model exceeded the output limit")
            continue
        sketch = extract_lean(text)
        rep = check(sketch)
        lemmas = LEMMA_RE.findall(sketch)
        valid = rep.get("compiled") and set(rep.get("problems", [])) <= {SORRY_ONLY} and lemmas and not rep.get("errors")
        note(f"Writing proof sketch ({k + 1}/{sketches}): splitting main steps into lemmas", "done" if valid else "fail",
             f"{len(lemmas)} lemma" if valid else "; ".join(rep.get("errors", [])[:1])[:200])
        res.log.append({"kind": "sketch", "source": sketch, "verified": False,
                        "lemmas": [{"name": n, "statement": statement_of(sketch, n)} for _, n in lemmas],
                        "errors": rep.get("errors", [])[:3]})
        if not valid:
            sk_prompt += f"\n\nThat sketch is not usable:\n```lean\n{sketch}\n```\n{feedback_text(rep)}\nFix it (only lemma sorries allowed)."
            continue
        assembled, failed = sketch, None
        for i, (header_sig, lname) in enumerate(lemmas, 1):
            note(f"Proving lemma {i}/{len(lemmas)}: {lname}", "start")
            proved = _prove_lemma(assembled, lname, ask, check, res, max_tokens)
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


def _prove_lemma(file_text: str, lname: str, ask: Ask, check: Check, res: ProofResult, max_tokens: int) -> str | None:
    """Proves one lemma of the sketch: automation first, then the model (two attempts). On success returns the file with
    the lemma's sorry replaced by its proof. Other sorries may remain; only this lemma is checked for verification."""
    m = re.search(rf"((?:lemma|theorem)\s+{re.escape(lname)}\b.*?):=\s*by\s+sorry", file_text, re.S)
    if not m:
        return None
    stmt = statement_of(file_text, lname)

    def replace(proof_block: str) -> str:
        return file_text[:m.start()] + m.group(1) + ":= by\n  " + proof_block.strip().replace("\n", "\n  ") + file_text[m.end():]

    candidate = replace(AUTOMATION)
    rep = check(candidate, lname, stmt)
    res.log.append({"kind": "lemma_automation", "lemma": lname, "verified": rep.get("verified", False)})
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
    return None


STRATEGIES = {"baseline": prove_baseline, "search": prove_search}
