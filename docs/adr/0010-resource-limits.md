# 0010 · Every heavy job runs in a memory- and CPU-limited scope

**Status:** Proposed (Phase 2) · **Date:** 2026-10-04 · **Related:** [0002](0002-sandbox.md), [0009](0009-sandbox-bwrap-and-lean-repl.md)

## Context
During Phase 2 the Lean REPL (~6 GB), ML experiments and model calls were run at the same time. The development machine with 16 GB of RAM fell into swap, the desktop slowed down and the session closed. This is unacceptable for a local-first product running on the user's machine: not even a single faulty script written by the agent should be able to lock up the user's computer.

## Decision
- **Every command run in the sandbox** (the agent's Python script, a Lean build, the Lean REPL) runs in its own systemd user scope, with a memory cap, swap off and a CPU quota.
  - Defaults: Python experiment 2 GB / 200% CPU (`QUAERA_SANDBOX_MEM`, `QUAERA_SANDBOX_CPU`); Lean 7 GB / 200% CPU (`QUAERA_LEAN_MEM`, `QUAERA_LEAN_CPU`).
  - If a limit is exceeded, only that command is killed; the research loop records it as a failed run.
- **Evaluation runs** use `tools/limited.sh <memory> <cpu> command`: the whole process tree in a single scope, at low priority (`nice 10`).
- **Heavy jobs run sequentially.** Lean and ML evaluations are not started at the same time.
- On systems without a systemd user session the limits cannot be applied; `quaera doctor` should show this as a warning (Phase 4).

## Verification
- `tests/test_sandbox_limits.py`: a script trying to allocate 600 MB under a 200 MB limit is killed, a 50 MB script runs.
- The real Lean tests passed under an 8 GB / 300% limit (10/10); the duration went from 20 s to ~6.5 min (CPU quota and the cache warming up again).

## Consequences
- Safety and user experience were preferred over speed; the limits can be raised by the user.
