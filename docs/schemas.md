# Schema guide (v1)

All schemas live under `schemas/v1/` and are written in JSON Schema 2020-12. Identity and versioning rules: [ADR 0008](adr/0008-schema-versioning.md).

## Research objects

| Object | Prefix | Schema | Produced by | Key rules |
| --- | --- | --- | --- | --- |
| Question | `Q-` | `question` | Human | Root object; domain `math` or `ml` |
| Hypothesis | `H-` | `hypothesis` | Hypothesis agent | A falsifiability note is required; every status other than draft needs human approval |
| Preregistration | `PRE-` | `preregistration` | Experiment designer | sha256 digest of the locked fields; later changes go through `amendments` and stay visible |
| Experiment | `E-` | `experiment` | Experiment designer | An approved experiment needs a preregistration, a novelty check and human approval; in mathematics the Lean version is required |
| Run | `RUN-` | `run` | Engineer / Verifier | A full run only after the preregistration and a successful pilot; only the Verifier starts a verification run |
| Result | `RES-` | `result` | Analyst | Limitations are required; Lean `verified` only with sorry-free compiler output |
| Critique | `CR-` | `critique` | Critic | An open objection cannot be closed without a reply |
| Evidence link | `EL-` | `evidence-link` | Analyst | supports / contradicts / inconclusive / methodological_only + L0–L4 independence |
| Verification | `VER-` | `verification` | Verifier | A cross-model claim must be consistent with the model family |
| Artifact | `ART-` | `artifact` | Various | Pinned with sha256 when possible |
| Message | `MSG-` | `message` | Agents, human | Typed; questions and objections need a reply; at most 3 rounds; approval requests go only to the human |

## Other schemas

| Schema | Use |
| --- | --- |
| `common` | Shared definitions: identity, actor, approval, cost, revision, branching (`branchOf`) |
| `agent` | `agents/*.yaml` — permissions are enforced in code; `publish` and `raiseBudgetCap` are always `false` |
| `skill` | `skills/*.yaml` — tools and the permissions they need |
| `bundle` | Export carrying all objects of a research project |
| `evidence-package` | The signed `quaera-manifest.json` in the RO-Crate evidence package; the AI label must contain "QuaeraLabs" |

## Marking a hypothesis "supported" or "refuted"

`tools/validate.py` enforces this gate: the hypothesis must have an evidence link in the matching direction (`supports` / `contradicts`), the result behind that evidence must have been reproduced by the Verifier with `reproduced: yes`, and there must be no open objection on the hypothesis or its linked results.
