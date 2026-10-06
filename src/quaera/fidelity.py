"""Formalization fidelity (#6): tests the Lean statement itself before money is spent on a proof.

  vacuity      if the assumptions contradict each other the statement is "vacuously true" (e.g. `n > 5 → n < 3 → …`); we try to derive `False`
  refutation   we try to prove the statement's negation with automatic tactics (if needed, with one model attempt);
               on success the hypothesis is REFUTED by a counterexample verified in Lean
  back-translation  a model that sees only the Lean file translates the statement into natural language; the Critic compares it with the hypothesis

The files keep the header the agent wrote (import/open/helper definitions); only the theorem changes.
"""

from __future__ import annotations

import re

# `done` guard: a tactic that makes progress without closing the goal must not stop the `first` chain (tested on real Lean).
AUTOMATION = ("first | decide | omega | (norm_num; done) | (simp; done) | (push_neg; decide) | (push_neg; norm_num; done)"
              " | (push_neg; simp; done) | aesop")
VACUITY_AUTOMATION = "first | omega | linarith | nlinarith | (simp_all; done) | aesop | (norm_num at *; done) | decide"
OPEN = "([{⦃"
CLOSE = {"(": ")", "[": "]", "{": "}", "⦃": "⦄"}


def split_theorem(source: str, name: str) -> tuple[str, str, str] | None:
    """Splits the file into (header, binders, conclusion). `theorem name (x : ℕ) (h : P x) : Q x := …`
    → ("import …\n", "(x : ℕ) (h : P x)", "Q x"). None if it cannot be parsed."""
    m = re.search(rf"(?:theorem|lemma)\s+{re.escape(name)}\b", source)
    if not m:
        return None
    header, rest = source[:m.start()], source[m.end():]
    i, binders = 0, []
    while i < len(rest):
        c = rest[i]
        if c.isspace():
            i += 1
            continue
        if c in OPEN:
            depth, j = 0, i
            while j < len(rest):
                if rest[j] in OPEN:
                    depth += 1
                elif rest[j] in CLOSE.values():
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth != 0:
                return None
            binders.append(rest[i:j + 1])
            i = j + 1
            continue
        if c == ":" and not rest.startswith(":=", i):
            end = rest.find(":=", i)
            if end < 0:
                return None
            return header, " ".join(binders), rest[i + 1:end].strip()
        return None
    return None


def as_prop(binders: str, concl: str) -> str:
    """Wraps the binders in ∀: Lean 4 accepts the form `∀ (x : ℕ) (h : P x), Q x`.
    Implicit/instance binders ({α}, [inst]) are turned into explicit binders."""
    if not binders:
        return concl
    explicit = re.sub(r"[{⦃\[]([^{}⦃⦄\[\]]*)[}⦄\]]", lambda m: f"({m.group(1)})" if ":" in m.group(1) else f"(_inst : {m.group(1)})", binders)
    return f"∀ {explicit}, {concl}"


def refutation_file(source: str, name: str, proof: str = f"by\n  {AUTOMATION}") -> tuple[str, str] | None:
    """Returns (file, statement to approve): `theorem quaera_refute : ¬ (∀ …, …)`."""
    parts = split_theorem(source, name)
    if not parts:
        return None
    header, binders, concl = parts
    stmt = f"theorem quaera_refute : ¬ ({as_prop(binders, concl)})"
    return f"{header}{stmt} := {proof}\n", stmt


def vacuity_file(source: str, name: str) -> tuple[str, str] | None:
    """Tries to derive `False` from the assumptions. Without binders there is no vacuity: None."""
    parts = split_theorem(source, name)
    if not parts or not parts[1]:
        return None
    header, binders, _ = parts
    stmt = f"theorem quaera_vacuous {binders} : False"
    return f"{header}{stmt} := by\n  {VACUITY_AUTOMATION}\n", stmt
