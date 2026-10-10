# Capacity plan: model-agnostic research, API-key providers, budget-driven search

**Status:** Phases 1–3 implemented (2026-10-10); Phase 0 (measurement) and Phase 4 open · **Date:** 2026-10-10 · **Supersedes:** the product roadmap PDF (removed) · **Related ADRs:** [0003](adr/0003-model-gateway.md) gateway, [0005](adr/0005-orchestration.md) orchestration, [0012](adr/0012-research-memory.md) memory, [0013](adr/0013-research-tree.md) tree, [0014](adr/0014-agent-intelligence.md) agent intelligence, [0017](adr/0017-discovery-mode-multi-model.md) discovery mode

## Goal

Raise what the system can discover **regardless of which model runs it**. The team will not always run on the strongest models; the structure must turn more search, more verification and more reuse into results, so a cheap or local model still produces Lean-verified progress and a strong model goes further. Four asks drive the plan:

1. Any model should benefit: robustness to weak JSON, short context, shallow reasoning.
2. Direct API-key providers next to the CLIs (Anthropic, OpenAI, Google, OpenRouter, local OpenAI-compatible servers).
3. When a question is not solved, keep branching **until the budget is spent**, within a logical frame (no repeats, no premature stop, best node first).
4. Beyond memory: everything else that compounds capacity (lemma bank, interactive prover, model-free kill tests, semantic Mathlib search, caching, scheduling, measurement).

What stays fixed: the Lean gate (only verified statements count), the hard budget cap, the event log as the single record, the contract checks (`contracts.py`), the honesty audit, human approval rules.

## Implementation status (2026-10-10)

Phases 1, 2 and 3 are implemented; their behaviour is covered by tests that need no model and no network, and the Lean parts are
covered by tests against real Lean 4 + Mathlib. Phase 0 (the measurement harness) is not built yet, so the **measured** acceptance
criteria below (solve rate at equal budget, cost per solve, …) are still open: the mechanisms exist, the numbers do not.

| Item | State | Where | Notes |
| --- | --- | --- | --- |
| R1 structured output | done | `structured.py`, `Orchestrator.ask_json` | JSON mode on API providers, coercion, cheap repair pass, re-ask, degradation for safe call sites (landscape, rankings, back-translation, reviews). Shapes are Python dicts instead of `schemas/v1/outputs/*.json` (simpler; content rules stay in the contracts). |
| R2 budget-driven search | done | `prover.prove_search(rounds, more)`, `Gateway.share_cap` | `QUAERA_SEARCH_ROUNDS` (3) rounds while the share lasts; Discovery attack continues past `ROUNDS` up to `QUAERA_DISCOVERY_MAX_ROUNDS`. |
| P1 provider registry | done | `registry.py`, `/api/registry`, Settings, `quaera providers` | Anthropic, OpenAI, Gemini, OpenRouter, OpenAI-compatible; keys in `~/.quaera/secrets.env` (600), never returned. |
| P2 role routing | done | `Gateway.routing`, `quaera route` | Providers keyed by route id; the cross-model rule compares families. |
| P3 API features | done | `LiteLLMProvider` | Backoff with jitter on retryable errors, Anthropic prompt caching for long system prompts, exact accounting (no CLI overhead, safety 1.0), concurrency limits. |
| P4 budget dimensions | done | `gateway.Limits`, `--max-calls/--max-hours/--max-tokens`, composer fields | Calls, tokens, wall-clock; keep-trying stops on any of them. |
| C1 completion cache | done | `cache.py` | Off with `QUAERA_CACHE=off`; best-of-N samples carry a nonce. |
| K1 lemma bank | done | `bank.py` | Written after the Verifier's clean recompile; exact reuse without a model call; hints for program, prover and formalization; reuse listed in the report. Recall is bm25 (no embeddings yet). |
| K2 interactive prover | done | `lean.goals/tactic`, `prover.prove_interactive` | REPL proof states; depth-first with backtracking; the finished script passes the normal Lean gate. |
| K3 kill tests and automation | done | `_plausible`, `AUTOMATION_HEAVY` | `plausible` in verification and discovery; a counterexample leads to a Lean refutation attempt, never counts as one. The model-free Python fallback is not built. |
| K4 Mathlib index | done | `mathlib_index.py`, `mathlib.search` | FTS5 over names, signatures, docstrings and notation words; built lazily per Mathlib rev. No embeddings, no online Loogle. |
| K5 best-of-N | done | `prove_search(samples)` | Lanes A…N with different hints, temperature on API providers, other families for B…N. |
| K6 recursive decomposition | done | `_prove_lemma(depth)` | Depth 2 by default, 3 for models with a low compile rate. |
| T1 tree search | done | `tree.iterate`, `progress_score` | Best-first with `beam`; `QUAERA_BEAM` defaults to **1** because every parallel branch starts its own Lean REPL (~5–6 GB). |
| T2 continue branches | done | `branch_project(kind="continue")` | Discovery only; the lemma program and its statuses are kept. |
| T3 stop rule | done | `tree.decide`, `coverage_gaps` | Untried lenses / change kinds force a second opinion; `branch.stop_rejected` / `branch.stop_confirmed`. |
| T4 dynamic budget | done | score-weighted slices, `/budget`, `/keep-trying` | Top-up reaches a running gateway; keep-trying is read after each run. |
| S1 cascade | done | `Gateway.ladders`, `Orchestrator.llm` | Escalates on model errors; in the proof search one rung per two failed checks. |
| S2 capability profiles | done | `capability.py`, `quaera models` | Records JSON and compile outcomes per model; adapts samples, REPL-first, output length and depth. The deep probe of M2 is not built. |
| S3 committees | done | Director stop (T3), cross-review with ≥3 families, `Orchestrator.confirm_blocking` | A `blocking` objection (math result review, ML critique) is put to a second family that is not the author's; unconfirmed → `high`. The objection stays **open** either way, so the honesty gate is unchanged. |
| S4 scheduling | done | `scheduler.py`, `/api/jobs`, Lab "Parallel work" card | Every parallel job (lanes, reviews, lemma attacks, seed runs, tree branches) goes through one scheduler with a global bound (`QUAERA_MAX_JOBS`, 8) shared by all projects; nested jobs take no slot (no deadlock); per-provider limits stay in the gateway. |
| First measurement | done | `evals/capacity_compare.py`, `evals/results/capacity-compare-20261010T131502.json` | 8 Putnam problems, Claude Haiku (cheap profile), $0.19 per task and arm, $3 hard cap ($2.74 spent). **Solved: before 0/8, after 0/8** — no solve gain is shown at this budget on this set. The new search made 79 model calls for $1.26 against 32 calls for $1.48 (2.9× more attempts per dollar, through short REPL steps and model-free rungs). The last two tasks were cut by the total cap in both arms. Putnam is far above Haiku's level; a set with headroom at this model size (e.g. a harder miniF2F split) and a larger per-task budget are needed to see whether the extra attempts turn into proofs. |
| M1, M2 | open | — | Phase 0: the eval harness and the deep probe are the next step; without them the effect of Phases 1–3 is not yet measured. |
| Phase 4 | open | — | ML grids, result reuse, remote runner, observability. |

