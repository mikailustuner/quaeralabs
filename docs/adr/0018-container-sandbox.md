# 0018 · A container sandbox backend next to bubblewrap, behind one contract

**Status:** Proposed (improvement plan A2) · **Date:** 2026-10-20 · **Related:** [0002](0002-sandbox.md), [0009](0009-sandbox-bwrap-and-lean-repl.md), [0010](0010-resource-limits.md)

## Context
ADR 0009 made bubblewrap the only backend and deferred the container backend. Two problems remained:
1. Ubuntu 24.04+ blocks unprivileged user namespaces through AppArmor, so bubblewrap fails until an administrator changes a sysctl.
2. macOS has no bubblewrap at all. Without a second backend the product cannot run there.

The sandbox was also built in three places by hand (generic commands, ML experiments, the Lean REPL). Each place had its own copy of the rules.

## Decision
- `quaera.sandbox` has two backends with the same interface. `argv()` builds the command line plus an optional stop command. `run()` executes it with a timeout.
  - `BwrapSandbox`: unchanged rules (ADR 0009, 0010).
  - `ContainerSandbox`: Docker or Podman. Each command runs in a `--rm` container with network `none`, a read-only root, a private `/tmp`, all capabilities dropped and `no-new-privileges`. It also gets a pids limit, a memory limit with swap off, a CPU quota, a CPU-time ulimit and the host UID. On Linux the host's system directories are bound read-only at the same paths, as in bwrap, so the ML environment and the Lean toolchain run unchanged.
- `make_sandbox()` chooses the backend with `QUAERA_SANDBOX=bwrap|container|auto`. `auto` uses bwrap where it can create namespaces and mount `/proc`, otherwise a container engine. It never falls back to running without a sandbox.
- ML experiments (`sandbox_exec`), one-shot Lean builds and the Lean REPL all go through the factory.
- The contract is a test: `tests/test_sandbox_contract.py` runs the same checks on every available backend. The checks cover exit code and output, network off, home directory invisible, only the working directory writable, binds to another destination, binds below `/tmp`, cleared environment, timeout (the container is removed too), process count and CPU time.

## Addendum (plan A2, second part, 2026-10-20): images
- Two images carry their own tools: `quaera-lean` (Lean, the Mathlib build cache, the REPL; `docker/lean.Dockerfile`) and `quaera-ml` (Python 3.12, numpy, scipy, scikit-learn, CPU torch; `docker/ml.Dockerfile`). Each describes itself in `/etc/quaera-image.json`: where `lean`, the REPL and the libraries are, or the ML `PATH`.
- An image runs through `ContainerSandbox` with host binds off: no host directory is visible except the fresh working directory (and the read-only data directory for ML). The same limits apply.
- `QUAERA_LEAN` and `QUAERA_ML` (`host`, `image`, `auto`) choose between the host environment and the image; `auto` prefers the host environment on Linux when it is installed, otherwise the image if it is present. On macOS it is always the image.
- Lean in the image is called directly (`lean`, `repl`), never through `lake`, so nothing needs to write inside the image.
- The Mathlib sources for the search index are copied out of the image once, to `~/.quaera/cache/mathlib-src-<rev>`.
- `.github/workflows/images.yml` builds both images for linux/amd64 and linux/arm64 and publishes them to ghcr.io.
- Not covered: GPU jobs in the ML image (they need the host environment or a remote runner) and `quaera prove` on a user's project (it needs Lean on the host, because the project's own toolchain must match).

## Consequences
- Killing a container's client does not stop the container. Both backends therefore return a stop command (`rm -f <name>`), which the timeout path and the REPL's `close()` run.
- Found while writing the contract: bwrap mounted the private `/tmp` after the read-only binds, so any directory below `/tmp` was hidden inside the sandbox. This affected a data directory, a test project and anything else bound there. The order is fixed, and a contract test covers it.
- The bwrap probe now also mounts `/proc`. Inside an unprivileged container, namespaces can work while mounting `/proc` fails, and doctor used to report such a sandbox as working.
