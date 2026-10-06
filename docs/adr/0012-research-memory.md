# 0012 · Research memory: a local full-text index, guidance not evidence

**Status:** Proposed (Phase 4) · **Date:** 2026-10-04 · **Related:** [0001](0001-local-first.md), [0004](0004-data-store.md)

## Context
One of the approved additions is "research memory": the team should learn from earlier research on the same machine. For example, it should not propose a refuted hypothesis again, and it should build on a verified result. There are three risks:
(1) information from memory could be used as evidence in a new project,
(2) evaluation runs could "remember" the earlier result of the same task and pass,
(3) evaluation projects with deliberately injected faults carry wrong information.

## Decision
- **Store:** `~/.quaera/memory.db`, SQLite FTS5 (unicode61, diacritic-insensitive), one row per project: question, accepted hypotheses and their status, result summaries, verification, open objections. No embedding model or external service; local-first and dependency-free.
- **When it is written:** automatically after the report stage; in bulk with `quaera memory rebuild`. Projects without a written report and projects carrying a `fault.injected` event are not indexed.
- **Search:** a crude stem for Turkish suffixes (first 5 letters of long words, FTS5 prefix query); the question title weighs 4 times the body; repeated runs of the same question are one record.
- **Use:** given only to the Hypothesis agent, at most 4 records, with the note "for guidance only, not evidence, cannot be cited". What was given is recorded with a `memory.recalled` event and is visible in the UI.
- **Evidence leakage is structurally prevented:** the Writer can cite only the project's own objects; project identifiers from memory are not in that set, so they cannot enter the report.
- **Evaluations run without memory:** `build(..., memory=False)` (synthetic set, fault injection, failure cases).

## Verification
`tests/test_memory.py`: indexing and search, exclusion of unfinished and fault-injected projects, context given and recorded in the second project, and the report not citing the first project. UI test: memory search (`web/e2e/walkthrough.mjs`).

## Consequences
- No semantic search: it can miss the same question asked with different words. If this proves insufficient in the alpha, a local embedding model will be evaluated.
- Memory lives on a single machine. Shared memory across projects comes with the publishing network (v1.1+).

## Update 2026-10-05: hybrid recall
- Each project also gets a multilingual sentence embedding (`paraphrase-multilingual-MiniLM-L12-v2` via fastembed, local ONNX, ~220 MB, downloaded once). Stored in `memory_vec` in the same `memory.db`; recomputed automatically when missing or when the model changes.
- Recall merges the bm25 and cosine rankings with reciprocal rank fusion (k=60). Vector-only hits need cosine ≥ 0.55, so unrelated questions still recall nothing.
- Reason: the repo moved to English while existing projects are Turkish; bm25 cannot match across languages. On 24 real projects, English questions now find the Turkish project (e.g. the `n³ - n` question: bm25 none, hybrid correct).
- Optional: `memory` extra (installed by `install.sh`). Without it, or with `QUAERA_MEMORY_EMBED=off`, recall is bm25 only, as before. Tests use a fake embedder.
