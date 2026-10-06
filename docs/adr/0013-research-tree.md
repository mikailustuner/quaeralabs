# 0013 · Research tree: branches with changes and iterative research

**Status:** Proposed · **Date:** 2026-10-04 · **Related:** [0004](0004-data-store.md), [0005](0005-orchestration.md), [0011](0011-local-web-ui.md)

## Context
In Phase 3, branching was an exact re-run from an event point. Real research, however, goes like this: a hypothesis is tried, it stays inconclusive, something is changed (the hypothesis, the experiment or the proof approach), and it is tried again. The user wanted to see this tree: what changed, how the results differed, the rates.

## Decision
- **Branch = change + rationale.** There are three kinds:
  - `hypothesis`: branches after literature. If a human writes the hypothesis, it is recorded under their name. If the Director proposes it, the proposal goes as a message to the Hypothesis agent and the agent writes the hypothesis; the Director has no permission to write hypotheses.
  - `approach`: branches after hypothesis approval. The instruction is passed to the Experiment designer and the Engineer.
  - `note`: branches after the chosen stage; the note is passed to the whole team.
  The information lives in the child's `branch` metadata and `branch.change` event, and in the parent's `branch.spawned` event. The `root` metadata ties the tree together.
- **Tree view** (`GET /api/projects/{id}/tree`, "Research tree" in the UI):
  - Each branch shows its result, Lean or replication verification, the primary metric and its difference from the parent, and the branch cost.
  - A word-level diff against the parent is shown for the hypothesis, method, success criterion and formal statement.
  - Tree totals give the success rate, conclusive-result rate, total cost, cost per conclusive result and the best metric.
  - Branch cost counts only calls after the branching; spending copied from the parent is not counted twice.
- **Iterative research** (`quaera iterate`, `POST /iterate`):
  - Only **inconclusive** branches are iterated (no proof found, experiment unclear, research stopped). A refuted hypothesis is a result.
  - The Director looks at what happened (open objections, recent runs, Lean output) and decides `hypothesis`, `approach` or `stop`. The decision is recorded with a `branch.proposed` event.
  - Before each new branch is opened, human approval or the autonomy rule applies. The approval amount is the branch budget plus $0.50 for the Director's decision.

## Consequences
- Branches are separate projects; they are tied together by the same research question title and `root`. As the tree grows, the project list gets crowded (later: collapsing trees in the list).
- Phase 3-style exact branches (`-branch-<seq>`; older projects use `-dal-<seq>`) are kept and are not part of the tree.
