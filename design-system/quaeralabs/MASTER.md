# QuaeraLabs design system (v3, 2026-10-05)

Sources:
- ui-ux-pro-max recommendation: "Swiss Modernism 2.0" (grid, rational spacing, minimal).
- Reference given after the researcher vote: narrow left panel, warm neutral tones, large input box in the center, serif welcome heading.

The reference's layout and color tone were adapted; the brand, icon and typefaces belong to QuaeraLabs. This document replaces the previous version (v2: iOS gray + green accent).

## Decisions
- **Language:** the UI is in English. The language of model output is chosen with `QUAERA_LANGUAGE` (default English).
- **Theme:** light by default. The dark theme follows the system preference or the icon selector in the sidebar (System / Light / Dark). The choice is stored only in the browser.
- **Color (tokens in `web/src/styles.css`):**
  - Light theme: warm paper background #FAF9F5, left panel #F3F1EA, white cards, text #1F1E1B.
  - Dark theme: #1F1E1C / #1A1917 / #262523, text #ECEAE4.
  - Clay orange accent: #A9502F in light, #E2896A in dark.
  - Semantic colors: success/supported = green, refuted/error = red, warning/objection = dark yellow, info/method = blue, open question = purple.
  - Every text/background pair was measured: lowest 4.79:1 (WCAG AA).
- **Type:** headings and welcome in Source Serif 4; body in Inter; code and Lean in Geist Mono; math in KaTeX.
- **Layout:**
  - Left panel (276 px): brand + collapse, search, New research, navigation, "What the lab learned" (contextual memory), single-line short project names grouped by date, local lab + theme on the bottom row.
  - On mobile the panel opens as a drawer (Esc and tapping the backdrop close it).
  - Home page: centered serif welcome + large input box. Domain, budget and autonomy below the box; example questions as chips.
  - Project page:
    - at the top, the short name (renamable), the full question (typeset) and the stage bar;
    - in the main column, the decision card, evidence summary, experiment/proof and activity;
    - in the right column, the Project manager, budget, active agent, team and limits;
    - at the bottom, a sticky input box: the default recipient is the manager, optionally a note to the team.
- **Corner radius:** card 14 px, inner element 10 px, button 9 px, input boxes 18 px, badge/chip fully rounded. No glass/blur.
- **Motion:** minimal; 150–200 ms; `prefers-reduced-motion` is respected.
- **Accessibility:** AA contrast in both themes, visible focus ring, all interactions keyboard-accessible, focus trap and Esc for drawer/dialog, scrollable regions focusable. The axe audit runs in the end-to-end test in light and dark themes.
