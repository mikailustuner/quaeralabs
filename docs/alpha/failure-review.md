# Weekly agent failure review

The working practice from the roadmap: "Every case where the agents got something wrong is reviewed and added to the eval set." The meeting is held once a week and lasts 45 minutes. Attendees: the agent engineer, the research protocol lead and, if needed, a domain advisor.

## Inputs
1. Case packages from participants (`quaera triage export`). Opened on a team machine.
2. A scan of the team's own runs: `quaera triage scan`.
3. Integrity audit: `quaera audit` (with `--lean` once a week).
4. A run of the failure case set against the current release: `tools/limited.sh 8G 300% uv run python -u evals/failures_run.py`.

## Why the automatic scan is not enough
`triage scan` only finds **visible** failures: halts, crashes, non-reproducible results, open objections, rejected sources. The most dangerous kind of failure, however, is silent. For example, in the Goldbach run in Phase 1 a restricted result was reported as a full answer; that run trips none of the scan's flags. This is why participants' "Report a problem" cases and the interviews are more valuable than the automatic scan.

## For each case
| Step | Question | Output |
| --- | --- | --- |
| 1. Classify | Which role made the mistake? Was it the model, the prompt, the orchestrator code, a tool, or user expectations? | label |
| 2. Severity | **Critical:** fabricated source, fake verification, a wrong result appearing "verified". **High:** wrong result, but the evidence is shown. **Medium:** crash or halt. **Low:** usability. | severity |
| 3. Reproduce | Can the case be written in `failures.yaml` format with an `expect`? | case record |
| 4. Root cause | If it is a code bug, a failing test is written first. | test + fix |
| 5. Close | After the fix, does `failures_run.py` pass the case? | green run |

If there is a critical case, no new feature ships that week. The gate criterion is "zero fabricated quotes or fake verifications".

## Adding to failures.yaml
- The participant must have allowed, in the consent form, adding it to the open eval set.
- If the question text contains personal or unpublished research information, an **equivalent question** that produces the same failure is written; the original question is not added.
- For ML cases, the participant's data is never added. The failure is reproduced with a synthetic task (`syntheticTask`) or a public dataset.

## Real cases from before Phase 4
| Case | Where it was found | Status |
| --- | --- | --- |
| Goldbach: a restricted proof was reported as a full answer | Phase 1 live run | fixed; `fail-goldbach-scope` |
| No hypothesis could be generated for an ML question | Phase 2 first synthetic run | fixed; `fail-ml-no-hypothesis` |
| The Critic's "medium" severity overgeneralization finding was dropped | Phase 2 live fault injection | fixed; `evals/critic_live_check.py` |
| The literature summary carried unverified sources into the report | Phase 4 integrity audit | fixed; `test_literature_summary_cannot_smuggle_unverified_citations` |
| A real arXiv DOI was counted as "fake" (only Crossref was queried) | Phase 4 integrity audit | fixed; DataCite and arXiv are queried too |
