# Phase 4 · Closed alpha — status

**Date:** 2026-10-04 · **Source:** roadmap, "Phase plan → Phase 4"

The software side of the closed alpha is ready:
- research memory,
- tools that turn failure cases into an eval set, and an in-app "Report a problem",
- the integrity audit,
- one-command installation,
- alpha program documents.

Current state of the automatically measurable part of the gate: across all projects, **fabricated citations 0, fake verifications 0**. The gate's other two criteria are measured with people and have not been measured yet because the alpha has not started: reuse by 20–30 researchers in their own work, and an external expert assessment.

## Tasks

| Task (roadmap) | Status | Where |
| --- | --- | --- |
| 20–30 researchers | **Human work, pending.** Invitation text, getting-started guide, consent form ready | [docs/alpha/](alpha/README.md) |
| Weekly feedback interviews and failure triage | **Process ready, interviews pending.** Interview guide, weekly failure review, `quaera triage scan` | [weekly-interview](alpha/weekly-interview.md), [failure-review](alpha/failure-review.md) |
| Reviewing failed studies and adding them to eval sets | Done (tool + first cases): "Report a problem" / `quaera triage add` → `export` (personal data is redacted) → `evals/sets/failures.yaml` → `evals/failures_run.py` | `src/quaera/triage.py` |
| Research memory | Done: local FTS5; given to the Hypothesis agent as guidance, cannot be evidence; off in evaluations | `src/quaera/memory.py`, [ADR 0012](adr/0012-research-memory.md) |
| Installation: one command, Linux, WSL2, macOS | **Partial.** Linux done (tested on clean Debian 13 and Ubuntu 24.04). WSL2 is supported but untested. **No macOS** (needs a sandbox backend) | `install.sh`, `tools/install_test.sh`, [installation](installation.md) |
| **Added:** integrity audit | Done: fabricated or unverified citation, broken reference, fake verification (including Lean recompilation), modified report | `src/quaera/audit.py`, `evals/audit_run.py` |
| **Carried over:** broader fault injection | Done: 4 fault types + control | `evals/critic_live_check.py` |

## Exit gate

| Criterion | Status |
| --- | --- |
| Most alpha users use the tool a second time in their own work | **Not measured: the alpha has not started.** Proposed threshold: ≥60% of active participants start a second study from their own work within 3 weeks of their first study ([metrics](alpha/metrics.md); awaiting team approval) |
| Zero cases of fabricated citations or fake verification | **Pre-alpha: passed.** 0 fabricated across 50 sources in 24 projects; 0 fake across 21 verifications; 4 Lean proofs recompiled in a clean process. Alpha projects will go through the same audit with the participants' permission |
| At least one study is found meaningful by an external expert | **Pending: human work.** Assessment form and selection rule ready |

## Measurements

| Measurement | Result | Cost | File |
| --- | --- | --- | --- |
| Integrity audit (all projects, online source check + Lean) | fabricated citations 0/50 · fake verifications 0/21 · Lean 4/4 · modified reports 0 · unverified citations 3 (in 2 old reports; see below) | $0 | `evals/results/audit-2026-10-04.json` |
| Failure case set (2 real cases, with current code) | 2/2 passed: Goldbach is now "restricted scope"; a hypothesis is generated for the ML question and the answer is correct | $1.18 | `evals/results/failures-*.json` |
| Fault injection, new types (preregistration violation, fabricated number) + control | objection 2/2, 0 false objections on the control. The fabricated number was found at "medium" severity; without the fix at the end of Phase 2 it would have been dropped | $0.96 | `evals/results/critic-live-*.json` |
| UI end to end (fake models) | 31/31 steps; axe WCAG 2.1 AA 0 violations on 11 screens | $0 | `web/e2e/walkthrough.mjs` |
| Installation in a clean container | Debian 13 and Ubuntu 24.04: stops with a clear message on missing packages, installs as a normal user, UI and API return 200, 39 tests pass | $0 | `tools/install_test.sh` |

## Bugs found and fixed in Phase 4
- **Unverified sources leaked into the report.** Citation verification was applied only to the Literature agent's "cited" list. DOIs in the agent's free-text summary went into the report unverified. The audit found this in two old reports: 3 sources, all real publications, not fabricated. Fix: unverified arXiv IDs and DOIs in the summary are removed and recorded.
- **A real arXiv DOI was counted as "fake".** The verifier only queried Crossref; arXiv's DOIs (10.48550/arXiv.…) are registered with DataCite. Fix: arXiv DOIs are checked against arXiv, others against Crossref and then DataCite.
- **The audit would have counted a timeout as fake verification.** Under memory pressure Lean paged to disk and waited without using CPU; the CPU time limit does not trigger in that case. Fix: a timeout counts as "inconclusive"; the audit runs each proof in a separate process with a wall-clock limit.
- **The UI did not build on Ubuntu 24.04.** The system Node is 18. The install script now downloads a portable Node 22 from nodejs.org, verified with SHASUMS256.
- **15 GB of local environments were tracked in git.** `ml-env/` and `lean/.lake/` were added to `.gitignore`.

## Limits and open work
- **Human work:** finding participants (the candidate list is empty), interviews, the external expert, Phase 3's 5-person usability test, the code of conduct contact address, having a lawyer read the consent text, team approval of the reuse threshold.
- **macOS:** the sandbox backend must be developed and tested on a Mac; moved to Phase 5.
- **WSL2 and arm64 are untested.** The container test runs with AppArmor disabled; the AppArmor namespace restriction on Ubuntu desktop should be tried on a real machine.
- **Memory is not semantic:** it can miss the same question asked in different words.
- **Discriminating evaluation sets are still missing:** non-template proof problems, a 20+ task synthetic set and real datasets, the advisor's "known findings" set. These will be fed by participants' questions and cases during the alpha.

## Test status
| Test | Count | Command |
| --- | --- | --- |
| Fast tests | 99 | `tools/limited.sh 3G 200% uv run pytest` |
| Real Lean + Mathlib | 12 | `tools/limited.sh 8G 300% uv run pytest -m lean` |
| UI end to end + accessibility | 53 steps (light + dark theme axe) | `tests/e2e_server.py` + `cd web && npm run test:e2e` |
| Clean installation | 2 distributions | `tools/install_test.sh` |
