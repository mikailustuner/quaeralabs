# Phase 3 usability test protocol (5 people)

**Gate criterion (roadmap):** A new user reaches their first report without a guide in 30 minutes. No critical findings remain in the accessibility audit.

The accessibility part is audited automatically (`web/e2e/walkthrough.mjs`, axe-core WCAG 2.1 AA). The usability part must be done **with real people**. This document defines how the sessions are run; the automated walkthrough test does not replace human testing.

## Participants
- 5 people. At least 2 researchers/students working in math or ML, at least 1 person who uses a screen reader or only a keyboard.
- None of them should have seen QuaeraLabs before. Candidate list: the alpha candidate list under `docs/research/`.
- The participant's own machine and own `claude` account. Budget cap at most 3 USD per task. Who covers the cost is settled in writing before the session.

## Preparation (moderator, before the session)
1. `uv sync --all-extras`, `cd web && npm install && npm run build`, `uv run quaera doctor` clean.
2. `~/.quaera/projects` empty; consent obtained for screen recording and think-aloud (the participant can stop the recording at any time).
3. `uv run quaera serve` running, `http://127.0.0.1:8765` open in the browser.

## Tasks (the participant is given only this text)
| # | Task | Success criterion | Time limit |
| --- | --- | --- | --- |
| 1 | Start a math research run with a 2 USD budget on the question “Is n³ − n divisible by 6 for every natural number n?” | Project created | 5 min |
| 2 | If the team asks you for something, decide and get the research run to finish. | Hypothesis selected, experiment approved, report written | 20 min |
| 3 | What was the result and how far can you trust it? Explain in your own words. | Finds where the verification and the proof are shown | 3 min |
| 4 | Send a message to the Critic. | The message appears in the timeline | 2 min |
| 5 | Download the report as PDF and as an evidence package. | Two files downloaded | 2 min |
| 6 | Replay the research run from the start. | Replay advanced at least one stage | 3 min |

**Gate:** passes if at least 4 of 5 people complete tasks 1–2 without help and within 30 minutes in total. The moderator intervenes only if there is a technical crash; every intervention counts as “help” and is recorded.

## Measurements
- Per task: completed or not, time, number of helps, error/dead-end moments (with timestamps).
- At the end of the session, SUS (System Usability Scale, 10 questions, 1–5) and two open questions: “Where did you get stuck the most?”, “What made you trust the result, or kept you from trusting it?”
- Additionally for the screen reader/keyboard participant: was focus lost, were the approval card and new activity announced?

## Record template
| Participant | Profile | T1 | T2 | T3 | T4 | T5 | T6 | Total time | Help | SUS | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | | | | | | | | | | | |
| P2 | | | | | | | | | | | |
| P3 | | | | | | | | | | | |
| P4 | | | | | | | | | | | |
| P5 | | | | | | | | | | | |

## Handling findings
Each finding is opened as an issue with a severity (blocking / serious / minor). The Phase 3 gate is not considered passed until blocking findings are closed. Personal data (names, screen recordings) is not put in the project repository; only the anonymous code (P1–P5) is used.