Tests: `tests/test_capacity_providers.py`, `test_structured.py`, `test_capacity_search.py`, `test_capacity_tree.py`,
`test_capacity_scheduling.py` (no model, no network) and `tests/test_lean_capacity.py` (real Lean: goal states, tactic steps, plausible, heavy automation, the interactive prover
end to end, the Mathlib index).

## Review findings (what the plan fixes)

| # | Finding | Where |
| --- | --- | --- |
| F1 | Invalid JSON → 2 retries + 1 other family → `StopResearch`. A weak model ends the research; a real example sits in the lab memory ("critic did not return valid JSON"). | `orchestrator.py:191-212` |
| F2 | Fixed attempt counts independent of budget and model: `attempts=2`, `sketches=2`, `maxFixAttempts=3`, `ROUNDS=3`, `LANES=4`. Budget is left unused while the search gives up. | `prover.py`, `discovery.py:39-41`, `agents/*.yaml` |
| F3 | The prover only sees compiler errors; no goal states, no Mathlib hits, no examples; models run text-only (`NO_TOOLS`). The REPL exists but the prover cannot drive it step by step. | `prompts.PROVE`, `lean.py:78`, `providers.py:31` |
| F4 | Keep-trying is a chain, not a tree: always the last branch's child, sequential, one Director's "stop" ends the loop. | `tree.iterate`, `server.iterate_loop` |
| F5 | Verified lemmas are not reused across branches or projects; `attempt_history` carries counts only. | `tree.py:282`, `discovery._program` |
| F6 | Subscription CLIs report `$0`, so "until the budget is spent" never triggers; `MAX_ITERATIONS=10` is the only brake. Budget is one-dimensional (USD). | `providers.py:11-13`, `tree.py:253` |
| F7 | API-key path is half built: LiteLLM only through `QUAERA_LITELLM_MODELS`, no UI, no detection, unknown price → refused (local models impossible), no JSON mode, no prompt cache, no backoff, one provider per family. The CLI path pays ~8k overhead tokens and a 2× output safety factor. | `gateway.py:34-36,137-184`, `cli.make_providers`, `providers.detect` |
| F8 | Mathlib search is a word grep; no semantic or type-based search. | `mcp_servers/lean_server.py:32` |
| F9 | No discriminating evaluation set yet (Phase 4 note); provider probe is "17×3". Changes cannot be measured. | `evals/README.md`, `providers.probe` |

## Phase overview

| Phase | Theme | Items | Exit criterion |
| --- | --- | --- | --- |
| 0 | Measurement first | M1 eval harness, M2 model scoreboard | Every later phase reports its effect on the same sets |
| 1 | Robust and provider-complete | R1 structured output layer, R2 budget-driven search, P1 provider registry, P2 role routing, P3 API features, P4 multi-dimensional budget, C1 completion cache | A weak model never ends a research through a parse failure; an API-key provider is usable from the UI; the search uses its budget share |
| 2 | Capacity multipliers | K1 lemma bank, K2 interactive prover, K3 model-free kill tests, K4 semantic Mathlib search, K5 best-of-N, K6 recursive decomposition | Putnam/synthetic solve rate rises at equal budget on the cheap profile |
| 3 | Search as a tree | T1 tree search, T2 proof-state nodes, T3 stop threshold, T4 dynamic budget, S1 cascade routing, S2 capability profiles, S3 committee decisions, S4 job queue | Keep-trying expands the most promising node, runs branches in parallel and stops only when the budget or the logical frame is exhausted |
| 4 | ML domain and operations | ML1 experiment grids, ML2 code reuse, ML3 remote runner, O1 observability | ML research gets the same branching and reuse as math |

Each item below has: goal · design · steps · files · tests and measurement · acceptance · risks. Items inside a phase are independent unless stated; "depends on" is explicit.

---

## Phase 0 · Measurement first

### M1 · Evaluation harness that discriminates

**Goal.** A fixed, versioned set of tasks and one command that reports solve rate, cost, calls and failure classes per model profile, so every later item is judged by numbers.

