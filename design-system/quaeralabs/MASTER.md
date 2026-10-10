# QuaeraLabs design system (v4, 2026-10-10)

Sources:
- ui-ux-pro-max recommendation: "Swiss Modernism 2.0" (grid, rational spacing, minimal).
- Reference given after the researcher vote: narrow left panel, warm neutral tones, large input box in the center, serif welcome heading.

The reference's layout and color tone were adapted; the brand, icon and typefaces belong to QuaeraLabs. v4 keeps the v3 layout and clay orange, with a deeper dark theme, larger radii and Apple-style details (segmented controls, hairline borders, frosted sticky bars), and denser project views that need less scrolling.

## Decisions
- **Language:** the UI is in English. The language of model output is chosen with `QUAERA_LANGUAGE` (default English).
- **Theme:** light by default. The dark theme follows the system preference or the icon selector in the sidebar (System / Light / Dark). The choice is stored only in the browser.
- **Color (tokens in `web/src/styles.css`):**
  - Light theme: warm grouped background #F2F0EA, left panel #EAE7DF, white cards, text #1F1E1B.
  - Dark theme: background #121110, left panel #0E0D0C, cards #1C1B19, text #F2F0EA.
  - Clay orange accent: #A04A2A in light, #E8906E in dark.
  - Semantic colors: success/supported = green, refuted/error = red, warning/objection = dark yellow, info/method = blue, open question = purple.
  - Every text/background pair was measured: lowest 4.84:1 (WCAG AA).
- **Type:** headings and welcome in Source Serif 4; body in Inter; code and Lean in Geist Mono; math in KaTeX.
- **Layout:**
  - Left panel (276 px): brand + collapse, search, New research, navigation, "What the lab learned" (contextual memory), single-line short project names grouped by date, local lab + theme on the bottom row.
  - On mobile the panel opens as a drawer (Esc and tapping the backdrop close it).
  - Home page: centered serif welcome + large input box. Domain, budget and autonomy below the box; example questions as chips.
  - Project page:
    - at the top, the short name (renamable), the question (typeset, clamped to 3 lines with "Show more") and the stage bar in a rounded track;
    - in the main column, the decision card, evidence summary, experiment/proof and activity; long statements are clamped with "Show more";
    - discovery mode shows Strategies / Lemma program / Landscape as tabs; the strategy board is a compact list plus the selected strategy in full (a horizontal strip on narrow screens, arrow keys move the selection);
    - in the right column, the Project manager, budget, active agent, team and limits; the column scrolls on its own;
    - at the bottom, a sticky one-row input box (message · recipient · send): the default recipient is the manager, optionally a note to the team.
- **Corner radius:** card 20 px, inner element 14 px, button 11 px, input boxes 22–24 px, tabs/segmented controls/badges/chips fully rounded.
- **Surfaces:** hairline borders and soft layered shadows; frosted glass (blur) only on sticky bars (mobile header, composer) and dialog backdrops.
- **Motion:** minimal; 150–200 ms; `prefers-reduced-motion` is respected.
- **Accessibility:** AA contrast in both themes, visible focus ring, all interactions keyboard-accessible, focus trap and Esc for drawer/dialog, scrollable regions focusable. The axe audit runs in the end-to-end test in light and dark themes.
