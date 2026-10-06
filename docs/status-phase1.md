# Phase 1 · Core engine and math loop — status

**Date:** 2026-10-04 · **Source:** roadmap, "Phase plan → Phase 1"

The goal of Phase 1 was for a single research loop to run end to end from the command line, without a UI. The loop works: a math question asked from the CLI reached a report containing a proof re-verified in a clean environment, with real models and real Lean 4 + Mathlib.

## Tasks

| Task | Status | Where |
| --- | --- | --- |
| Orchestrator: persistent state machine, resume, crash recovery, branching | Done | `src/quaera/orchestrator.py`, `store.py` |
| Model gateway: multiple providers, token counter, budget cap | Done (see note 1) | `src/quaera/gateway.py` |
| Sandbox: isolated execution, resource limits, network off | Done (bwrap; see [ADR 0009](adr/0009-sandbox-bwrap-and-lean-repl.md)) | `src/quaera/sandbox.py` |
| Tool layer: skill tools over MCP | Done | `src/quaera/tools.py`, `mcp_servers/` |
| Lean 4 + Mathlib: compilation, error feedback, proof status | Done | `src/quaera/lean.py`, `lean/` (Mathlib v4.34.1) |
| Five roles (Director, Literature, Hypothesis, Engineer, Critic) | Done (see note 2) | `src/quaera/prompts.py`, `agents/` |
| Local store, event log, shared board, message routing, permissions enforced in code | Done | `src/quaera/store.py`, `permissions.py` |
| Preregistration, pilot run, novelty check (brought forward from Phase 2) | Done (for math) | `orchestrator.py` |

**Note 1 — providers.** In Phase 1 the real provider is the user's logged-in `claude` CLI (no tools, no session, without loading user settings). The LiteLLM adapter that works with an API key was not written; the decision in ADR 0003 will be completed in Phase 2. As a result the cross-model rule cannot be applied yet, and reports state this explicitly as "Cross-model review: no".

**Note 2 — deterministic roles.** The Director's stage transitions and, in math, the Experiment designer, Analyst, Verifier and Writer run from templates, without an LLM. In the records the model appears as `quaera/deterministic`; this is a deliberate transparency choice. The Verifier's job in math really is deterministic: compiling the proof once in a clean sandbox.

## Exit gate

| Criterion | Result |
| --- | --- |
| A math question started from the CLI reaches a report that is verified with Lean or ends explicitly with "not found" | **Passed.** Live run below |
| A baseline is measured on miniF2F | **Passed.** Table below |
| The budget cap is never exceeded in tests | **Passed.** Unit test (cap $0.05, call blocked, spend ≤ cap); in live runs the real cost of every call is below the worst-case estimate computed beforehand |

### Live end-to-end run

`quaera ask "Is n³ - n divisible by 6 for every natural number n?" --budget 3 --auto-approve-under 3`

- All 13 stages completed; the hypothesis was **supported**. The theorem `∀ n : ℕ, 6 ∣ n ^ 3 - n` compiled in Lean 4 without `sorry` or non-standard axioms, and the Verifier recompiled it in a clean environment (`reproduced: yes`).
- The Critic approved, with the reasoning that truncated subtraction in ℕ does not break the statement.
- 7 model calls, **$0.154** total (cap $3). No violations in the final check of the project rules.
- Models: Literature `claude-haiku-4-5`, Hypothesis / Engineer / Critic `claude-opus-5-5`.

### Live run 2: an open problem (Goldbach conjecture)

`quaera ask "Can every even natural number greater than two be written as the sum of two primes?" --budget 1.5 --auto-approve-under 1.5`

- The Hypothesis agent proposed three hypotheses: the full conjecture (`full`), the bounded range `4 ≤ n ≤ 1000` (`restricted`) and deriving ternary Goldbach from binary (`related`).
- Auto-approve mode chose the first hypothesis; it was proved in Lean 4 for `n ≤ 1000` and recompiled in a clean environment.
- Report summary: **"Only a restricted version of the question was proved; the original question remains open."** The Critic's independent scope note also went into the report.
- 9 model calls, **$0.49** (cap $1.50). No rule violations.

This question exposed two bugs on the first attempt (below, "Scope overstatement" and "Provider budget argument"). The first attempt is kept as evidence under `~/.quaera/projects/2026-10-04-goldbach-ilk-koşu-kapsam-hatasi`.

### miniF2F baseline

`uv run python evals/minif2f_run.py --split test --n 20 --attempts 2 --budget 5 --profile balanced` · result: `evals/results/minif2f-test-2026-10-04.json`