**Design.**
- Sets: Putnam sample (exists, `evals/putnam_run.py`), miniF2F (exists), a new **non-template synthetic set** of 30 statements at three difficulty tiers (automation-solvable, one-lemma, multi-lemma) generated once and frozen with a hidden split, and a **discovery mini-set** of 5 open-ended targets where only lemma progress is scored.
- One runner `evals/capacity_run.py --set X --profile cheap|balanced|best --provider F --budget B --seed S` writing `evals/results/capacity-<set>-<profile>-<date>.json` with: solved, verified-clean, model calls, tokens, USD, wall clock, failure classes (`invalid_json`, `output_cut`, `compile_error`, `budget`, `timeout`).
- Memory off (`memory=False`, as evals already do) and the lemma bank off (Phase 2) unless the run is explicitly a reuse test.

**Steps.**
1. Factor the shared loop out of `putnam_run.py`/`minif2f_run.py` into `evals/_harness.py` (task loading, budget, result schema, failure classification).
2. Generate the synthetic tiers with `evals/synth_math.py`; freeze; add `sets/capacity-synth.yaml` with the hidden split listed by id only.
3. Add the discovery mini-set `sets/capacity-discovery.yaml` scoring `verified lemmas`, `refuted lemmas`, `dead strategies`.
4. Write the failure classifier from `model.error`, `model.invalid_json`, `budget.blocked`, `lean.output` events.
5. CI job (manual trigger) that runs the cheap profile on the smallest set with the scripted provider for regression of the harness itself.

**Files.** `evals/_harness.py` (new), `evals/capacity_run.py` (new), `evals/sets/capacity-*.yaml` (new), `evals/putnam_run.py`, `evals/minif2f_run.py`, `evals/README.md`.

**Tests.** Harness unit tests with the scripted provider: classification, result schema, budget never exceeded.

**Acceptance.** One command, three profiles, reproducible JSON; the README table lists the baseline numbers for each set.

**Risks.** Contamination (miniF2F public): report it as a ceiling, as today. Keep the hidden split out of prompts.

### M2 · Model scoreboard and real probe

**Goal.** Know, per model, how reliable it is on the tasks the system actually gives it.

**Design.**
- Table `model_stats` in `~/.quaera/memory.db` (same SQLite, new table): model, purpose (from `purpose_of`), calls, invalid_json, output_cut, compile_ok, verified, mean cost, mean tokens, updated_at. Written from the gateway's `record` hook and from `lean.output` events (no extra calls).
- `probe()` gains a `--deep` mode: 5 tiny Lean statements (tier 1 of M1) compiled through the real checker; result = `proving_score` 0–5 and JSON validity. Shown on the settings page next to "Test".
- `quaera models` CLI prints the scoreboard.

**Steps.** Add the table and writer (`memory.py`), hook in `Gateway.call` and `_lean_artifact`; extend `probe`; settings UI column; CLI command.

**Files.** `memory.py`, `gateway.py`, `providers.py`, `server.py` (settings endpoints), `web/src/pages/Home.tsx` (Settings), `cli.py`.

**Acceptance.** After one research run the scoreboard shows per-model rates; the settings page shows a proving score after a deep test.

---

## Phase 1 · Robust and provider-complete

### R1 · Structured output layer

**Goal.** A parse failure never ends a research; weak models get every chance to produce valid objects.

**Design.** A single entry point `structured(role, system, prompt, schema, max_tokens)` in a new `gateway_schema.py`:
1. **Native schema** when the provider supports it: OpenAI `response_format={"type": "json_schema"}`, Anthropic forced tool-use with the schema as the tool input, Gemini `response_schema` (all through LiteLLM parameters; the provider declares `supports_schema`).
2. **Lenient parse** (`parse_json`, exists) + **field coercion**: numbers in strings, "7/10" → 7 (`score_of` exists in discovery), missing optional fields defaulted, lists where an object was expected wrapped.
3. **Repair pass**: on failure, a cheap-profile call "turn this text into JSON matching this schema" (any family; cost ~1/50 of the original). Max 2 repairs.
4. **Re-ask** with a shorter, stricter prompt, then the alternative family (exists).
5. Only when the budget share is gone: `StopResearch`. Otherwise the stage degrades: e.g. a failed Critic ranking returns the unranked list (already done for `_rank_hypotheses`); the same degradation is defined per call site.

Schemas: one JSON Schema per prompt (`schemas/v1/outputs/*.schema.json`) so the contract is explicit and testable; `ask_json` passes the schema name.

**Steps.**
1. Write the output schemas for every `ask_json` call site (hypothesis, rank, critic statement/result/experiment, landscape, target, ideate, cross_review, program, repair, revise, backtranslate, literature plan/summary, design ML, analyst, writer).
2. Implement `structured()` with the five levels; make `Orchestrator.ask_json` and `literature._json` use it.
3. Provider capability flags: `supports_schema`, `supports_reasoning`, `max_context` on each provider class.
4. Event `model.repaired` with the level that succeeded; feed M2.
5. Per-site degradation table: which calls may return a default instead of stopping.

**Files.** `gateway_schema.py` (new), `schemas/v1/outputs/` (new), `gateway.py`, `orchestrator.py`, `literature.py`, `discovery.py`, `tree.py` (REVISE), `tests/test_structured.py` (new).

**Tests.** Scripted provider returning: valid JSON, JSON in prose, numbers as strings, truncated JSON, a list instead of an object, garbage then valid on repair, garbage forever → degradation or stop depending on the site. Budget never exceeded across repairs.

**Measurement.** M1 failure class `invalid_json` on the cheap profile before/after.

**Acceptance.** On the cheap profile the `invalid_json` stop count is 0 over the synthetic set.

**Risks.** Forced tool-use changes model behaviour slightly (shorter prose); keep the free-text channel for proofs (Lean code is never schema-forced).

### R2 · Budget-driven search instead of fixed counts

