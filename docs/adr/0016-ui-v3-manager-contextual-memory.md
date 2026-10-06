# 0016 · UI v3: English UI, Project manager, contextual memory, plain-text math parser

**Status:** Proposed · **Date:** 2026-10-05 · **Related:** [0012](0012-research-memory.md), [0015](0015-ui-v2-parallel-agents.md)

## Context
In the researcher vote the UI was found lacking. There were six problems:
- project names in the left panel were too long;
- what was learned across projects was not visible;
- the "Lab memory" page looked empty (nothing was listed until a search was made);
- the UI was in Turkish;
- math and Lean expressions that agents wrote as plain text were not typeset;
- there was no chat agent that followed the process and answered questions without keeping the Director busy.

The layout of a chat app was given as a visual reference: a narrow left panel, warm neutral tones, a large input box in the middle.

## Decision
1. **Project manager** (`agents/manager.yaml`, `src/quaera/manager.py`).
   - It is a read-only agent: it cannot write objects, run tools or send messages to the team.
   - It builds a summary from the project's event log and answers the human's question based only on that log.
   - If the human wants to tell the team something, the manager *proposes* a note (a `FORWARD:` line). The note goes to the Director only when the human presses the "Send to Director" button, and it is recorded as a normal human message.
   - It has its own budget: `QUAERA_MANAGER_CAP_USD` per project, default $0.50. It is not deducted from the research budget; it is tracked separately with `manager.call` and `manager.error` events.
   - The manager also gives the short project name (`shortTitle`; retroactively with `quaera titles`). The user can rename it.
2. **Contextual memory** (`memory.py`: the `learnings` table, `lessons()`).
   - After the analysis, verification, result and report stages, learnings are derived from the project's own log without using a model: the fate of the hypothesis, a Lean-verified result, a proof method that worked or ran out, a vacuity finding, exploration observations, open objections, the reason for stopping.
   - In new research the Hypothesis agent and the manager see these items for guidance only; they cannot be sources for the report.
   - Evaluation projects and fault-injected projects are not added to this memory.
   - The left panel and the "Lab memory" page list the memory; search is optional.
3. **English UI.**
   - All UI text and the user-facing text produced by the backend (result summaries, approval texts, report template, error messages) are in English.
   - The language of model outputs is chosen with `QUAERA_LANGUAGE` (default English).
   - The prompts ask models to write math in Unicode and Lean expressions in backticks. LaTeX backslashes inside JSON are not requested, because an invalid escape breaks the JSON.
4. **Plain-text math parser** (`web/src/automath.ts`).
   - The code finds math fragments (≤, ∑, ℕ, ε, `x_k`, set braces, fractions, `limsup`) in text outside existing `$…$` and links, and typesets them with KaTeX.
   - It shows Lean expressions (`Summable (fun k => …)`, `Filter.Tendsto …`) as highlighted inline code.
   - A fragment KaTeX cannot parse is shown as code; red error text never appears.
5. **Visual layout.**
   - Warm neutral palette, light theme by default, with a dark theme too. All color pairs were chosen by measuring WCAG AA (≥4.5:1).
   - A serif font in headings (Source Serif 4).
   - Left panel: search, new research, navigation, "What the lab learned", short project names grouped by date, theme switch. The panel can be collapsed; on mobile it opens as a drawer.
   - The home page is a centred input box: field, budget and autonomy below the box.
   - On the project page the default recipient of the bottom input box is the Project manager; a note to the team can be written if desired.

## Consequences
- The researcher can ask questions without interrupting the team. Everything that goes to the team still goes through an explicit human action and is recorded.
- **Race condition fix:** the server request and the research thread wrote to the same database through separate connections. When two human messages arrived back to back, an object identifier could collide (`UNIQUE constraint failed`). `Store.put` now does identifier assignment and the write inside a single `BEGIN IMMEDIATE` transaction; a concurrent-writer test was added.
- **Naming cost:** in the model call for the short name, thinking tokens also count towards the output limit. If the output limit is kept small, the call fails even though it is billed, so the limit is 1500 tokens. The real cost is about $0.002 per project.
