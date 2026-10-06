# Phase 3 · UI, evidence graph and reports — status

**Date:** 2026-10-04 · **Source:** roadmap, "Phase plan → Phase 3"

The UI runs locally with `quaera serve`. A study can be carried out from start to finish in the browser: the question is asked, approvals are given from cards, the team is watched live, you write to an agent, you branch from a stage. After that the evidence graph, Lean proof, report, signed evidence package and replay are used from the same UI. The automated end-to-end flow test passed 28/28 steps, and the axe-core WCAG 2.1 AA audit passed on 10 screens with no findings. The human part of the gate, the 5-person usability test, has not been done yet.

## Tasks

| Task | Status | Where |
| --- | --- | --- |
| Design system: tokens, components, character avatars | Done: B2 "Observatory Modern" tokens; 8 B2 avatars ported, a new avatar drawn for the 9th role, the Verifier | `web/src/styles.css`, `web/src/avatars.ts` |
| Screens: lab, new study, evidence graph, Lean proof view, report, settings and budget | Done | `web/src/pages/` |
| Live updates: event stream, approval cards, messages, @agent, objection states | Done: SSE; approvals via `WebApprover`; an @message is added once to the agent's next call; on an open high/blocking objection the Critic is marked orange | `server.py`, `pages/Lab.tsx` |
| Autonomy level and branching UI | Done: three modes (in a new study and in "Resume"); branch with ⑂ at every completed stage; a branch shows a link to its parent project | `pages/Lab.tsx`, `/branch` |
| Report export: PDF, Markdown, RO-Crate | Done: PDF via the browser print style; RO-Crate 1.1 zip, ed25519-signed manifest, QuaeraLabs AI label; verification rejects the package if the label is removed or a file changes | `package.py`, `pages/Report.tsx` |
| Replay mode (no key) | Done: from the project page or from a `…-replay.json` file; step, stage, speed, slider; no model calls | `pages/Replay.tsx` |
| Accessibility: keyboard, WCAG AA, screen reader | Done (automated audit): skip link, visible focus, scrollable areas are focusable, arrow-key navigation in the graph, focus moves to the first invalid field on form errors, live regions | `web/e2e/walkthrough.mjs` |
| **Added:** local security | Done: Host/Origin/Sec-Fetch-Site checks and required JSON; another site starting a study and spending the budget is blocked | [ADR 0011](adr/0011-local-web-ui.md) |

## Exit gate

| Criterion (roadmap) | Result |
| --- | --- |
| A new user reaches their first report in 30 minutes without guidance (5-person usability test) | **Pending: human test.** The protocol is ready ([usability test](research/usability-test.md)). Proxy measurement: the automated flow (new study → 2 approvals → @message → report → graph → proof → package → replay → branching) passed 28/28 with fake models. This measurement does not replace the human test. |
| No critical findings remain in the accessibility audit | **Passed**: axe-core (WCAG 2.0/2.1 A and AA) 0 violations on 10 screens; no horizontal overflow at 375 px width |

## Verification
- `uv run pytest`: 56 fast tests passed. 7 of them are server tests: list/detail/graph/report/replay, input validation, @message, web approver, signed package and tamper detection, branch parent info, cross-site and rebinding rejection.
- `web/e2e/walkthrough.mjs`: real server code, fake models (`tests/e2e_server.py`), headless Chromium.
- A visual check was done on real projects (the math and ML runs from Phases 1–2) with screenshots at 1440 px and 375 px.

## Limits
- Single user, 127.0.0.1 only. No authentication for remote access (v1.1).
- If the server restarts, a pending approval is lost. "Resume" continues the study from where it stopped and asks for the approval again.
- The PDF is produced with the browser's print dialog; there is no server-side PDF generation.
- The evidence graph is layered with a fixed layout. Zoom/filtering for very large graphs (hundreds of nodes) was left to Phase 4.
