# 0007 · Evidence package in RO-Crate 1.1 format

**Status:** Accepted · **Date:** 2026-10-03

## Context
Exported research must be openable with other tools, uploadable to archives (e.g. Zenodo) with a DOI, and form the basis of the shared network in v1.1.

## Decision
The evidence package is an RO-Crate 1.1 directory:

```
package/
├── ro-crate-metadata.json   # RO-Crate standard: files, authors, license
├── quaera-manifest.json     # schemas/v1/evidence-package.schema.json
├── bundle.json              # schemas/v1/bundle.schema.json (all research objects)
├── report.md / report.pdf
└── artifacts/               # code, logs, Lean outputs (named by sha256)
```

**Mapping:** every object in `bundle.json` appears in the RO-Crate as a `CreativeWork`, with its identifier (such as `H-0001`) in the `identifier` field. Agents appear as `SoftwareApplication`, the approving human as `Person`, and the AI label in the root dataset's `description` field and in `quaera-manifest.json`.

**Signature:** `quaera-manifest.json` is signed with ed25519. The signature covers the AI label and all file digests; a package whose label has been stripped or whose files have been changed fails verification.

## Consequences
- Example manifest: `examples/packages/ml-warmup.manifest.json` (the signature value is an example).
- The signing key is generated and stored on the user's device.
