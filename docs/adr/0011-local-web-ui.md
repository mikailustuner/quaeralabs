# 0011 · The UI is a local web server; screen state is derived from the event log

**Status:** Proposed (Phase 3) · **Date:** 2026-10-04 · **Related:** [0001](0001-local-first.md), [0004](0004-data-store.md), [0005](0005-orchestration.md)

## Context
The Phase 3 UI includes the live lab, approval cards, evidence graph, proof, report and replay screens. The product is local-first (ADR 0001): QuaeraLabs operates no server and keys never leave the user's machine. A desktop shell (Electron/Tauri) adds distribution burden; the goal of Phase 3 is to validate the flow.

## Decision
- **`quaera serve`**: Starlette + uvicorn, `127.0.0.1:8765` only. JSON API + event stream (SSE, ~1 s latency). The UI is built under `web/` with React + TypeScript (Vite) and served from `web/dist`. Fonts are bundled in the package; the UI never makes a request to the outside network.
- **The event log is the single source of truth.** Team state, stages, budget, objects, pending approvals and the timeline are derived on the client with the pure function `derive(events)`. The live screen and replay use the same code; replay passes `events.slice(0, n)`. The evidence graph is also built on the client from the objects (same rules as the server's `graph()`), so a replay file can be opened without a server.
- **Human approval over the web:** at an approval point `WebApprover` writes an `approval.pending` event and waits for the decision; the autonomy modes (`manual`, `under`, `cap`) apply unchanged, and publishing always requires human approval.
- **No authentication, but closed to local attacks.** So that another site open in the browser cannot start research and spend the budget, every request is checked: Host only 127.0.0.1/localhost (DNS rebinding), Origin local if present, `Sec-Fetch-Site: cross-site` rejected, POST body must be `application/json` (cross-origin requests trigger a preflight, which is blocked because there is no CORS permission).
- **Routing is hash-based** (`#/p/<id>/graph`): the server serves a single `index.html`, and deep links and the browser back button work.

## Verification
- `tests/test_server.py`: list/detail/graph/report/replay, input validation, an @message delivered exactly once, the web approver, a signed and tamper-resistant evidence package, branch metadata, cross-site and rebinding requests.
- `web/e2e/walkthrough.mjs`: end-to-end flow in the browser (28 steps) against the real server with a fake model (`tests/e2e_server.py`), and an axe-core WCAG 2.1 AA audit on every screen.

## Consequences
- Single user, single machine. Multi-user/remote access (e.g. checking a runner on a server from a phone) needs authentication; this will be handled together with the v1.1 publishing network.
- If the server restarts, pending approvals are dropped; the research continues where it left off with "Resume" and the approval is asked again.
- A desktop package (Tauri) will be reconsidered in Phase 4 for the installation experience.
