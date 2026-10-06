# 0014 · Agent intelligence: exploration, diverse hypotheses, formal faithfulness, proof search, thinking budget

**Status:** Proposed · **Date:** 2026-10-04 · **Related:** [0005](0005-orchestration.md), [0009](0009-sandbox-bwrap-and-lean-repl.md)

## Context
Before testing against the Millennium problems, the user wanted the team to be smarter, more creative and more logical. Measurement came first: since our own sets had hit the ceiling, a PutnamBench sample was taken (`evals/putnam_run.py`). The baseline run showed two weaknesses of the current system:
- the proof was single-shot with a short loop,
- when the model exceeded its output limit, an attempt was wasted: the error cut the loop and money was spent.

## Decision (in the order of the math flow)
1. **Diverse hypotheses and hard filtering (#4).**
   - Generation uses two perspectives: direct, generalisation and special case; analogy, opposing view and edge case.
   - Near-identical candidates are merged.
   - The Critic scores each candidate: testability 0.30, plausibility 0.20, novelty 0.15, scope 0.25, cost 0.10.
   - The best 3 candidates come to approval with their scores and rationale; the scores stay in the `hypotheses.ranked` event.
   - A candidate that breaks the contract is skipped and does not crash the research.
2. **Experimental exploration (#3).**
   - In math, the approved hypothesis is tested on small cases with Python in the sandbox and a counterexample is searched for (the `explore` stage).
   - If there is a counterexample candidate, a refutation is tried in Lean before the proof; the observations become hints for the prover.
   - In ML, a deterministic profile of the data is produced before the hypothesis (the `data_profile` stage).
3. **Formalisation faithfulness (#6).**
   - A model that has not seen the hypothesis translates the Lean statement back into Turkish; the Critic compares the hypothesis with this independent reading.
   - Before the proof, a **vacuity** check is done: if `False` can be derived from the assumptions, the research stops with a rationale.
   - Before the proof, a **refutation** is tried: the negation is attempted first with automation and, if no proof is found, once with the model.
   - If the refutation succeeds, the hypothesis is **refuted** in a Lean-verified way. Re-verification, review and the honesty audit cover this path too.
4. **Proof search (#2, `prover.py`).**
   - Model-free automation is tried first.
   - Then come error-tolerant whole-proof attempts; if the output limit is exceeded, a shorter proof split into lemmas is requested.
   - Then a sketch and lemmas: each lemma is proved separately (automation first, then the model) and the file is assembled. If a lemma fails, a new sketch is requested once.
   - The orchestrator and the evaluation use the same code.
5. **Thinking budget (#8).** An `effort` is defined per role: Engineer, Critic and Hypothesis `high`, Literature `low`. The worst-case cost estimate is scaled by effort; the hard cap is still the remaining budget.

## Measurement
`evals/putnam_run.py`: the same 20 Putnam problems, `--strategy baseline --effort default` (baseline) and `--strategy search` (new). Results are in `evals/results/putnam-7-baseline-*.json` and `evals/results/putnam-7-search-*.json`.

## Consequences
- Model calls and cost per research increase (two hypothesis calls, scoring, back-translation, exploration, sketch/lemma calls). The budget cap does not change; the cap decides how much search is done.
- Cross-model (#7) is deliberately left out: it needs a second provider key.
