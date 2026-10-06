# Consent and privacy (alpha)

This text has not been reviewed by legal counsel. Before it is sent to participants, it must be read by a lawyer once the association/foundation is established.

## Default: nothing leaves your machine
| Data | Where | Who sees it |
| --- | --- | --- |
| Your questions, hypotheses, experiment code, results, reports | `~/.quaera/projects/` (your machine) | only you |
| Research memory | `~/.quaera/memory.db` | only you |
| Problem reports | `~/.quaera/triage/` | only you |
| Your data files | the directory you choose; read-only during experiments | only you |
| Model calls | your model provider account | the provider's own terms apply |
| Signing key | `~/.quaera/signing-key` (0600) | only you |

QuaeraLabs does not collect telemetry. The telemetry policy is one of the open decisions on the roadmap.

## Shared by your choice
| What | How | Consent |
| --- | --- | --- |
| Interview notes | weekly interview; recorded only if you allow it | verbal consent at the start of each interview; recordings are deleted after 90 days |
| Failure cases | `quaera triage export` package; user name and file paths are redacted automatically, **the question text is not redacted** | you are asked to review the package contents before sending it |
| A whole project | evidence package (RO-Crate zip) | separate, explicit written consent; for expert review |
| Adding a case to the open eval set (repo) | the team adds the case to `evals/sets/failures.yaml` | separate written consent; data files are not added |

## Participant consent form (summary)
- [ ] I have read the purpose and duration of the alpha.
- [ ] I know the tool is an alpha release and its results can be wrong. I will not publish results without checking them independently.
- [ ] I will keep the QuaeraLabs AI label on every output I publish ([AI label](../policy/ai-label.md), [usage policy](../policy/usage-policy.md)).
- [ ] I allow / do not allow interviews to be recorded.
- [ ] I allow / do not allow the failure cases I share to be added anonymously to the open eval set.
- [ ] I know I can leave the alpha at any time and ask for the data I shared to be deleted.
