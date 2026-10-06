# Architecture decision records (ADR)

Every significant architectural decision is kept here as a record. Format: context, decision, options, consequences.
An accepted record is never changed; if a decision changes, a new record states that it supersedes the old one.

| No | Decision | Status |
| --- | --- | --- |
| [0001](0001-local-first.md) | v1.0 runs local-first | Accepted (2026-10-03) |
| [0002](0002-sandbox.md) | Rootless container + gVisor sandbox | Accepted (2026-10-03) |
| [0003](0003-model-gateway.md) | LiteLLM as the model gateway | Accepted (2026-10-03) |
| [0004](0004-data-store.md) | SQLite + content-addressed store + event log | Accepted (2026-10-03) |
| [0005](0005-orchestration.md) | Our own state machine | Accepted (2026-10-03) |
| [0006](0006-mcp.md) | Tools via the Model Context Protocol | Accepted (2026-10-03) |
| [0007](0007-ro-crate.md) | Evidence package as RO-Crate 1.1 | Accepted (2026-10-03) |
| [0008](0008-schema-versioning.md) | Schema identifiers and versioning | Accepted (2026-10-03) |
| [0009](0009-sandbox-bwrap-and-lean-repl.md) | bwrap sandbox on Linux; Lean checks via the REPL | Proposed (Phase 1) |
| [0010](0010-resource-limits.md) | Heavy jobs run sequentially in a memory/CPU-limited scope | Proposed (Phase 2) |
| [0011](0011-local-web-ui.md) | Local web UI; screen state is derived from the event log | Proposed (Phase 3) |
| [0012](0012-research-memory.md) | Research memory: local FTS5, guidance not evidence | Proposed (Phase 4) |
| [0013](0013-research-tree.md) | Research tree: branches with changes and iterative research | Proposed |
| [0014](0014-agent-intelligence.md) | Agent intelligence: exploration, diverse hypotheses, faithfulness, proof search, thinking budget | Proposed |
| [0015](0015-ui-v2-parallel-agents.md) | UI v2, parallel agents and live visibility | Proposed |
| [0016](0016-ui-v3-manager-contextual-memory.md) | UI v3: English UI, Project manager, contextual memory, math parser | Proposed |
| [0017](0017-discovery-mode-multi-model.md) | Discovery mode and multiple models (Codex CLI, OpenCode CLI, cross-check) | Proposed |

All records were accepted at the Phase 0 gate on 2026-10-03.
