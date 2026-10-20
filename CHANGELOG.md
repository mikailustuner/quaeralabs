# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Plans and measurements live in `docs/`; this file lists what changed for users.

## [Unreleased]

Phases 1 to 6 of the improvement plan of 2026-10-20 (items A1–E4 below are named after it).

### Phases 5 and 6

#### Added
- `quaera critique DIR`: the measured Critic on an experiment directory you ran yourself. It reads code, results,
  pre-registration and report with line numbers, executes nothing, confirms a `blocking` objection with a second model
  family, and writes Markdown or JSON (`--fail-on` for CI). The critic eval goes through the same function.
- `quaera verify-package FILE.zip`: signature, AI label, file hashes, manifest schema, citations against the official
  registries, and with `--lean` a clean recompilation of the proof. The same offline checks run in the browser
  (`web/verify/`, published by `.github/workflows/pages.yml`); nothing is uploaded.
- GitHub Actions: `tools/actions/critique` comments a critique on changed experiments in a pull request;
  `tools/actions/audit` checks the evidence packages in a repository (`docs/github-action.md`).
- Fast path: when the literature step finds a mathematical result already known, the numerical exploration is skipped.
  Reports show the time to result.
- Measurement switches for the proof search, off by default until measured: `QUAERA_ENGINEER_LADDER` (start cheap and
  climb), `QUAERA_LADDER_STEP`, `QUAERA_APPLY_HINTS` (Lean's `apply?` suggestions in later rounds).

#### Changed
- README: verified, auditable research first; the three standalone commands; a comparison with related tools.

### Phase 4

#### Added
- One Lean server per process: research branches share one REPL instead of loading Mathlib (4–5 GB) each
  (`QUAERA_SHARED_LEAN=0` turns it off). One-shot builds run one at a time (`QUAERA_LEAN_SLOTS`).
- `quaera doctor` shows the last Mathlib load time and whether it failed; a load that times out now says why
  (`docs/installation.md#lean-on-a-slow-disk`).
- PutnamBench pinned in full (`evals/data/putnambench-a635813f.jsonl`, 672 problems, answer comments removed):
  `capacity_run.py --set putnambench --n 50 --seed S`. A `fresh` set for problems after the models' knowledge cutoff
  (`evals/sets/fresh-2026-q4.yaml`, approved problems only). The hidden `capacity-synth` split runs only with `--tag`.
- Contamination status per set in `evals/README.md` and in every capacity result.
- Discovery targets carry an expectation (synthesis, Mathlib, lemmas); the result counts the ones met and lists targets
  that missed theirs twice in a row.

### Phase 3

#### Added
- Lean and ML container images (`docker/lean.Dockerfile`, `docker/ml.Dockerfile`), published by `.github/workflows/images.yml`
  for amd64 and arm64. They run in the container sandbox with no host directory but the working directory. Choose with
  `QUAERA_LEAN` / `QUAERA_ML` (`host`, `image`, `auto`); `install.sh --lean-image` and `--ml-image` pull them.
- macOS install path: `install.sh` uses containers there and pulls the images. Not yet tested on a Mac.
- Literature: Semantic Scholar search (with `S2_API_KEY`), OpenAlex abstracts and citation counts, a second search pass
  with rewritten queries when nothing relevant was found (at most 8 queries), and a 7-day search cache.
- `evals/discovery_json_run.py`: JSON reliability of Discover ideation and cross-review.
- The capability scoreboard counts answers cut off at the output limit (`output_cut`).

#### Fixed
- The Claude CLI provider read only the final `result` field, which holds the last text block. An answer split by
  thinking lost its beginning; this is the likely cause of the invalid JSON in the 2026-10-05 Discover run. The answer is
  now read from the stream with all text blocks.

### Phase 2

#### Added
- `quaera prove FILE --decl NAME --budget B [--write]`: Lean project mode. It closes the `sorry` of one declaration in
  your own Lean project with the same proof search as a research run. Lean checks against your project inside the
  sandbox. Everything above the declaration stays byte for byte the same. The model may only add helper lemmas above the
  declaration and write its proof. A found proof is rebuilt from scratch with the whole file and shown as a diff, and it
  is written only with `--write` and your confirmation. Guide: `docs/lean-project-mode.md`.
- Container sandbox backend (Docker or Podman) next to bubblewrap, behind one contract that both backends pass
  (`tests/test_sandbox_contract.py`, ADR 0018). Choose with `QUAERA_SANDBOX=bwrap|container|auto`. `auto` uses a container
  where bubblewrap cannot run, for example under Ubuntu's AppArmor namespace restriction.
- Installed-package path: the wheel ships the rule files and the built web UI. `quaera setup ml` creates the ML
  environment in `~/.quaera/ml-env`. `tools/wheel_install_test.sh` checks the whole path in a clean container. A release
  workflow publishes to PyPI on a version tag (trusted publishing; one-time setup on PyPI).
- `quaera doctor` reports in tiers: core, AI/ML, mathematics, extras. A missing research tier is not an error. The web
  UI disables a research domain that is not installed and says what is missing.

#### Fixed
- bubblewrap mounted its private `/tmp` after the read-only binds, so any directory below `/tmp` was invisible inside
  the sandbox. This affected a data directory or a project there.
- The Lean REPL accepted a failed import of its header silently.
- The bubblewrap check now also mounts `/proc`; inside some containers namespaces work but `/proc` cannot be mounted.

### Phase 1

#### Fixed
- `install.sh` and the installation guides cloned from `github.com/quaeralabs/quaeralabs`, which does not exist; they now
  use `github.com/mikailustuner/quaeralabs`. The clean-container install test checks that the default address is reachable.
- The literature step could call a textbook fact `novel` when the search returned only unrelated papers. The verdict is now
  decided in code: `novel` and `partially_done` need at least one verified, relevant source, otherwise the verdict is `unknown`.
- A project whose run crashed (for example a Lean tool error) showed as "paused" in the project list after a server restart,
  because the crash was only kept in server memory. Project status is now derived from the event log.

#### Added
- Mathlib probe: before the proof search, the approved statement is tried with `exact?`. If Mathlib already contains it, the
  literature verdict becomes `already_done` with basis `mathlib`, the report names the declaration, and no model call is made
  for the proof.
- Project status in `/api/projects`: `running`, `waiting` (an approval card is open), `crashed`, `stopped`, `paused`, `done`.
  The project list and the sidebar show it, and the home page lists projects that need attention.
- The report's literature section lists how many search results were found, how many were relevant and how many were verified.
- Discovery mode is labelled experimental, with the expectation for famous open problems stated next to the mode switch.
- Discovery: the research landscape rates the target `tractable`, `hard` or `famous_open`. For a famous open problem the target
  approval card states up front that the realistic output is partial Lean-verified lemmas. The report's summary now starts with
  one line: main claim status, verified lemmas, refuted lemmas, abandoned strategies.
- The literature eval runs every case three times and fails if a textbook fact is ever called `novel`; four cases were added.

#### Changed
- The Riemann Hypothesis example was removed from the home page; Discovery examples are now targets the team can make
  verified progress on.

## [0.1.0] - 2026-10-10

First tagged state: nine-role research loop for mathematics (Lean 4 + Mathlib) and AI/ML, local web UI, signed RO-Crate
evidence package, integrity audit, research memory, failure triage, one-command Linux install, API-key and local model
providers, budget-driven proof search, research tree, evaluation harness. Details: `docs/status-phase0.md` …
`docs/status-phase4.md` and `docs/capacity-plan.md`.

[Unreleased]: https://github.com/mikailustuner/quaeralabs/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mikailustuner/quaeralabs/releases/tag/v0.1.0