**Goal.** The search runs until its budget share is used, not until a counter hits 2.

**Design.**
- `SearchBudget` already exists; make it the only brake. `prove_search(..., share)` loops: automation → whole attempts → sketch/lemma rounds, repeating the whole ladder with fresh prompts (different hints, shuffled Mathlib hits, the other family) while `spent < cap`. Counters become *minimums*, not maximums.
- Per-stage shares become settings with defaults: `prove 0.5`, `attack 0.7`, `ideation 0.1`, `synthesis 0.5`; the human can change them per project (UI "More options").
- Discovery rounds and lanes: `ROUNDS`/`LANES` stay as minimums; the attack continues while share remains and open lemmas exist.
- Every loop iteration records `search.iteration` {kind, spent, cap} so the UI shows "attempt 7 of budget".
- For subscription providers (cost 0) the brake is the call/time budget from P4.

**Steps.** Refactor `prove_search` into a ladder generator; add `share` plumbing from `stage_prove`/`_attack_lemma`/`stage_synthesis`; settings + UI; events.

**Files.** `prover.py`, `orchestrator.py`, `discovery.py`, `server.py`, `web/src/pages/Home.tsx`, `web/src/pages/Lab.tsx` (iteration counter), tests.

**Tests.** Scripted provider failing N times then succeeding: with budget for N+1 calls the proof is found; with less it stops with `SearchBudget`; cap never exceeded.

**Measurement.** M1 solve rate at equal budget, cheap and balanced profiles.

**Acceptance.** Solve rate on tier-2 synthetic tasks rises with budget monotonically on the cheap profile.

**Risks.** Longer runs; the UI already streams progress. Keep a wall-clock cap (P4).

### P1 · Provider registry (API keys)

**Goal.** Add any API-key provider from the settings page or a file, with price, limits and a test, without touching the repo.

**Design.**
- `~/.quaera/providers.json` (never in the repo; `.gitignore` already excludes `~/.quaera`): list of `{id, kind, family, apiKeyEnv | apiKeyRef, apiBase?, models: {cheap, balanced, best} | [names], price?: {model: [in, out]}, limits?: {rpm, concurrent}, enabled}`.
  Kinds: `anthropic`, `openai`, `google`, `openrouter`, `openai-compatible` (vLLM, Ollama, LM Studio: `apiBase` required), plus the existing CLI kinds.
- Keys: read from an env var name, or stored in `~/.quaera/secrets.env` (mode 600) written by the settings page; never logged, never in events, masked in the API.
- `detect()` returns registry entries with `ready` = key present + (optional) probe ok. `build_providers()` replaces `build_cli_providers()` and `make_providers()` and keeps the same return shape.
- `LiteLLMProvider` gains: `api_base`, per-model price override (unknown price + no override → still refused, the budget guarantee stays), `limits`.
- Settings UI: provider list with Add/Edit/Test/Deep test (M2), key field (write-only), model names per profile.
- `quaera providers add|list|test` CLI mirrors it.

**Steps.** Registry loader/validator (schema `schemas/v1/provider.schema.json`); provider factory; secrets file handling; `detect`/`probe` integration; settings endpoints (`GET/PUT /api/providers`, `POST /api/providers/{id}/test`); UI; CLI; docs (`docs/installation.md` providers section).

**Files.** `providers.py`, `gateway.py`, `cli.py`, `server.py`, `schemas/v1/provider.schema.json` (new), `web/src/pages/Home.tsx` (Settings), `docs/installation.md`.

**Tests.** Registry validation; price override; masked key in API responses; `mock_response` LiteLLM path end to end; a local OpenAI-compatible stub server in tests.

**Acceptance.** A user with only an OpenRouter key (or only Ollama) runs a full math research from the UI, with the budget cap enforced from the registry price.

**Risks.** Key leakage: keys never enter events, blobs or reports; add an audit test that greps the project store for the key value after a run.

### P2 · Role → model routing

**Goal.** Choose the model per role across providers (not per family), keep the cross-family rule.

**Design.**
- `~/.quaera/routing.json` or project meta `routing`: `{role: {provider, model, effort?}}` with fallbacks: role → profile default of the primary provider → any ready provider.
- Gateway keys providers by `provider id`, not family; `mustDifferFrom` compares `family` (unchanged semantics). Two providers of the same family are allowed (e.g. Claude CLI for the Engineer and Anthropic API for the Manager).
- Lanes: `lane_family` becomes `lane_provider`; discovery lane rotation iterates providers and prefers distinct families first.
- UI: a routing table on the settings page and a per-project override in "More options" (which providers this project may use; exists as `families`, becomes provider ids).

**Depends on** P1.

**Files.** `gateway.py` (`providers: dict[str, Provider]`, `_family_for`, `alternative`, `call`), `orchestrator.py` (`lane_family`), `discovery.py` (`families`, `other_family`), `cli.py`, `server.py`, UI.

**Tests.** Routing resolution and fallbacks; cross-family rule still enforced when two providers share a family; existing tests pass with the compatibility shim (`families` meta maps to provider ids).

**Acceptance.** `engineer` on provider A, `critic` on provider B of a different family, `literature` on a local model, all in one run; the event log records provider id, model and family per call.

### P3 · API-specific features: prompt caching, backoff, exact accounting

**Goal.** Same budget, more attempts; fewer failed calls.

