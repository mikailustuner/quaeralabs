# Evaluation sets

| Set | File | Status | What it measures |
| --- | --- | --- | --- |
| Critic test | `sets/critic-test.yaml` | v0.1 · 30 flawed + 6 clean cases | Catching planted flaws without raising false alarms on clean reports |
| Citation verification | `sets/citations.yaml` | v0.1 · 8 real + 6 fabricated IDs | Never accepting a fabricated source (zero tolerance) |
| Known findings | `sets/known-findings.yaml` | Selection protocol ready, cases with the domain advisor | The agent team rediscovering published findings without knowing the result |
| miniF2F (Lean 4) | `sets/minif2f.yaml` | Source defined, version to be pinned in Phase 1 | Formal proof success rate |

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
