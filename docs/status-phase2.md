# Phase 2 · Full agent team and ML experiments — status

**Date:** 2026-10-04 · **Source:** roadmap, "Phase plan → Phase 2" + three items carried over from Phase 1

All nine roles of the agent team are working, and the ML experiment loop runs end to end, inside the sandbox, with real models. On five synthetic discovery tasks whose correct answer is fixed by the design of the data generator, the team reached the correct answer in all five, and every result was reproduced exactly with the same seed.

## Tasks

| Task | Status | Where |
| --- | --- | --- |
| The remaining four roles: Experiment designer, Analyst, Verifier, Writer | Done: Designer, Analyst, Writer are LLM; Verifier is deterministic (rerun) | `src/quaera/ml_loop.py`, `prompts.py` |
| Experiment manifest: config, seed, commit, environment, hardware, metrics | Done | git commit, seed, metrics on every run; `run` objects |
| GPU adapters: local GPU | Done: the GPU is exposed to the sandbox only for the permitted role (tested with a GTX 1050 Ti) | `mcp_servers/sandbox_server.py` |
| GPU adapters: the user's cloud account (Modal, RunPod) | **Not done**: requires an account; no untested code was written | — |
| Novelty check and preregistration lock | Done | the preregistration is locked after plan review |
| Cost estimator, pilot run, approval points | Done | `gateway.py`, pilot is the first stage |
| Autonomy levels (three modes) | Done: `manual`, `under` (below an amount), `cap` (up to the budget cap); publishing stays with a human in every mode | `orchestrator.py`, CLI `--autonomy` |
| Critic gate, rerun in a clean environment, debate rounds, isolated roles | Done | plan review and blind result review, at most 3 rounds |
| Cross-model support | Done (code + tests); live with a single provider | `LiteLLMProvider`, `QUAERA_LITELLM_MODELS` |
| Citation verification (DOI, arXiv) | Done | `literature.py` |
| **Carried over:** literature search | Done | arXiv AND queries, OpenAlex, `unknown` verdict, grounding label |
| **Carried over:** Critic test v0.2 | Done | `evals/sets/critic-test-v0.2.yaml` |
| **Carried over:** contamination-free proof measurement | Done | `evals/synth_math.py` |
| **Added:** resource limits | Done | [ADR 0010](adr/0010-resource-limits.md), `tools/limited.sh` |

## Exit gate

| Criterion (roadmap) | Result |
| --- | --- |
| The team reaches the correct result on most of the "known findings" set | **Measured with the synthetic discovery set: 10/10 on 10 balanced tasks** (see note). The "known findings" set to be chosen by the advisor is still pending (from Phase 0). |
| The Critic test, including resistance to persuasion, passes the threshold | **Passed** (v0.2 hidden test: detection 25/25, category 100%, false alarm 0/12, persuasion resistance 8/8) |
| The Verifier can reproduce results exactly with the same seed | **Passed**: `reproduced: yes` on 15 of 15 task runs (difference ≤ 1e-9) |

**Note: why a synthetic set?** You left the selection of the roadmap's "known findings" set to the advisor. To measure the gate instead, five tasks were built whose correct answer is fixed by the design of the data generator. Because the data is regenerated with a hidden seed on every run, the answer cannot come from the model's memory. The correct answer of each task was confirmed with an independent reference solver on three different data seeds (`evals/synthetic/reference.py`).

## Measurements

| Measurement | Result | Cost | File |
| --- | --- | --- | --- |
| Synthetic discovery set v1 (5 tasks, unbalanced) | 5/5 correct and reproduced; 0 rule violations | $1.60 | `evals/results/synthetic-20261004T133417.json` |
| **Synthetic discovery set v2 (10 tasks, balanced: 5 yes / 5 no)** | **10/10 correct and reproduced; 5/5 per class**; a baseline that says "no" to everything gets 5/10 | $3.30 | `evals/results/synthetic-20261004T140221.json` |
| Critic objection, live fault injection (inverted conclusion / overgeneralization / control) | first run failed (overgeneralization dropped) → fix → **passed**: objection 2/2, 0 false objections on the control | $1.73 | `evals/results/critic-live-*.json` |
| Literature / novelty set | 12/12 correct verdicts; open problems were never called "already done"; verified source in 10/12 | $0.17 | `evals/results/literature-*.json` |
| Critic test v0.2 (45 hidden cases) | detection 100%, category 100%, false alarm 0%, persuasion resistance 100% (majority baseline: category 30%, false alarm 100%) | $0.86 | `evals/results/critic-v0.2-*.json` |
| New proof set (QuaeraLabs-Synth-Math, 30 problems, 10 templates) | 29/29 solved and verified in a clean environment (pass@2 100%); 1 problem excluded due to a generator bug | $0.24 | `evals/results/synth-math-*.json` |
| Proof set — hard level (30 problems, 10 new templates: residue classes, congruence with no root, divisibility by induction, closed form, Cauchy-Schwarz, Bezout, unsolvable Diophantine …) | 30/30, verified in a clean environment (pass@2 100%; 27 on the first attempt) | $0.33 | `evals/results/synth-math-hard-2027-*.json` |
| Proof set — olympiad level (21 problems, 7 templates: d ∣ a^(2n+1)+b^(n+2), 510 ∣ n¹⁷−n, power residues, AM-GM/Nesbitt-type inequalities, Fibonacci identities, IMO 1959-type gcd, absence of rational roots) | 21/21, verified in a clean environment (pass@2 100%; 20 on the first attempt) | $0.36 | `evals/results/synth-math-olympiad-2028-*.json` |

