# 0004 · SQLite + content-addressed store + event log

**Status:** Accepted · **Date:** 2026-10-03

## Context
Research objects are revised, and a published revision cannot be changed. We need a portable store that needs no installation.

## Decision
- Research objects are kept in SQLite as JSON conforming to the `schemas/v1` schemas; each revision is a separate row.
- Every change is written to an append-only event log. The current state can be rebuilt from the events; branching (`branchOf`) is a fork from an event point.
- Large files (logs, checkpoints, Lean outputs) are kept in a file store addressed by sha256; objects reference them with `artifact`.
- Metrics are also written to Parquet for analysis.

## Options
| Option | Pro | Con |
| --- | --- | --- |
| **SQLite + CAS (chosen)** | No setup, single file, easy to back up | Not suited to multi-user writes (not needed in v1.0) |
| PostgreSQL | Scales | Local setup burden; could be considered for the v1.1 shared network server |

## Consequences
- The rules in `tools/validate.py` are also enforced in the store layer (e.g. a full run record is rejected before the preregistration is locked).
