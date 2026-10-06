# Contributing guide

Thank you for contributing to QuaeraLabs. This project is governed by a [code of conduct](CODE_OF_CONDUCT.md).

## Setup

```bash
uv sync
uv run pytest                       # all tests
uv run python tools/validate.py     # schemas, examples, agent and skill definitions
uv run python evals/run_evals.py    # evaluation sets (needs network access)
```

## Where can you contribute?

| Area | Where | Note |
| --- | --- | --- |
| Skill | `skills/*.yaml` | The easiest area open to the community in v1.0. A skill declares its own permissions with `requiredPermissions`; a skill that exceeds an agent's permissions cannot be assigned to it. |
| Evaluation case | `evals/sets/` | A new flawed or clean case for the Critic test, a new real/fabricated identifier for the source set. |
| Example research | `examples/*.json` | If you find a research situation the schema cannot represent, open an issue with an example. |
| Architecture decision | `docs/adr/` | A new ADR for a new decision; an accepted ADR is not changed. |
| New agent role | — | Opens in v1.1. |

## Rules

- Every change must pass `uv run pytest` and `uv run python tools/validate.py`.
- A backward-incompatible schema change requires a new major version (see [ADR 0008](docs/adr/0008-schema-versioning.md)).
- If you add a new validation rule, also add a negative test that violates it (`tests/test_validate.py`).
- Keep commit messages short and descriptive; open an issue first for large changes.

## License

Your contributions are licensed under the [Apache License 2.0](LICENSE). Published research reports and schemas are under CC BY 4.0.