**How to read it:**
- **The synthetic set was balanced.** In v1 the answer to 4 of the 5 questions was "no"; a team that says "no" to everything would also get 4/5. v2 added four new tasks whose answer is "yes" (an MLP beats LR on a nonlinear boundary, dropping the informative column lowers accuracy, the majority class exceeds 80% on imbalanced data, an MLP gives lower MSE on a sine target) and one "no" task (scaling does not change a decision tree). The distribution shift in syn-03 was strengthened; its answer is now the same on all three data seeds. The gate requires at least 8/10 and at least 4/5 per class; the result is 10/10 and 5/5–5/5. The tasks are still small; a 20+ task version and real datasets were left to Phase 4.
- **Ceiling effect in the Critic test.** The cases were written by an author from the same model family (Claude) and the Critic caught all of them. This shows that it does not raise false alarms and is not fooled by persuasive defenses, but it cannot tell a good Critic from a perfect one. v0.3 should be done with external, human-written cases.
- **The proof set got harder, but is still not discriminating.** The hard (30/30) and olympiad (21/21) levels were solved too. The proofs were reviewed by hand: no cheating, all were verified in a clean Lean process with only the standard axioms. For example, 510 ∣ n¹⁷−n was proved by case analysis on residues for each prime, a/b+b/c+c/a ≥ 3 with ordering cases and `nlinarith`, and q² = 48 by reduction to the irrationality of √3. Conclusion: **template-generated problems have a single type of proof strategy** and do not reach the difficulty ceiling for this model. Measuring the team's limit requires non-template problems: questions from competitions held after the model's knowledge cutoff, or unpublished multi-step problems written for this purpose by mathematicians. This was moved to Phase 4 together with the advisor set.
- **The first proof set was too easy.** Since the problems are freshly sampled there is no memorization issue, but the templates are simple (linear system, modular exponent, sum formula…). Read together with the 95% on miniF2F: simple and medium-level proofs are not a problem; measuring the team's limit needs olympiad-level and multi-step templates. The generator bug (a negative number being read as subtraction in the form `g -3`) was fixed.
- **The Critic objection was tested live.** In natural runs the Critic never opened a high-severity objection. So `evals/critic_live_check.py` was written to test the objection loop. A real completed ML project (syn-01, correct answer "no") was branched right after analysis and a deliberate error was inserted into the Analyst's conclusion. The fault was written to the event log as `fault.injected`. Then critique, verification and the report ran with real models. **The first run failed the gate:** the inverted conclusion was caught, but although the Critic noticed the added "the finding holds for all datasets and models", it rated its severity "medium". Because the code only opened objections for high/blocking findings, the finding was silently dropped and the overgeneralization went into the report as a conclusion sentence. **Fix:** every flaw found in result review is opened as an objection and waits for a response from the Analyst (`test_medium_result_flaw_is_not_dropped`; fails on the unfixed code). The Critic prompt did not change. **The second run passed:** on the inversion, blocking objection → Analyst accepted → the relation flipped to "contradicts"; on the overgeneralization, medium objection → Analyst withdrew the sentence; no objection on the control arm. The final answer was correct ("no") on all three arms. Cost $0.88 + $0.85. Limit: a single source project and two fault types; a broader fault-injection set in Phase 4.

## Bugs found and fixed in Phase 2

- **The budget estimate was not an upper bound.** A real Opus call exceeded the worst-case estimate ($0.086 > $0.076). Two reasons: the `claude` CLI adds ~5k cache-write tokens to every call, and the output limit is not strictly enforced (2500 requested, 2797 produced). The price table had not been verified either (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10). Fix: verified prices, provider overhead, a 2x margin for output, and a calibration that grows itself when an overrun is seen. No estimate overrun was seen in runs after the fix. The total cap was never exceeded in any run.
- **The hypothesis prompt was math-specific.** No hypothesis could be generated for an ML question. A separate prompt was added for ML.
- **Rounding at approval time.** When the cost was rounded to the nearest value it exceeded the remaining budget by a small amount and `cap` mode rejected the experiment. It is now rounded down and computed against the remaining budget at approval time.
- **Truncated JSON crashed the research.** Every call that expects JSON now asks for a correction once; if that fails, the research stops in a controlled way and writes a report.
- **Meaningless seeds.** In a deterministic method the seed changed nothing; the Critic caught this. The Designer and Engineer now tie the seed to data resampling.
- **Python could not be found in the sandbox.** The virtual environment's interpreter was a symlink; the real path is now mounted too.
- **Medium-severity result flaws were dropped.** Found during live fault injection; see above.
- **The computer slowed down and the session closed.** The Lean REPL and ML runs ran in parallel and memory filled up. Resource limits and sequential execution ([ADR 0010](adr/0010-resource-limits.md)); evaluation results are now written to disk after every task.

## Test status

| Test | Count | Command |
| --- | --- | --- |
| Fast tests (contracts, orchestrator, ML loop, providers, sandbox limits) | 47 | `tools/limited.sh 3G 200% uv run pytest` |
| Real Lean + Mathlib | 10 | `tools/limited.sh 8G 300% uv run pytest -m lean` |

## Known limitations

- **No cloud GPU.** The Modal/RunPod adapters were not written because they require an account; work runs on the local CPU/GPU.
- **Cross-model only in code.** This machine has no key for a second provider; reports say "cross-model: no".
- **The synthetic tasks are simple.** The tasks are small experiments that take minutes on a single GPU; success on real research questions must be measured separately (Phase 4, closed alpha).
- **The advisor's "known findings" set** is still pending.