**Design.**
- **Prompt caching:** mark the stable prefix (system prompt + the approved Lean file + landscape text) as cacheable. Anthropic: `cache_control` on the system block; OpenAI/Gemini: automatic prefix caching when the prefix is byte-identical, so prompts are built as `stable prefix + variable suffix`. The gateway's estimate uses cached-input price for the prefix after the first call per project.
- **Backoff:** on 429/5xx/timeouts exponential backoff with jitter (max 5 tries, bounded by the wall-clock budget), per-provider concurrency semaphore from `limits.concurrent`.
- **Exact accounting for API providers:** no `PROVIDER_OVERHEAD_TOKENS`, `OUTPUT_SAFETY=1.0` plus the provider's `max_tokens` as the hard output limit; the estimate therefore drops ~30–60% and the cap still holds (output is capped by the API).
- **Reasoning tokens:** read `usage.completion_tokens_details.reasoning_tokens` / Anthropic thinking usage into `output_tokens` so M2 and the calibration are right.

**Depends on** P1.

**Files.** `gateway.py` (estimate per provider kind, `Completion` fields), `prompts.py`/call sites (prefix/suffix split for the big prompts: PROVE, SKETCH, PROGRAM, IDEATE, CROSS_REVIEW), `providers.py`.

**Tests.** Estimate for API vs CLI; backoff with a stub raising 429 twice; prefix stability test (same prefix bytes across attempts).

**Measurement.** Cost per solved task on the API path vs the CLI path on the same model.

**Acceptance.** ≥ 30% lower cost per attempt on API providers at equal solve rate; no research stops on a transient 429.

### P4 · Multi-dimensional budget

**Goal.** "Until the budget is spent" means something on subscription CLIs and bounds wall-clock everywhere.

**Design.**
- Budget object `{usd, calls, tokens, seconds}`; any dimension missing = unlimited. Project meta `budget` replaces `budgetCapUsd` (kept as alias for old projects).
- Gateway reserves/accounts all four; `BudgetExceeded` names the dimension. Subscription providers count calls and tokens (they already report tokens).
- Keep-trying (`tree.iterate`) stops when any dimension of the line is exhausted; `MIN_BRANCH_USD` becomes `min_branch` per dimension.
- UI: budget field becomes "USD · max calls · max hours"; the budget card shows the tightest dimension.

**Files.** `gateway.py`, `store.py` (meta), `tree.py`, `orchestrator.py`, `cli.py` (`--budget`, `--max-calls`, `--max-hours`), `server.py`, UI (Home composer, Lab Budget card), report (`report.py` budget line).

**Tests.** Each dimension alone stops the run; old projects with `budgetCapUsd` still load; report shows all dimensions.

**Acceptance.** With a subscription-only setup, keep-trying ends on the call or time budget instead of the hard-coded 10 branches.

### C1 · Completion cache

**Goal.** Never pay twice for the same call (branches, resumes, evals, repairs).

**Design.** Content-addressed cache in `~/.quaera/cache.db`: key = sha256(provider id, model, effort, system, prompt, max_tokens, temperature); value = completion + usage. Hit = recorded as `model.call` with `cached: true`, cost 0, no reservation. Bypass flags: `temperature > 0` sampling for best-of-N (K5) uses a `sample` nonce in the key; evals can disable the cache (`QUAERA_CACHE=off`) to measure true cost.

**Files.** `gateway.py` (lookup before reserve), `memory.py` or new `cache.py`, `cli.py` (`quaera cache stats|clear`).

**Tests.** Second identical call hits; different effort misses; budget unaffected by hits.

**Acceptance.** A branch after `literature` makes zero paid literature calls.

---

## Phase 2 · Capacity multipliers

### K1 · Lemma bank (verified knowledge base)

**Goal.** Every Lean-verified statement becomes reusable across branches and projects; weak models stop re-proving what is already proved.

