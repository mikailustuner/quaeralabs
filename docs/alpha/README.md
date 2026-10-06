# Closed alpha program (Phase 4)

**Goal:** Verify with real researchers, on their real questions, whether the tool is useful and never fabricates anything. Duration 6 weeks, 20–30 participants.

This folder contains everything needed to run the program. Work that must be done by humans (finding participants, interviews, expert review) is defined here. The software side is ready in the repo.

| Document | Purpose |
| --- | --- |
| [invitation.md](invitation.md) | Invitation text to send to participants |
| [getting-started.md](getting-started.md) | The participant's first 30 minutes: from installation to the first report |
| [consent-and-privacy.md](consent-and-privacy.md) | Which data stays where, and what is shared under which consent |
| [weekly-interview.md](weekly-interview.md) | 20-minute weekly feedback interview |
| [failure-review.md](failure-review.md) | Weekly "agent failure review": turning cases into the eval set |
| [metrics.md](metrics.md) | Measurement definitions and thresholds for the exit gate |

## Schedule

| Week | Participant | Team |
| --- | --- | --- |
| 0 | Invitation, consent form, installation | 30–40 invitations from the candidate list ([alpha-candidates.csv](../research/alpha-candidates.csv)); same-day response to installation problems |
| 1 | First research (own question), first interview | Classify installation blockers; collect `quaera doctor` outputs |
| 2–5 | Use in own work, weekly interview, "Report a problem" | Weekly failure review; a new release every week; add cases to `evals/sets/failures.yaml` |
| 6 | Final interview, selection of candidate work for expert review | `quaera audit` (all shared projects), gate measurement, Phase 4 report |

## Ready on the software side
- **Installation:** `./install.sh` (Linux, WSL2) and `quaera doctor` ([installation](../installation.md))
- **Report a problem:** the button on the project page or `quaera triage add`. Cases stay on the participant's machine; `quaera triage export` produces a package with personal information redacted.
- **Failure scan:** `quaera triage scan` flags halts, crashes, non-reproducible results, open objections and rejected sources across all projects.
- **Integrity audit:** `quaera audit [--lean]` flags fabricated or unverified quotes, broken citations, fake verification and altered reports.
- **Research memory:** `quaera memory search`, "Lab memory" in the UI. A participant's earlier research feeds into their new research as guidance.
- **Failure case set:** `evals/failures_run.py` reruns past failures on every release.
