# 0015 · UI v2, parallel agents and live visibility

**Status:** Proposed · **Date:** 2026-10-04 · **Related:** [0003](0003-model-gateway.md), [0011](0011-local-web-ui.md), [0014](0014-agent-intelligence.md)

## Context
The user asked for three things:
- an iOS-style UI with light and dark themes, functional everywhere;
- visibility into what each agent says and what it is doing right now;
- experiments and proofs shown step by step, not skipped over as "done".

They also asked for agents to work in parallel at suitable stages to improve throughput.

## Decision
1. **Visibility events.** Agent text and what is running in which tool go into the event log, so the live screen and replay are fed from the same log.
   - `agent.said`: every model output. The full text is kept in the CAS; the event holds only the purpose, lane, sha256 and a short preview.
   - `activity`: started, finished or failed; the lane if there is one.
   - `tool.started`: written before a tool call starts, and includes the Lean or Python code.
   - The full text is read with `GET /api/projects/{pid}/blob/{sha}`. Only that project's blobs are served, and the limit is 400 KB.
2. **Parallel agents.** The budget cap is never exceeded in any case.
   - **Budget:** the gateway reserves the worst-case amount of every call under a lock (reservation). Parallel calls cannot exceed the cap in total; this is tested.
   - **Hypothesis:** two lanes (A and B) generate concurrently from two perspectives. If a second provider family is configured, lane B uses it (cross-model).
   - **Proof:** in the first round two candidates are written in parallel: a direct proof and a structured proof.
   - **ML:** seed runs proceed in parallel up to `QUAERA_PARALLEL_RUNS` (default 2). With a GPU they run sequentially.
   - **Why:** these stages are independent jobs. Sequential steps (approvals, preregistration, verification) stay sequential.
3. **UI v2.** The design system is in `design-system/quaeralabs/MASTER.md`.
   - **Theme:** light theme by default; dark theme follows the system or is chosen manually. WCAG AA in both themes, audited with axe.
   - **Layout:** left sidebar; stage bar; decision card; evidence summary; experiment and proof section; on the right, budget, active agent, team and limits.
   - **Agent drawer:** what the agent said is shown as full, readable text; the steps taken are listed too.
   - **Hypothesis ranking:** answers "What was ranked?": criterion weights, scores per candidate, chosen/presented/eliminated status.
   - **Experiment visualisation.** Math:
     - vacuity and refutation checks
     - automation
     - candidates A and B, with their errors
     - sketch, lemma by lemma
     - verified proof or refutation

     ML:
     - locked preregistration
     - data preview and histograms
     - seed chart, mean, confidence interval and threshold
     - pilot logs and experiment code
   - **Parser (`web/src/rich.tsx`):**
     - Markdown, KaTeX ($, $$, \( \), \[ \]), Lean and Python highlighting
     - a Turkish field/value view of JSON outputs
     - All HTML goes through DOMPurify.

## Consequences
- The live screen shows the code the agent is writing or compiling right now. Because the code goes into the event, old logs look the same in replay.
- Parallel stages are faster. Cost does not change for the same work; it may rise slightly on easy problems because the first proof round writes two candidates.
- The event log grows. That is why long texts are kept in the CAS and only a preview stays in the event.