**Design.**
- Table `lemmas` in `memory.db`: id, project, name, statement (Lean, normalized by `lean.normalize`), natural-language statement, proof source sha (in the project's CAS, copied into a global `~/.quaera/cas/`), Mathlib rev, axioms, verified_at, reverified (Verifier), tags (from the question), embedding (fastembed, exists).
- Written on `lemma.status=verified` after the Verifier's clean recompile (`lemma.reverified`), on `proof` of the main theorem, and on verified `quaera_step_*` lemmas of a proof search.
- **Recall:** `bank.search(text | lean, k)` hybrid FTS + vector (reuse `LabMemory` ranking). Injected as hints in `FORMALIZE`, `PROVE`, `SKETCH`, `PROGRAM` ("Already verified in this lab, you may copy these with their proofs"), with the proofs included when the Mathlib rev matches, statement only otherwise.
- **Direct reuse in synthesis:** `stage_synthesis` and `_prove_lemma` prepend bank lemmas whose statements match the program's `dependsOn`; a lemma that compiles from the bank is recorded as `lemma.status=verified` with `source: bank` (no model call).
- **Honesty:** bank lemmas are evidence only after they recompile in this project (same gate as any proof); the report lists them as reused with their origin project. Eval runs use an isolated bank (`QUAERA_BANK=off` or a temp path), as memory does.
- Branches: the bank is the mechanism that makes a new branch cheaper than its parent (F5).

**Depends on** nothing (Phase 1 recommended first).

**Files.** `memory.py` (or `bank.py`), `discovery.py` (`_record`, `_program`, `stage_synthesis`, `stage_verification`), `orchestrator.py` (`stage_prove` hints, `_formalize`), `prover.py` (hints parameter already exists), `report.py` (reused lemmas section), `cli.py` (`quaera bank search|stats|rebuild`), UI (Lab: "reused from bank" badge; Memory page: lemma tab).

**Tests.** Verified lemma lands in the bank only after reverification; recall by text and by Lean; reuse compiles and is recorded without a model call; eval isolation.

**Measurement.** Discovery mini-set: verified lemma count per budget on second and third attempts of a line (should rise); synthetic tier-3 solve rate with a warm bank.

**Acceptance.** A branch that re-attacks a target reuses every previously verified lemma without a model call.

### K2 · Interactive prover (REPL-driven)

**Goal.** The Engineer works on one open goal at a time with goal states and suggestions, instead of regenerating whole files. This is the largest known lever for weaker models.

**Design.**
- Lean tools exposed to the prover loop (not to the model as free tools; the loop calls them and feeds results into the prompt): `goals(file, decl)` → list of unsolved goals after the current partial proof; `try_tactic(file, decl, goal_index, tactic)` → new goals or error; `suggest(file, decl, goal_index)` → `exact?`, `apply?`, `simp?`, `rw?` results with timeouts; `check(file)` (exists).
- New strategy `interactive` in `prover.STRATEGIES`: start from the sketch or a `sorry` proof, pick the first open goal, prompt the model with the goal state + hypotheses + suggestions + bank hits (K1) + Mathlib hits (K4), ask for **one to three tactic lines**, apply, loop; backtrack on error with the error attached; close the lemma when no goals remain. Depth/width bounded by the budget share (R2).
- Prompt `PROVE_STEP` (new): short, structured output `{ "tactics": ["..."], "why": "..." }` (R1 schema).
- Works for `quaera_step_*` lemmas, program lemmas (`quaera_L*`) and the main theorem; `prove_search` calls it as the last rung before giving up and, on the cheap profile, as the first rung after automation.
- REPL process reuse: `_Repl` is already persistent; add `pickle`-free state by re-sending the file prefix with `sorry` holes (simple and robust), cache the environment per file prefix.

**Depends on** R1, R2; benefits from K1, K4.

**Files.** `lean.py` (goal extraction via REPL `tactic` mode, suggestion runner), `mcp_servers/lean_server.py` (new tools), `prover.py` (`prove_interactive`), `prompts.py` (`PROVE_STEP`), `orchestrator.py`/`discovery.py` (strategy selection by profile), UI (step tree shows tactic steps; `experiment.tsx` ProofStep kinds `tactic`).

**Tests.** With real Lean (`-m lean`): goals of a `sorry` proof, applying a tactic reduces goals, `exact?` suggestion closes a trivial goal, a two-step lemma proved by the scripted provider returning tactics.

**Measurement.** Tier-2/3 synthetic and Putnam on the cheap profile: solve rate and calls per solve, interactive vs whole-file.

**Acceptance.** On the cheap profile the interactive strategy solves strictly more tier-2 tasks than whole-file at the same budget.

**Risks.** REPL cost per step (seconds); mitigate with environment caching and the automation rung first. Goal-state text can be long; truncate hypotheses by relevance (names appearing in the goal first).

### K3 · Model-free kill tests and richer automation

**Goal.** Refute false lemmas and close easy ones without any model call.

**Design.**
- `plausible` (Mathlib's random property testing) run on every new lemma statement with decidable instances: a found counterexample marks the lemma `refuted (plausible)` → repair path (`_repair`) without a model proof attempt; recorded with the counterexample.
- `decide` over small finite ranges when the statement is bounded; `Finset`/`Nat` range enumeration helper.
- Automation chain extended with `exact?`, `apply?` (bounded 20 s), `field_simp`, `ring_nf`, `push_cast`, `simp_all`, `nlinarith [sq_nonneg …]` patterns; ordered cheapest first; each with its own timeout.
- Python exploration (`EXPLORE_MATH`) keeps using the model for the script; add a model-free fallback: direct numeric evaluation of the statement when it parses as an inequality/equality over integers (sympy, in the sandbox).

**Files.** `prover.py` (`AUTOMATION` → ordered list with timeouts), `fidelity.py` (plausible file builder), `discovery.py` (`_attack_lemma` round 0), `lean/QuaeraLean.lean` (helper macros if needed), tests with real Lean.

**Measurement.** Fraction of program lemmas settled with zero model calls (M2 purpose `automation`).

**Acceptance.** False lemmas in the discovery mini-set are refuted before any model call in ≥ 50% of cases.

### K4 · Semantic and type-based Mathlib search

**Goal.** Find the right Mathlib declaration from natural language or a goal type.

**Design.**
- Index Mathlib once per rev: declaration name, type, docstring, module → FTS + fastembed vectors in `~/.quaera/mathlib-<rev>.db` (build time minutes, size hundreds of MB; built lazily on first use, `quaera mathlib index`).
- `mathlib_search(query)` becomes hybrid; `mathlib_search_type(goal)` calls a local Loogle-style matcher (pattern on the normalized type) with an optional online Loogle fallback when the user allows network (off by default; the local-first ADR).
- Hits injected into `FORMALIZE` ("declarations that match your hypothesis"), `PROVE`/`PROVE_STEP` (per goal), `PROGRAM`.

**Files.** `lean.py` (declaration dump via `lake env lean` script or Mathlib's `#print` tooling), `mcp_servers/lean_server.py`, `literature.py` (`lit.mathlib` uses the new search), `memory.py` (embedder reuse), `cli.py`.

**Tests.** Index build on a small module; search returns the known lemma for a canonical query; type search matches `∀ n, …` patterns.

**Measurement.** Formalization success at first compile; invalid-identifier errors in `lean.output` (count before/after).

**Acceptance.** Invalid-identifier compile errors drop by half on the synthetic set.

### K5 · Best-of-N sampling with the Lean selector

**Goal.** Turn parallel cheap samples into solutions; quality from breadth plus verification.

**Design.** `prove_search` rungs accept `n` samples per prompt (temperature 0.7–1.0 on API providers; CLIs vary the hint/lens instead), run in parallel through the gateway's reservation, compile all, keep the first verified; failed samples' distinct errors are merged into the next prompt (error diversity). `n` chosen from the budget share and M2's compile rate (low rate → larger n, smaller max_tokens).

**Depends on** R2, P3 (concurrency limits), C1 (sample nonce).

**Files.** `prover.py`, `gateway.py` (temperature/sample parameters), `providers.py` (CLI variants), UI lanes (`A`…`N`).

**Measurement.** Calls per solve and solve rate on the cheap profile for n ∈ {1, 4, 8}.

**Acceptance.** n=4 beats n=1 on tier-2 at equal USD on the cheap profile.

### K6 · Recursive decomposition

**Goal.** A failed lemma is sketched again at its own level instead of restarting the whole sketch.

**Design.** `_prove_lemma` on failure calls `prove_search` on the lemma's file with depth−1 (default depth 3), so a lemma gets automation → whole → interactive → its own sketch. Nodes and depth are recorded (`proof.search` steps gain `depth`, `parent`), the UI step tree nests them.

**Depends on** R2 (budget share), K2 optional.

**Files.** `prover.py`, `discovery.py` (`_attack_lemma` passes depth), `web/src/experiment.tsx`.

**Acceptance.** Tier-3 synthetic tasks (multi-lemma) solve rate rises at equal budget.

---

## Phase 3 · Search as a tree

### T1 · Tree search for keep-trying

**Goal.** Expand the most promising node, run several branches in parallel, never repeat.

**Design.**
- Nodes: every project in a research line (`tree.family` exists). **Progress score** per node = weighted {verified lemmas, strategies alive, critic score of the chosen strategy, exploration found no counterexample, fraction of stages passed, open objections (negative)}; the weights live in one table and are logged with each decision.
- Selection: best-first with a diversity term (penalize siblings of the same `change kind` and lens), beam width `k` (default 2, bounded by the provider concurrency and the budget), so two different directions run at once.
- Expansion: the Director proposes **several** changes (not one) per selected node, de-duplicated against the whole tree (`tried_hypotheses`, `_tried_strategies` exist; extend to approaches); each becomes a child with a budget slice (T4).
- Termination: the line's budget (P4) or T3's logical frame.
- Persistence: a `tree.decision` event on the root with scores, selected nodes and the proposals, so the Branches tab can show why a node was chosen.

**Depends on** P4, S4 (parallel runs), K1 (so children benefit from parents).

**Files.** `tree.py` (`score`, `select`, `iterate` → `search`), `server.py` (`iterate_loop` runs k children concurrently with separate approvers), `cli.py` (`quaera iterate --beam k`), UI Branches tab (`Tree.tsx`: score, selected marker, rationale).

**Tests.** Scripted line: the node with more verified lemmas is expanded first; siblings of the same kind are penalized; repeats rejected; budget split respected.

**Acceptance.** A keep-trying run with beam 2 produces two concurrent branches of different kinds and expands the better one next.

### T2 · Proof-state nodes (continue, do not restart)

**Goal.** A branch continues from the parent's open goals and verified lemmas rather than regenerating the program.

**Design.** Branch kinds gain `continue`: copies the parent's `program` state (lemmas with status, files, attempts) as the starting program; the Director's instruction says which open lemma to attack differently or which strategy to swap. Bank lemmas (K1) are marked verified immediately. Partial proofs with remaining `sorry` holes are kept as `open subgoals` on the lemma, so K2 resumes from them.

**Depends on** K1, K2, T1.

**Files.** `tree.py` (`CHANGE_STAGE["continue"] = "program"`), `discovery.py` (`stage_program` reads the inherited program; `stage_attack` resumes), `store.py` branch copy (state events are already copied), UI (lemma shows "inherited from <parent>").

**Acceptance.** A `continue` branch makes no program-generation call and starts attacking open lemmas within one minute.

### T3 · Stop threshold (the logical frame)

**Goal.** "Stop" is a justified, reviewed decision, not one model's fatigue.

**Design.** The Director may return `stop` only when all hold: (a) every discovery lens has been used at least once in the line **and** every proposed strategy is dead or exhausted its rounds; (b) a second family, given the same history, also returns `stop` (S3); (c) the remaining budget is below `min_branch`. Otherwise the decision is rewritten as "propose k new directions", with the rejection reason logged (`branch.stop_rejected`). The human can always stop (unchanged).

**Files.** `tree.py` (REVISE prompt gains the coverage table; `iterate` enforces), `prompts.py`.

**Tests.** Scripted Director saying stop with unused lenses → rejected and asked again; with coverage complete and second-family agreement → accepted.

**Acceptance.** No line stops with a lens never tried while budget remains.

### T4 · Dynamic budget allocation

**Goal.** Budget follows promise.

**Design.** Each child gets `slice = remaining × softmax(score)/Σ` bounded by `[min_branch, max_share]`; unspent slices return to the pool; the human can top up the line's budget from the UI while it runs (new `POST /api/projects/{root}/budget` with approval record), and toggle keep-trying on a running project (`keepTrying` meta is read at the end of each run; make it live).

**Depends on** T1, P4.

**Files.** `tree.py`, `server.py`, UI Budget card (top up, keep-trying switch).

**Acceptance.** Two siblings with scores 0.8 and 0.2 receive proportionally different slices; a top-up extends a running line.

### S1 · Cascade routing (escalate only on failure)

**Goal.** Cheap first, expensive only when needed, per step.

**Design.** Routing (P2) gets `ladder: [provider/model, …]` per role; the gateway starts at rung 0 and escalates when the step fails (invalid output after R1, compile failure after n samples, critic blocking). De-escalation for routine steps (literature summary, back-translation). The rung is recorded per call; M2 shows cost saved.

**Depends on** P2, R1, M2.

**Files.** `gateway.py` (`call(..., rung)`), `orchestrator.py` (`llm` retries climb the ladder instead of only switching family), `prover.py` (rung per attempt), settings UI (ladder editor).

**Measurement.** Cost per solve, balanced ladder vs static best profile.

**Acceptance.** ≥ 30% lower cost per solve at equal solve rate on the mixed ladder.

### S2 · Capability profiles that adapt prompts and search

**Goal.** The system adapts to the model it has.

**Design.** From M2 stats compute per model: `json_reliability`, `compile_rate`, `context_budget`, `proving_score`. Rules: low json_reliability → always native schema or repair-first; low compile_rate → interactive (K2) first, larger n (K5), smaller tasks (K6 depth up); small context → shorter landscape text, fewer hints; high proving_score → whole-file first. Prompt variants: a few-shot block (two short verified proofs from the bank K1) added for models below a threshold. All rules in one table (`capability_rules.py`) and logged per call (`model.adapted`).

**Depends on** M2, R1, K1, K2, K5, K6.

**Acceptance.** The same research on a cheap local model and on a frontier model takes different documented paths, both ending with Lean-verified output or an honest "not found".

### S3 · Committee decisions

**Goal.** No single model's judgment ends or misdirects a line.

**Design.** Critical decisions run on two families and are merged: Director stop/continue (T3), strategy ranking (average of two cross-reviews when families ≥ 2; exists partly), Critic `blocking` severity (a second family must confirm `blocking`, else it becomes `high` and the loop continues with the objection recorded). Cost bounded by the profile (cheap second opinion).

**Files.** `discovery.py` (`stage_cross_review` two reviewers), `orchestrator.py` (`stage_statement_review`, `stage_result_review`), `tree.py`, `prompts.py`.

**Acceptance.** A `blocking` objection from one family alone never stops a line.

### S4 · Job queue and scheduler

**Goal.** Parallel branches, parallel lemma attacks, per-provider limits, one place to see load.

**Design.** `scheduler.py`: a thread pool per provider (size from `limits.concurrent`), a global queue of jobs {priority, project, role, fn}; the orchestrator's `parallel()` and `prove_search` submit to it; `stage_attack` attacks open lemmas concurrently (Lean checks serialized per REPL, one REPL per worker with a memory guard from ADR 0010). The server's `Runner` uses it for concurrent projects/branches. UI: active jobs in the Active agent card.

**Depends on** P3 limits.

**Files.** `scheduler.py` (new), `orchestrator.py`, `prover.py`, `discovery.py`, `server.py`, `lean.py` (REPL pool), UI.

**Tests.** Limits respected under load; budget reservation holds with 8 concurrent calls; REPL pool memory guard.

**Acceptance.** Three open lemmas are attacked concurrently on two providers without exceeding either provider's limit or the cap.

---

## Phase 4 · ML domain and operations

### ML1 · Experiment grids as branches
Director proposals for ML become structured grids (hyperparameters, ablations, seeds); each cell is a branch node scored by the preregistered metric delta (`metricDelta` exists). Same tree search (T1), same stop rules.

### ML2 · Code and result reuse
A results table (ML counterpart of the lemma bank): verified runs (commit, seed, metrics, data hash) searchable by hypothesis; branches reuse `experiment.py` with a diff instead of rewriting it; the Verifier reproduces only what changed.

### ML3 · Remote runner
Optional GPU runner over SSH or a container API with the same sandbox contract (no network, read-only data, recorded commit); approval card shows the runner and cost.

### O1 · Observability
A per-run timeline of cost, calls, tokens, cache hits, rungs and failure classes (from M2), exportable with the evidence package; alerts when a provider's error rate spikes.

---

## Cross-cutting rules

- **Honesty gate unchanged:** nothing reused, cached or sampled counts until Lean (or the Verifier's rerun) accepts it in the current project.
- **Budget guarantee unchanged:** every new call path reserves before calling; caches and bank reuse cost zero and never reserve.
- **Secrets:** keys only in `~/.quaera/secrets.env` or env vars; a test greps every project store and report after a run for key values.
- **Evals isolated:** memory, bank and cache off (or temp paths) unless the eval measures reuse.
- **Events first:** every new decision (repair level, rung, node selection, stop rejection, bank reuse, cache hit) is an event, so Replay and the audit see it.
- **Compatibility:** old projects (`budgetCapUsd`, `families`) keep loading; new meta keys are additive.

## Measurement plan (per phase)

| Metric | Set | Reported by |
| --- | --- | --- |
| Solve rate, verified-clean | Putnam sample, synthetic tiers, miniF2F (ceiling) | M1 |
| Verified lemmas per USD / per call | discovery mini-set | M1 |
| Failure classes (invalid_json, output_cut, budget, timeout) | all | M1 + M2 |
| Cost per solve, API vs CLI, ladder vs static | synthetic tier 2 | P3, S1 |
| Zero-model-call settlements | discovery mini-set | K3 |
| Invalid-identifier compile errors | synthetic | K4 |
| Keep-trying: nodes expanded, repeats rejected, stop reasons | discovery mini-set with keep-trying | T1, T3 |

Baselines are taken in Phase 0 on the cheap, balanced and best profiles and stay frozen; every item's PR links its before/after numbers.

## Suggested order of work

1. M1, M2 (one week).
2. R1 → R2 → P1 → P2 → P3 → P4 → C1 (two weeks; R1 and P1 can proceed in parallel).
3. K1 → K2 (K3, K4 alongside) → K5 → K6 (three to four weeks).
4. S4 → T1 → T3 → T4 → T2 → S1 → S2 → S3 (three to four weeks).
5. ML1–ML3, O1 as the alpha participants' questions demand.

Each item ships behind a setting (env or registry flag) with the default off until its measurement is in, then the default flips on.
