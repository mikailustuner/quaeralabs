# QuaeraLabs

An open-source, non-profit AI research team. The user enters a research question; a team of nine agents carries it from literature to report, while the human steers and approves costly or critical steps. First domains: **Mathematics** (proofs verified with Lean 4) and **AI/ML research**.

> **Status:** Phase 4 · Preparing the closed alpha. The software side is ready: research memory, "Report a problem" → failure case set, integrity audit (zero fabricated citations and zero fake verifications across all projects so far) and one-command install (Linux, WSL2; no macOS yet). The part run with participants is pending: [alpha program](docs/alpha/README.md). Details: [Phase 4](docs/status-phase4.md) · [Phase 3](docs/status-phase3.md) · [Phase 2](docs/status-phase2.md) · [Phase 1](docs/status-phase1.md) · [Phase 0](docs/status-phase0.md).

## Installation

```bash
./install.sh            # Linux or Windows + WSL2; --with-ml, --with-lean, --all
quaera doctor
```
Details and platform status: [docs/installation.md](docs/installation.md). macOS is not supported yet.

## Usage

```bash
uv sync
uv run quaera doctor                       # bwrap, claude CLI, Lean + Mathlib, REPL
uv run quaera ask "For every natural number n, is n³ - n divisible by 6?" --budget 3 --auto-approve-under 3
uv run quaera ask "Does a linear model exceed 75%?" --domain ml --data-dir ./data --scope "X: (n,2), y: 0/1" --budget 2 --autonomy cap
uv run quaera status <project>             # stages, hypotheses, cost
uv run quaera resume <project>             # resume an interrupted run where it stopped
uv run quaera branch <project> --at 42 --name new-branch   # branch from an event point
uv run quaera verify <project>             # recompile the proof in a clean environment
uv run quaera serve                        # web UI: http://127.0.0.1:8765
uv run quaera memory search "linear classifier"  # research memory (past projects on this machine)
uv run quaera audit [--lean]               # integrity audit: fabricated citations, fake verification
uv run quaera triage scan                  # failure signals; triage add/export: failure cases
uv run quaera providers add|list|key|test  # API-key and local model providers (Anthropic, OpenAI, Gemini, OpenRouter, Ollama…)
uv run quaera route engineer --ladder local@cheap anthropic@best   # role routing and escalation ladders
uv run quaera iterate <project> --total-budget 10 --beam 2         # tree search: expand the most promising attempts until the budget is spent
uv run quaera bank search "sum of odd numbers"                     # lemma bank: Lean-verified statements reused across projects
uv run quaera models [probe <provider>]    # capability scoreboard per model; the deep probe proves five small Lean statements
uv run python evals/capacity_run.py --set capacity-synth --budget 4   # capacity measurement: previous vs current search
```

