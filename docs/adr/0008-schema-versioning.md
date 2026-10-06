# 0008 · Schema identifiers and versioning

**Status:** Accepted · **Date:** 2026-10-03

## Decision
- Schemas are written in JSON Schema 2020-12 and carry the identifier `urn:quaeralabs:v1:<name>`. Once the project domain is settled, the `$id`s can move to https addresses; the URNs remain as aliases.
- The major version (`v1`, `v2`) increases only on a backward-incompatible change and is the directory name (`schemas/v1`). Backward-compatible additions are made in the same directory and written to the CHANGELOG.
- Object identifiers are permanent: `Q-0001`, `H-0001`, `PRE-`, `E-`, `RUN-`, `RES-`, `CR-`, `EL-`, `VER-`, `ART-`, `MSG-`. An identifier is never reused.
- A published revision cannot be changed; a change increments `revision`. Changes to a preregistration are added as `amendments` and, if made after the results were seen, are shown as a warning in the report.
