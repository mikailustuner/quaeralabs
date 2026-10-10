# Evaluation sets

| Set | File | Status | What it measures |
| --- | --- | --- | --- |
| Critic test | `sets/critic-test.yaml` | v0.1 · 30 flawed + 6 clean cases | Catching planted flaws without raising false alarms on clean reports |
| Citation verification | `sets/citations.yaml` | v0.1 · 8 real + 6 fabricated IDs | Never accepting a fabricated source (zero tolerance) |
| Known findings | `sets/known-findings.yaml` | Selection protocol ready, cases with the domain advisor | The agent team rediscovering published findings without knowing the result |
| miniF2F (Lean 4) | `sets/minif2f.yaml` | Source defined, version to be pinned in Phase 1 | Formal proof success rate |

## Capacity measurements (docs/capacity-plan.md)

| Set | Source | What it measures |
| --- | --- | --- |
| `capacity-synth` | `sets/capacity-synth.yaml` · 30 statements, three tiers, frozen hidden split (12) | Proof search at three difficulty levels; the hidden ids must never reach prompts or rules |
| `minif2f-valid` | miniF2F valid split without `mathd_*` | Olympiad-style problems with headroom for small models |
| `putnam` | `data/putnam-sample-7.jsonl` | Ceiling probe |
| `discovery` | `sets/capacity-discovery.yaml` · 5 targets | Discovery progress: verified / refuted lemmas, dead strategies, synthesis |

```bash
uv run python -u evals/capacity_run.py --set capacity-synth --profile cheap --budget 4      # both arms, same per-task cap
uv run python -u evals/capacity_run.py --set minif2f-valid --n 12 --provider openrouter --budget 6
uv run python -u evals/capacity_run.py --set putnam --arms after --provider local --max-calls 8 --budget 1   # $0 provider
uv run python -u evals/capacity_run.py --set discovery --n 2 --budget 4
```

Recorded results (Claude, both arms on the same tasks and the same per-task cap):

| Run | Model | Before (previous search) | After (Phase 1–3 search) | Spent |
| --- | --- | --- | --- | --- |
| `results/capacity-compare-20261010T131502.json` · Putnam, 8 | Haiku | 0/8 | 0/8 | $2.74 |
| `results/capacity-compare-20261010T141258.json` · miniF2F valid, 12 | Haiku | 7/12 | 7/12 (same tasks) | $3.33 |
| `results/capacity-compare-20261010T165008.json` · miniF2F valid, 8 | Sonnet | 6/8 | 6/8 (same tasks) | $4.01 |

The new search spends the budget differently (up to ~3× more attempts per dollar, one task closed without a model call) but has not
solved more tasks on these sets; the stronger model is what moved the solve rate.

## Running

```bash
uv run python evals/run_evals.py                 # all
uv run python evals/run_evals.py --only citations
```

Results are written to `results/baseline-<date>.json`. If a fabricated source is accepted, the command exits with 1.

## Baselines

Current values and their interpretation: [../docs/status-phase0.md](../docs/status-phase0.md#baselines).

- The **majority-class baseline** objects to every report with the most frequent category. It is a case-independent lower bound; an LLM Critic must beat it on both catch rate and false-alarm rate.
- In Phase 0 a rule-based Critic was also tried, but because its rules were written by reading the cases it overfit the set (30/30) and was removed. For this reason the cases in this set must never be given as input to agent prompts or rules during development; the next version should set aside a separate hidden test split.
