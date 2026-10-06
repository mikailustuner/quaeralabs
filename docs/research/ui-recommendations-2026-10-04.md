# QuaeraLabs — 5 UI directions

The current React UI, the page components and the running application on 127.0.0.1:8765 were reviewed. Research list, new research, lab and evidence graph were viewed on desktop; the lab was also checked at 375 px width. Because the research tree screenshot stayed in its loading state, the assessment of that view is based on the source code. This work is a design proposal; application code was not changed.

## Current state

- Local application; math and AI/ML research, nine agents, budget and autonomy, human approval, live event stream, agent messages, evidence graph, Lean proof, research branches, report and replay are available.
- The research list showed 39 entries. Branches of the same question are shown on separate rows with the same title. There are no visible list filters, no sorting and no grouping of branches under the root project. Memory search is a separate function.
- In the lab, team, stages, activity, budget, hypotheses and objections are in separate panels. The activity log stays in the center even for a finished research run; result and report are not prioritized.
- In the math example, the general Goldbach question is in the title, and the hypothesis supported only on a finite range is shown with its status badge. The scope restriction is in the long text on the right. A clear scope warning near the title is needed.
- In the 375 px check, the document's horizontal overflow is 0 px. On mobile, however, the activity stream is pushed to the front; budget and evidence information stay further down. This review is not a full accessibility audit.
- The new research form presents field, question, scope, budget and autonomy in one long form. Showing the relevant settings progressively after the field is chosen could be considered.

## Recommendations

| Direction | Visual language | Main interaction | Best fit |
| --- | --- | --- | --- |
| 01 Focus | Warm white, dark green, Geist-like sans | Pending decision in the center; cost, scope and evidence summary beside it | Day-to-day research runs |
| 02 Research Notebook | Ivory, terracotta, serif headings and readable body | Question → hypothesis → experiment → finding → critique; evidence reachable from blocks | Reading results and reviewing reports |
| 03 Evidence Atlas | Light gray, cobalt, wide node canvas | Inspecting a selected claim with its grounds and objections | Scientific traceability |
| 04 Experiment Bench | Navy charcoal, purple, monospace metrics | Experiment plan, seed table, log and critique in a split workspace | Technical AI/ML users |
| 05 Control Center | Light lavender, indigo, dark navigation | Decision inbox; filtered research list and grouped branches | Managing many projects |

Recommended combination: 05 Control Center on the home page, 01 Focus inside a project. Evidence Atlas can be used as a separate review view and Research Notebook as the report view. Experiment Bench is an alternative direction for technical users. The user has not chosen yet.

## Limits of the images

The ImageGen images are design mockups with sample data. The generated person names, agent role names, dates, datasets, file details and numeric values are not real project records. The application should use the existing nine agent roles; no person names should be shown where there is no real human contribution. The answer to the general question and the result of the restricted hypothesis should be shown separately. Verification labels must not be mixed across fields: Lean is for math.

The decision inbox, list filters and grouping can be designed on existing data; collecting the pending approvals of all projects in one place requires a separate look at the API needs. Result tables and charts should only be shown if real run outputs contain data. No new overall trust score should be added.

The new images are stored under `images/ui-concepts-2026-10-04/`. The older images such as `images/01-laboratuvar.png` are kept.
