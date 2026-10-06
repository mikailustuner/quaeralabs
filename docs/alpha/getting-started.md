# The first 30 minutes

Goal: get from installation to your first report in 30 minutes without a guide. If you get stuck somewhere, report that too; it is valuable information for us.

## 1. Installation (10–15 min)
```bash
git clone https://github.com/quaeralabs/quaeralabs.git && cd quaeralabs
./install.sh                 # for math: ./install.sh --with-lean   · for ML: --with-ml
quaera doctor                # every required line must be ✓
```
If Claude Code is not installed, install it and log in once with the `claude` command. Details: [installation](../installation.md).

## 2. First research (10–15 min)
1. Run `quaera serve` and open `http://127.0.0.1:8765` in your browser.
2. **New research** → choose the field, write your question in a precise and testable form, enter 2 USD as the budget, and leave autonomy at "Ask at every step".
3. While the team works on the lab screen, you are asked for approval twice: **which hypothesis to test** and **the experiment/proof plan**. Read the card and decide.
4. If you like, write to an agent from the box at the bottom (e.g. @Critic "check the edge cases too").
5. When the research finishes, switch to the **Report** tab. Every sentence carries, in square brackets, the ID of the evidence it rests on; the **Evidence graph** shows how these IDs connect.

## 3. How far can you trust the result?
- **Math:** "supported" is written only when the proof compiles in Lean 4 with the standard axioms. Still, check yourself in the **Proof** tab whether the formal statement encodes your question correctly. The "Restricted scope" label means only part of your question was proven.
- **ML:** the success criterion is locked before the experiment starts (preregistration). The result is measured with several seeds, and the Verifier reruns it in a clean environment with the same seed. The report should not generalize a finding from a single dataset; if it does, use "Report a problem".
- Every source in the report is verified on arXiv or Crossref/DataCite. A source that cannot be verified does not get into the report.

## 4. If something goes wrong
Press the **Report a problem** button on the project page: what went wrong, and what should the right outcome have been? The case stays on your machine. We can look at it together in the interview, or you can share it with `quaera triage export`.
