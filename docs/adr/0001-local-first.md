# 0001 · v1.0 runs local-first

**Status:** Accepted · **Date:** 2026-10-03

## Context
QuaeraLabs is a non-profit project. It has no budget to carry token and GPU costs centrally. Keeping users' API keys on a server would also create trust and security risks.

## Decision
The whole research engine (`quaera runner`) runs on the user's computer. Model, literature and GPU calls are made with the user's own accounts. QuaeraLabs operates no server for v1.0. Data goes to the shared network only when the user deliberately publishes it (v1.1).

## Options
| Option | Pro | Con |
| --- | --- | --- |
| **Local-first (chosen)** | Zero server cost; keys stay on the device; privacy | Harder setup; the user's machine must stay on |
| Hosted SaaS | Easy start | Token/GPU cost is ours; key storage risk |
| One-click install on the user's cloud | Runs while the machine is off | Extra maintenance load; enlarges v1.0 scope |

## Consequences
- The installation experience becomes critical: one-command install and `quaera doctor` are mandatory in Phase 4.
- The orchestrator must be durable so long research can continue even if the computer shuts down (see 0005).
- Open question: will the option that runs on the user's own cloud make it into v1.0? (Roadmap, "Pending decisions")
