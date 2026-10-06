# Phase 0 · Foundations — status

**Date:** 2026-10-03 · **Source:** roadmap, "Phase plan → Phase 0"

The goal of Phase 0 is to lock the contracts before any code is written. All technical deliverables are in this repo and pass their tests. The contracts were approved on 2026-10-03. Three tasks that require people remain before the gate can close (see "To close the gate" below).

## Tasks

| # | Task | Status | Output |
| --- | --- | --- | --- |
| 1 | Schema of the research objects | Done | `schemas/v1/` · 11 object schemas + shared definitions · [guide](schemas.md) |
| 2 | Agent specifications and message protocol | Done | `agents/` (9), `skills/` (24), `schemas/v1/agent`, `skill`, `message` |
| 3 | Preregistration and evidence package schema (RO-Crate) | Done | `preregistration`, `evidence-package` schemas · [ADR 0007](adr/0007-ro-crate.md) · example manifest |
| 4 | Architecture decision records | Done: accepted (2026-10-03) | [docs/adr](adr/README.md) · 8 records |
| 5 | Eval sets and baselines | Partial | Critic test and citation set measured; known findings and miniF2F pending (below) |
| 6 | License, contributing guide, code of conduct | Done (contact address missing) | `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` |
| 7 | Usage policy and QuaeraLabs AI label | Done: v1.0 approved (2026-10-03) | [usage policy](policy/usage-policy.md) · [AI label](policy/ai-label.md) |
| 8 | Interviews with 10 researchers | Preparation done, interviews not held | [interview guide](research/interview-guide.md) · [candidate list](research/alpha-candidates.csv) |

## Where the schemas are tested

`tools/validate.py` enforces the following rules alongside the schemas, and `tests/test_validate.py` contains a negative test for each one that deliberately breaks the rule (25 tests):

- Referential integrity and unique IDs
- The preregistration digest (sha256) must match the locked fields
- A full run must start after the preregistration and a successful pilot
- Lean "verified" only with sorry-free compiler output
- Only the Verifier verifies; a cross-model claim must be consistent with the model family
- A hypothesis can be "supported" or "refuted" only with evidence that passed the Verifier and while no objection is open
- Question and objection messages cannot go unanswered; a debate is at most 3 rounds
- No agent may publish or raise the budget cap; the Verifier cannot write code; an agent cannot be assigned a skill beyond its permissions; the AI label must contain "QuaeraLabs"

Three example studies (`examples/`) show end-to-end use of the schema: a theorem proved with Lean, an ML experiment with an objection + preregistration amendment + partial rerun + derived hypothesis, and a preregistered hypothesis refuted by a verified negative result. The numbers in the examples are illustrative; the Lean file was not compiled in this environment.

## Baselines

Source: `evals/results/baseline-2026-10-03.json`

| Runner | Set | Result |
| --- | --- | --- |
| `tools/citations.py` (live arXiv + Crossref) | Citation verification, 14 cases | 14/14 correct · fabricated accepted: 0 · unreachable: 0 |
| Majority-class baseline (not an LLM) | Critic test | Catch 20% (6/30) · false alarm 100% (6/6) |
| LLM Critic: claude-opus-5-5, blind, single attempt, without the QuaeraLabs prompt | Critic test | Detection 100% (30/30) · correct category 86.7% (26/30) · false alarm 0% (0/6) |

**Interpretation:** Critic test v0.1 is **too easy**. Because the errors are stated explicitly in the report text, a general-purpose model found all of them without any QuaeraLabs prompt; in its current form the set cannot tell a good Critic from a bad one. All four category mismatches are defensible (e.g. "training on evaluation data" is both methodology and leakage), meaning the categories overlap.

**To do for v0.2** (at the start of Phase 1, with the domain advisor):
- Cases where the error is not stated in the text and must be inferred from numbers, tables or config (e.g. train and test sizes sum to more than the dataset).
- Labeling that allows more than one correct category.
- More clean control cases (at least 15) and "almost wrong" borderline cases.
- A hidden test split not seen during development.

Phase 0 also tried a rule-based Critic whose rules were written by looking at the cases; since it was fitted to the set (30/30) it was meaningless and was removed.

## Pending eval work

| Work | Why it is pending | When |
| --- | --- | --- |
| 20 cases of the "known findings" set | Choosing open-code, small-scale findings from papers published after the models' knowledge cutoff requires domain expertise. The selection protocol is ready (`evals/sets/known-findings.yaml`). | With the domain advisor, before the Phase 0 gate |
| miniF2F baseline | The Lean toolchain is set up in Phase 1 | Phase 1 |
| Baselines with QuaeraLabs agent prompts | The agents and model gateway are written in Phases 1–2; API spending requires approval | Phases 1–2 |

## To close the gate

The Phase 0 exit gate in the roadmap: *"Schemas, agent specifications and message protocol approved; eval sets run and baselines recorded."*

- [x] **Team approval:** schemas, agent specifications, message protocol, 8 ADRs, usage policy and AI label approved on 2026-10-03.
- [ ] **10 researcher interviews** are held and the alpha candidate list is filled in.
- [ ] **20 cases of the "known findings" set** are chosen by the domain advisor (decision: the selection is left entirely to the advisor).
- [ ] **Contact address** is decided and written into `CODE_OF_CONDUCT.md` (the project address has not been set up yet).
- [x] Eval sets run and the first baselines are recorded (Critic test, citation verification).
