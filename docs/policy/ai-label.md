# QuaeraLabs AI label

**Version:** 1.0 · Approved at the Phase 0 gate on 2026-10-03.

Every exported report and evidence package carries the label below. The label appears both inside the report (right below the title) and in the signed `quaera-manifest.json`.

## Text

> This research was produced by a **QuaeraLabs AI agent team** and published with the approval of {approver} on {date}. Models used: {role: model list}. Cross-model review: {yes/no}. QuaeraLabs version: {version}.

## Rules

- The label is a report element that cannot be removed; the report template does not compile without it.
- Because the code is open source, deleting the label from a PDF cannot be prevented technically. That is why the label is also in the signed manifest; a package whose label has been removed or altered fails verification and is not accepted into the shared network in v1.1.
- Schema rule: `aiLabel.text` must contain the string "QuaeraLabs" (`schemas/v1/evidence-package.schema.json`).
- Contributions made by humans (giving direction, approvals, edits) are listed separately in the "Contributions" section of the report.
