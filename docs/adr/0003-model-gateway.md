# 0003 · LiteLLM as the model gateway

**Status:** Accepted · **Date:** 2026-10-03

## Context
Users hold keys for different providers. The budget cap, token counting, per-role model choice and the cross-model rule must be enforced in a single place.

## Decision
All model calls go through a thin gateway layer built on LiteLLM. The gateway enforces: the project budget cap (warning at 80%, stop at 100%), a cost record for every call, per-role model assignment, the `mustDifferFrom` rule in the agent definition (Critic and Verifier in a different model family where possible) and rejection of tool calls outside the allowlist.

## Options
| Option | Pro | Con |
| --- | --- | --- |
| **LiteLLM (chosen)** | Many providers, cost tracking, open source | External dependency; version changes must be watched |
| Our own adapters | Full control | Maintenance load for every provider |

## Consequences
- If the user has only one provider key, the cross-model rule cannot be applied; the report and the AI label state this as `crossModelReview: false`.
- The gateway is one of the two places where permissions are enforced in code (the other is the orchestrator).
