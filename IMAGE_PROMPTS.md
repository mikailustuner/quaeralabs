# QuaeraLabs — UI Concept Images (Codex task)

## Task

Generate 5 UI concept images with the 5 prompts below and save them to the `images/` folder.

- Generate the images with your image generation tool (e.g. `gpt-image-1`); **1 image** per prompt.
- Size: **1536×1024** (landscape, desktop screen ratio). Quality: high.
- For each image, use the style block from the **"Shared style"** section by prepending it to the screen prompt.
- File names: `01-lab.png`, `02-new-research.png`, `03-evidence-graph.png`, `04-lean-proof.png`, `05-research-report.png`.
- Reference design: `design-reference/lab-screen-B2.html`. This is the current design of the lab screen; check colors, typefaces and component style against it.
- If the text in an image comes out garbled or illegible, regenerate that image **at most once**. Write the results briefly to `images/NOTES.md`: which image took how many attempts, and what went wrong.

## Product context (reference for image generation)

QuaeraLabs is a non-profit, open-source "AI science lab". The user enters a research question; a team of 8 AI agents carries out the research: Director, Literature, Hypothesis, Experiment designer, Engineer, Analyst, Critic and Writer. The human approves costly steps. First domains: **Mathematics** (proofs verified with Lean) and **AI/ML research**. The user brings their own API key, so the token budget is visible on every screen.

---

## Shared style (add to every prompt)

```
High-fidelity UI design screenshot of a modern dark-mode web application called "QuaeraLabs", an open-source AI research lab where a team of AI agents conducts scientific research. Desktop browser viewport, flat front-on screenshot, no device frame, no perspective, no people, no hands.

Visual style: modern, calm, premium developer-tool aesthetic. Near-black background #0A0B0D; panels/cards #121418 with 1px subtle borders rgba(255,255,255,0.07) and 16px rounded corners; inner tiles slightly lighter. Single warm amber accent #F5B83D used sparingly for primary buttons and highlights; mint green #4ADEA3 for "running/live/verified"; soft blue #7DAAFF for data and "supporting"; soft orange #FF9B6A for "critique/contradicting". Typography: clean geometric sans-serif (Geist-like) for UI text, monospace (Geist Mono-like) for numbers, IDs and code. Generous but information-dense layout, crisp alignment on a grid, high contrast readable text, no gradients on backgrounds, no glassmorphism, no emoji, thin-stroke line icons only.

Agents are shown as small cute flat-vector scientist avatars in 44px rounded-square tiles (each with a distinct accessory: bow tie, round glasses, lightbulb, flask, lab goggles, bar-chart badge, magnifying glass, beret).

UI text is in English; keep labels short and render all text sharp and legible.
```

---

## 1) Lab screen — live research → `01-lab.png`

```
Screen: the live "lab" dashboard of one research project.
Top bar: small amber square logo with "QuaeraLabs", breadcrumb "My lab / Warmup study", a wide command search field "Search or command the team ⌘K", a small circular budget ring with "$3.40 / $10", and a mint "Live" pill with a dot.
Header: small tags "AI / ML", "Q-0012"; large title "Is learning-rate warmup necessary in small transformer models?"; below it a 7-segment progress bar labeled Question, Literature, Hypothesis, Experiment, Analysis, Critique, Report — first three amber, "Experiment" half filled.
Three-column layout:
Left card "Research team": vertical list of 8 agents with scientist avatars and status lines; "Engineer" row highlighted mint with "Run 5/9 running" and a thin progress bar; "Analyst" row highlighted amber with "Awaiting your approval".
Center: a card "Training loss · E-0031" with 4 metric tiles (2.47, 3.4k / 10k, ~18 min, 5 / 9) and a line chart of 4 smooth loss curves (blue, dashed blue, orange plateauing, short mint live curve with a dot); below, an approval card with a thin amber gradient border: "Analyst requests your approval" with small chips "+12 runs", "~$2.40", "~3 h GPU" and buttons "Approve" (amber) and "Edit"; below, an "Activity" timeline with colored dots.
Right: "Hypotheses" card with three hypothesis tiles H1 (blue "Testing" badge and a small evidence bar), H2 (orange "Under critique"), H3 ("Queued"); and a "Reproducibility" checklist with mint check marks.
```

