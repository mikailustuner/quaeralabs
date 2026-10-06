# 0009 · bubblewrap is the default sandbox on Linux; Lean checks via the REPL

**Status:** Proposed (Phase 1 implementation) · **Date:** 2026-10-04 · **Related:** [0002](0002-sandbox.md)

## Context
ADR 0002 proposed rootless Podman/Docker + gVisor. Two real constraints surfaced in Phase 1:
1. On the development machine Docker runs as a root daemon (not rootless) and gVisor is not installed. Expecting every user to install these raises the installation threshold.
2. `import Mathlib` maps ~5 GB of compiled files on every one-shot build. On a machine with a spinning disk (HDD) and a cold cache, the first build took minutes; with a warm cache a one-shot build takes ~6 s and a check in the REPL ~1 s. Because the proof loop needs dozens of checks, a persistent process is still required.

## Decision
- **The default sandbox backend on Linux is bubblewrap (`bwrap`)**: it runs unprivileged and needs no daemon. Defaults: all namespaces separate (`--unshare-all`), network off, capabilities dropped, home directory and user files invisible; only system libraries, the Lean toolchain and Mathlib are read-only, the working directory is writable; CPU time and process count limited with `prlimit`, plus a wall-clock timeout.
- **Lean checks go through a persistent Lean REPL (leanprover-community/repl).** The REPL starts inside the sandbox and loads Mathlib once; every check runs independently from the same base environment. On timeout the process is killed and restarted.
- **The Verifier does not use the REPL.** To guarantee a "clean environment", every verification is a one-shot `lean` build in a new sandbox; it is slow but fully independent of REPL state.
- The container (Docker/Podman) backend is added in Phase 2 for ML experiments; macOS and Windows support is addressed then as well.

## Consequences
- Memory: the REPL process holds ~4–5 GB of RAM with Mathlib. `quaera doctor` should warn on machines with less than 8 GB of memory (Phase 4, installation experience).
- Measurement (development machine, warm cache): the REPL loads Mathlib in ~5 s, a check takes ~1 s; a one-shot clean build ~6 s. On a cold disk the first load can take minutes.
- Implementation note: REPL output is read on a separate thread; when `select()` and `readline()` were used together, the reply stayed in Python's internal buffer and the reader hung (a bug caught in Phase 1).
- `bwrap` has no memory limit (`prlimit --as` breaks Lean's memory mapping); the memory limit comes with the container backend.
