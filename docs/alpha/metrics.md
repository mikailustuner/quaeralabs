# Phase 4 exit gate: measurement definitions

The roadmap defines the gate as: *"Most alpha users use the tool a second time in their own work. Zero cases of fabricated quotes or fake verification. At least one study is found meaningful by an outside expert."* It says the reuse threshold is to be set at the start of Phase 4. The definitions below are a **proposal** and must be approved by the team before the alpha starts. If the gate is not passed, the criterion is not relaxed; the phase is extended.

## 1. Reuse
- **Active participant:** a participant who completes installation and takes at least one research run all the way to a report.
- **Use in own work:** a question from the participant's own research agenda. Example or trial questions do not count. Determined by the participant's statement in the interview.
- **Proposed threshold:** **at least 60%** of active participants start a **second** research run from their own work within 3 weeks of their first. This was chosen higher than "most" because response bias (the tendency to speak positively in interviews) is expected.
- **Source:** weekly interview notes. For participants who allow it, the number of projects and their dates.

## 2. Fabricated quotes and fake verification: zero
- **Fabricated quote:** a source cited in the report that is not found on arXiv, Crossref or DataCite (`quaera audit` → `fabricated_citations`).
- **Unverified quote:** a real source cited in the report that did not go through the verification pipeline (`unverified_citations`). Not counted as fabricated, but a rule violation; the code now prevents it and it must also be 0 in the alpha.
- **Fake verification:** a result said to be "reproduced" but not backed by a successful verification run or by metrics identical to the original run within ≤1e-9. In math, a proof that cannot be recompiled in a clean Lean process (`fake_verifications`).
- **Scope:** the team's own runs, `evals/` runs and the evidence packages participants share with permission. Projects participants do not share cannot be measured; we will state this clearly in the results.
- **Additional source:** cases reported by participants (those with "critical" severity).

## 3. Outside expert review
- At the end of the alpha, with participants' permission, **at most 3 studies** are selected. The review is done blind by an outside domain expert unrelated to the study; the tool name is not hidden, the participant name is.
- The expert receives the evidence package (report, code or proof, logs) and fills in this form:

| Question | Scale |
| --- | --- |
| Is the question a meaningful one in its field? | 1–5 |
| Is the result supported by the evidence? | 1–5 |
| Does the report contain an overstated claim? | yes/no + which sentence |
| What would you say if you received this work from a student? | free text |
| Is it meaningful as a contribution to a publication, preprint or course? | yes/no + why |

- **Pass condition:** at least one study is found "meaningful" (yes on the last question) and that study has no overstated claims.

## Current status (2026-10-04, before the alpha)
| Criterion | Value | Basis |
| --- | --- | --- |
| Reuse | not measurable: the alpha has not started | — |
| Fabricated quotes | **0** / 50 sources (24 projects) | `evals/results/audit-2026-10-04.json` |
| Unverified quotes | 3: in 2 old reports, all real publications; the source of the leak was closed | same |
| Fake verification | **0** / 21 verifications; 4 Lean proofs recompiled in a clean process | same |
| Outside expert | not done | — |