**Capacity (any model):** the team is built to turn weaker or cheaper models into verified progress as well: malformed answers are
repaired instead of ending the research, the proof search keeps going while its budget share lasts (whole proofs, step-by-step proving
in the Lean REPL, random testing with `plausible`, library search, recursive decomposition, best-of-N candidates), verified lemmas are
reused across projects, a failed question is explored as a tree (the most promising attempt is expanded next; a stop needs the
logical frame covered and a second model's agreement), and the search adapts to each model's measured reliability. Budgets have four
dimensions: USD, calls, tokens and wall-clock. ML research can run hyperparameter grids as branches, reuses verified results and
the parent's code, and can send GPU jobs to a remote runner under the same sandbox (`QUAERA_REMOTE_RUNNER`). Every run reports
where its budget went (report tab and the evidence package). Measured so far: the same solve rate as before on three sets, with
more attempts per dollar. Plan, status and measurements: [docs/capacity-plan.md](docs/capacity-plan.md).

**Web UI (Phase 3):** first, once: `cd web && npm install && npm run build`. The UI is in English; warm neutral tones, light theme by default, with a dark theme too. The left panel shows short project names and what was learned across projects (contextual memory). The Project manager watches the research and answers your questions without interrupting the team; a note reaches the team only if you send it ([ADR 0016](docs/adr/0016-ui-v3-manager-contextual-memory.md)). The language of model output is chosen with `QUAERA_LANGUAGE` (default English). Codex CLI and OpenCode CLI installed on this machine are detected automatically and used as a different model family for cross-checks (Critic, Verifier, parallel lanes). Mathematics has two modes: **Verify** (test a claim) and **Discover** (attack an open problem with multi-model idea generation, cross-review and a Lean lemma program; only results Lean verifies count) ([ADR 0017](docs/adr/0017-discovery-mode-multi-model.md)). The lab screen shows the team live: you see the code an agent is compiling right now, and clicking an agent opens the full text of what it said. Math and ML experiments are visualized step by step, hypothesis rankings are shown with their criteria, and parallel agents are followed in lanes ([ADR 0015](docs/adr/0015-ui-v2-parallel-agents.md)). You can also answer approval cards, message an agent with `@agent`, and branch from a completed stage; the evidence graph, Lean proof, report (Markdown, PDF printing, signed RO-Crate evidence package) and keyless replay all live in the same UI. The server listens only on 127.0.0.1 and rejects requests from other sites ([ADR 0011](docs/adr/0011-local-web-ui.md)).

Projects are kept under `~/.quaera/projects/` (override with `QUAERA_HOME`). Each project contains `quaera.db` (event log), `cas/` (content-addressed files), `report.md` and `bundle.json`.

**Requirements (Phase 1):** Linux, `bwrap`, a logged-in `claude` CLI (model provider), Lean 4 + Mathlib (`lean/` directory; `lake exe cache get` and `lake build REPL/repl`). The Lean REPL with Mathlib uses about 4–5 GB of memory.

## Repo layout

| Path | Contents |
| --- | --- |
| `schemas/v1/` | JSON Schemas for research objects, messages, agent/skill definitions and the evidence package ([guide](docs/schemas.md)) |
| `agents/` | Role card, workflow, skill list and permissions for each of the nine agents |
| `skills/` | 24 skills: tools (MCP) and the permissions they need |
| `examples/` | End-to-end examples of the schemas: a Lean proof, a contested ML experiment, a negative result; a sample evidence package manifest |
| `src/quaera/` | Engine: data store, permissions, model gateway, sandbox, Lean, MCP tools, orchestrator, report, evidence package, web server, CLI |
| `web/` | UI (React + TypeScript, Vite); `e2e/` browser tests |
| `lean/` | Lean workspace pinned to Mathlib, and the REPL |
| `tools/validate.py` | Validator for the schemas and for rules a schema cannot express on its own |
| `evals/` | Evaluation sets and baselines ([guide](evals/README.md)) |
| `docs/adr/` | Architecture decision records |
| `docs/policy/` | Usage policy and the QuaeraLabs AI label |
| `docs/research/` | Researcher interview guide, alpha candidate list, usability test |
| `docs/alpha/` | Closed alpha program: invitation, getting started, consent, interviews, failure review, metrics |
| `install.sh` | One-command install; `tools/install_test.sh` tests it in clean containers |

## Quick start

```bash
uv sync
uv run pytest                       # fast tests (with a fake model and fake Lean)
uv run pytest -m lean               # real Lean + Mathlib tests (slow)
# Run heavy jobs with a memory/CPU limit (ADR 0010):
tools/limited.sh 8G 300% uv run pytest -m lean
uv run python tools/validate.py     # whole repo
uv run python evals/run_evals.py    # evaluation sets (needs network access)
# UI end-to-end + accessibility (fake models, spends no money):
uv run python tests/e2e_server.py /tmp/qe2e 8766 &   # in a separate terminal
cd web && npm run test:e2e
```

## Core principles

1. No claim without a source.
2. There is no single truth score; evidence status and independence level are shown.
3. A negative result is a result too.
4. Ask before spending money; no agent can publish or raise the budget cap.
5. In mathematics, only Lean grants the "verified" label.
6. Everything is reproducible; the preregistration is locked before results are seen.
7. What the AI did is transparent: every report carries the QuaeraLabs AI label.

## License

Code: [Apache License 2.0](LICENSE). Published research reports and schemas: CC BY 4.0. To contribute, see [CONTRIBUTING.md](CONTRIBUTING.md).
