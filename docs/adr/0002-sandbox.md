# 0002 · Rootless container + gVisor sandbox

**Status:** Accepted · **Date:** 2026-10-03

## Context
Agents write and run code; that code must not harm the user's files or network. An agent producing malicious code through prompt injection is a realistic threat.

## Decision
Code runs in a rootless Podman (or, failing that, rootless Docker) container. On Linux, gVisor (`runsc`) is used for extra kernel isolation. Default limits: read-only system, writes only to the project working directory, CPU/memory/disk/time limits, network off; network is opened only through the `networkAllowlist` in the agent definition. The Verifier uses a clean container built from scratch from the recorded environment every time.

## Options
| Option | Pro | Con |
| --- | --- | --- |
| **Rootless container + gVisor (chosen)** | Local, free, mature | Needs a VM layer on macOS/Windows |
| Firecracker microVM | Strong isolation | Linux/KVM only; heavy setup |
| Hosted sandbox (e.g. E2B) | No setup | Paid; code goes to a third party. Remains an optional adapter using the user's own account |

## Consequences
- macOS and Windows need Podman machine / WSL; `quaera doctor` checks for this.
- GPU access is given to the container only for runs of agents with the `gpuSpend: within_approved_plan` permission.