| Measure | Value |
| --- | --- |
| Sample | 20 problems from the test split (seed 0) |
| Statements that compile on current Mathlib | 20 / 20 |
| Model | Engineer role, `claude-sonnet-5-5` (cost profile: balanced) |
| Solved (REPL + recompilation in a clean environment) | **19 / 20 · pass@2 = 95%** |
| Spent | $0.33 (cap $5) |

**How to read it:**
- **An optimistic ceiling.** miniF2F has been public since 2021; the problems and their solutions may be in the model's training data. This number does not show the agent team's success on new mathematics.
- **The sample is small.** With 20 problems the confidence interval of a 95% result is wide (roughly 75–99%).
- **One attempt was mostly enough.** All 19 of the 19 solutions came on the first attempt; the only unsolved problem is IMO 1982 P1.
- **The verification chain was tested.** In the first run the script did not save the proofs; in the second run they were saved and every proof was recompiled in a clean environment, independently of the REPL. Along the way we found a parser bug where, in clean mode, the axiom line got mixed into a warning message and could not be read, and a hole where the check could be fooled by fake "depends on axioms" output; both were fixed and tests were added. The 19 saved proofs were rechecked with the fixed verifier without any model calls (`evals/minif2f_reverify.py`).
- **Next step (Phase 2).** For a contamination-free measurement, build a set of problems published after the models' knowledge cutoff (the math counterpart of the "known findings" set) and increase the sample to 100+ problems.

## Test status

| Test | Count | Command |
| --- | --- | --- |
| Contracts and rules (Phase 0) | 25 | `uv run pytest` |
| Orchestrator: full loop, resume after crash, budget cap, approval rejection, branching, permissions, model error, restricted scope | 8 | `uv run pytest` |
| Real Lean + Mathlib: valid proof, sorry, native_decide, modified statement, false claim, custom axiom, foreign import, clean mode, fake axiom output, axiom line after a warning | 10 | `uv run pytest -m lean` |

## Bugs caught and fixed in Phase 1

- **REPL reader deadlock.** When `select()` and `readline()` were used together, the response stayed in Python's internal buffer and the reader waited forever. In early measurements this looked like "Mathlib takes minutes to load". Fixed; Mathlib loads in ~5 s and a check takes ~1 s.
- **Permission matrix gap.** The Verifier could not write its own compilation logs; the permission check caught this. The Verifier was given `artifact` write permission (the log-recording skill was already assigned to it).
- **Scope overstatement (the most important).** When asked the Goldbach conjecture, the Hypothesis agent silently restricted the question to `n ≤ 10000`; the proof was correct, but the report said "hypothesis supported" and looked as if it had answered the question. Hypotheses now declare their scope (`full` / `restricted` / `related`), the Critic separately compares the formal statement against the *original question*, and if either sees a restriction the report says "only a restricted version of the question was proved; the original question remains open". A backward-compatible `scopeRelation` field was added to the hypothesis schema.
- **Provider budget argument.** The gateway passed its own per-call estimate to the CLI as the budget; the CLI computed the cost differently, cut the call off, and the orchestrator crashed without writing a report. The CLI is now given the project's remaining budget (the pre-check stays in place, the cap still cannot be exceeded), the cost of a failed call is recorded as spend, and on a model error the research retries once and then stops in a controlled way and writes a report. The crashed project continued from its stage with `quaera resume` and completed (a live resume test).
- **Axiom parser and fake-output hole.** In clean mode a warning message swallowed the axiom line; in addition a proof could print a fake axiom line with `#eval`. Now only the last line belonging to the theorem name is read, and output-producing commands (`#eval`, `#print`, `run_cmd`, `elab`, `macro`…) are forbidden in proof files.
- **New axiom name for `native_decide`.** In Lean 4.34, `native_decide` uses an axiom generated per theorem instead of `Lean.ofReduceBool`; the allowlist approach (only propext, Classical.choice, Quot.sound) rejected this as well.

## Known limitations

- **Literature search is weak.** In the live run the arXiv searches returned irrelevant results and the Literature agent called a textbook-level result "novel". The novelty assessment is not reliable yet; building the search queries and treating "no related result found" as a separate decision were left to Phase 2.
- **Single provider.** The cross-model rule cannot be applied (Note 1).
- **Linux only.** The sandbox relies on `bwrap`; macOS and Windows come in Phases 2–4.
- **Memory.** The Lean REPL holds ~5–6 GB of RAM with Mathlib.
- **Auto-approve mode picks the first option.** With `--auto-approve-under`, hypothesis selection is automated too; in the Goldbach run the restricted hypothesis was chosen. The choice is recorded together with its scope label and the report states it explicitly, but in interactive mode a human should make this decision.
- **No UI.** Comes in Phase 3.