## 2) Start new research → `02-new-research.png`

```
Screen: "New research" — creating a new research project, centered single-column form (max width ~760px) on the dark background, with the same top bar.
Step indicator at top: "1 Question · 2 Team · 3 Budget" with step 1 active in amber.
Large multi-line text area labeled "Research question" containing "Can sparse attention reach the same accuracy as full attention on long contexts?".
Below: "Domain" segmented choice with two large selectable cards: "Mathematics" (subtitle "Proofs verified with Lean", small sigma line icon) and "AI / ML" (subtitle "Experiments and ablations", small neural-net line icon) — "AI / ML" selected with an amber border.
A helper card from the "Hypothesis" agent avatar: "2 suggestions to make your question testable" with two suggestion chips.
Section "Team": row of 8 small scientist avatars with toggles, all on.
Section "Budget": slider "Token budget cap" set to "$10", a masked API key field "sk-••••••••3f9c" with a mint "Connected" label, a note "Your key is stored only in your browser".
Bottom right: secondary button "Save draft" and primary amber button "Start research".
```

## 3) Hypothesis and evidence graph → `03-evidence-graph.png`

```
Screen: "Evidence graph" — an interactive node-link graph view of a research project's knowledge.
Full-width canvas area with a subtle dot grid. Nodes are rounded rectangles with monospace IDs: a root question node "Q-0012" on the left; three hypothesis nodes "H1", "H2", "H3"; experiment nodes "E-0031", "E-0032"; result nodes; a critique node with an orange outline; a "derived hypothesis" node "H4" with a dashed outline. Edges are thin curved lines labeled with small pills: "tests", "supports" (blue), "refutes" (orange), "derived".
H1 node is selected, with a soft amber outline.
Right side panel (360px) for the selected node: title "H1 · Warmup is unnecessary at low rates in small models", a status badge "Testing", an evidence summary with a horizontal stacked bar (2 supporting blue, 0 refuting orange, 7 pending gray), a list "Independence level: L1", links to experiments, and a button "Ask the Critic for a review".
Top left: filter chips "All", "Hypotheses", "Experiments", "Critiques", "Negative results"; bottom left: zoom controls and a mini-map.
```

## 4) Mathematics — Lean proof view → `04-lean-proof.png`

```
Screen: a mathematics research project in QuaeraLabs, tag "Mathematics", title "An attempt at a new lower bound for small Ramsey numbers".
Two-column layout.
Left (60%): a code editor card showing Lean 4 source code with syntax highlighting on a dark background (keywords "theorem", "lemma", "by", "intro", "simp", "omega", "exact"), line numbers in monospace, one lemma line highlighted mint with a check mark in the gutter, one line marked with a small orange "sorry" warning badge.
Right (40%): a "Proof tree" card showing a vertical tree of lemmas with status icons: mint check "Verified", amber dot "Agent working on it", orange "Missing step"; above it a large status block "Lean verification: 7 / 9 lemmas" with a progress bar; below it a small card from the "Critic" avatar: "The case split in Lemma 4 may be incomplete" with buttons "Show" and "Ignore".
Top bar identical to the lab screen with budget ring and "Live" pill.
```

## 5) Research report → `05-research-report.png`

```
Screen: "Research report" — the final published report of a finished research project, a readable document layout (content column ~760px) with a right-side sticky outline.
Header: tags "AI / ML", "Completed", "Negative result"; title "Warmup is not necessary in small transformer models — but it improves stability at high learning rates"; meta line "8 agents · 21 runs · $8.70 · 3 days"; small row of 8 scientist avatars labeled "Authors: QuaeraLabs agent team, approved by a human advisor".
Body sections with clear headings: "Summary", "Findings" (with one clean line chart comparing warmup vs no-warmup and a compact results table in monospace), "Critic review" (a card with a mint "Approved" badge and two resolved objections), "Limitations", "Reproduce" (a code block "quaera replicate E-0031" and badges "Code pinned", "Seed", "Environment locked").
Right outline panel: section links, buttons "Download PDF", "Reproduce this study" (amber), and "Open in evidence graph".
```
