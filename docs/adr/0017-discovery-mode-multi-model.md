# 0017 · Discovery mode and multiple models: Codex CLI, OpenCode CLI, cross-check, attacking open problems

**Status:** Proposed · **Date:** 2026-10-05 · **Related:** [0003](0003-model-gateway.md), [0014](0014-agent-intelligence.md), [0016](0016-ui-v3-manager-contextual-memory.md)

## Context
The Riemann Hypothesis attempt showed one thing: because Verification mode rewards testability, it replaced an open problem with a provable restricted claim. That is the right behaviour for verification, but the researcher wants something else. The goal is new discovery: the first target is to prove or refute the Riemann Hypothesis in full and in a verifiable way.

Codex CLI and OpenCode CLI are installed on this machine. Different model families give both diversity of ideas and independent cross-checks.

## Decision
1. **Providers** (`src/quaera/providers.py`).
   - **`CodexCLIProvider`:** `codex exec --json --sandbox read-only --ephemeral`, in an empty temporary directory. Family `openai`; the cost profile is translated into reasoning effort.
   - **`OpenCodeCLIProvider`:** `opencode run --format json --agent plan`, with the prompt in a file. The family is derived from the model prefix (`opencode/*` → `opencode`).
   - **Auto-detection (`detect`):** makes no model call; it looks at the binary, the version and, for Codex, the login file.
   - **Selection:** globally with `QUAERA_PROVIDERS`, or per project by choosing model families (meta `families`).
   - **Where to check:** `quaera doctor` and Settings → "Test". The test asks a small real question.
   - **Role assignment:** Claude is the primary family. The Critic and the Verifier go to a different family through the `mustDifferFrom` rule. The parallel lane B (hypothesis, proof candidate) runs on the second family.
   - **Billing:** subscription CLIs do not report a per-call price. The event records `costUsd=0, billing: "subscription"`. The budget cap limits only measured spending; the call count of these CLIs is limited by the round limit.
2. **Discovery mode** (`src/quaera/discovery.py`, `mode: "discover"`, math only).
   - Stages: `literature → landscape → target → design → ideation → cross_review → strategy_approval → formalize → statement_review → program → attack → synthesis → analysis → result_review → verification → conclude → report`
   - **The target is not narrowed.** The question is turned into a single claim (`scopeRelation: full`) and the human approves it.
   - **Ideation.** `QUAERA_IDEATION_LANES` (default 4) lanes run in parallel across families. Each lane is given a creative perspective (`prompts.DISCOVERY_LENSES`, skill `creative-thinking`).
     - **Lane A is always free.** In the other lanes the agent may also drop its perspective; it is guided, but creativity is not restricted.
     - Each strategy is asked for a "kill test" and for how it gets past the known obstacles.
   - **Cross-review.** Each strategy is reviewed by a family other than its author's: plausibility, novelty, obstacle awareness, testability and fatal flaw. The human chooses the strategy; the others remain as fallbacks.
   - **Lemma program.** The strategy is split into 2–6 lemmas whose Lean statements compile with `sorry`.
   - **Attack rounds** (`QUAERA_DISCOVERY_ROUNDS`, default 3):
     - in each round every open lemma goes to another family;
     - numerical testing is done and proof search runs with two-family lanes;
     - a refutation is tried;
     - a refuted lemma is repaired, and if it cannot be repaired the next strategy is taken up.
   - **Synthesis.** If all lemmas are verified, an attempt is made to build the main theorem or its negation in Lean from these lemmas.
3. **Honesty rule.**
   - The main claim counts as proved or refuted only if Lean accepts it or its negation: no `sorry`, standard axioms, and the Verifier has recompiled it in a clean process.
   - Every verified lemma is also recompiled in a clean process (`lemma.reverified`).
   - Verified lemmas, refuted intermediate claims and abandoned strategies are reported as "discoveries" and written to the lab memory; they do not count as proof of the main claim.
   - For the Riemann Hypothesis the target is Mathlib's `RiemannHypothesis` definition; that this definition compiles was tested in a real Lean test.
4. **UI.**
   - In new research, Verify/Discover and the model families are chosen. The Discover budget cap is $500 (Verify $50).
   - The project page shows the approach map, the strategy board (proposing family, perspective, reviewing family, scores, fatal flaw, status) and the lemma program (rounds, families, Lean proof or refutation, clean recompilation).

## Consequences
- An open problem is no longer replaced with a "restricted but provable" claim. The result is either a Lean-verified solution or an honest "open", plus verified intermediate findings.
- Realistic expectation: for a problem like the Riemann Hypothesis the probability of a solution is very low. The platform still makes attempts, cross-examination and the quick elimination of wrong paths cheap. Every verified intermediate result found is permanently recorded and goes into memory.
- **Known limit (OpenCode):** OpenCode loads the user's global configuration (MCP servers, plugins). For example, a memory plugin may record sessions. Codex keeps no session record with `--ephemeral`.
