# Review mode: systematic review and meta-analysis

The third research domain. A question about published evidence ("does aerobic exercise reduce depressive symptoms in
adults?") goes from a locked protocol to a pooled estimate. The models read and judge. The code checks every number
against its source and does all the arithmetic.

```bash
quaera ask "Does aerobic exercise reduce depressive symptoms compared with usual care in adults?" --domain review --budget 2
quaera verify <project>        # fetches every source again and recomputes the pooled estimate
```

In the web UI, choose **Review** next to Mathematics and AI / ML. It needs only the core install and network access to
Europe PMC, OpenAlex and Crossref.

## What happens
1. **Literature and hypothesis**: the same steps as the other domains. The hypothesis names population, intervention,
   comparison, outcome and direction.
2. **Protocol** (Experiment designer): eligibility criteria, the outcome and which group is group 1, the effect measure
   (`SMD` Hedges' g, `MD`, `OR`, `RR`), search queries and a numeric success criterion on the pooled estimate. The Critic
   reviews it. The preregistration then locks it: the criteria and the measure are inside the hashed `analysisPlan`, so a
   change after screening breaks the hash.
3. **Screening** (Literature agent): OpenAlex and Europe PMC are searched with the protocol's queries. Only records with a
   DOI are kept. Titles and abstracts are judged against the locked criteria. Each included DOI must be confirmed by
   Crossref or DataCite. At most `QUAERA_REVIEW_MAX_STUDIES` studies are included (default 12); the PRISMA counts are recorded.
4. **Extraction** (Analyst, then code): for each study the source text is fetched: the open-access full text from Europe PMC,
   else the abstract from Europe PMC or OpenAlex. A full text is ordered for reading: abstract, results sections, tables,
   then the rest. The text is stored by its SHA-256. The Analyst returns the raw numbers (means/SDs/ns, event counts, a
   reported effect with its 95% CI, or for SMD a reported g or d with the group sizes) and a verbatim quote. An effect
   reported the other way round is marked `reversed`; the code flips its sign, and the report says so. **A row counts only if the
   quote is in the source text and every number is written in the quote.** The model's own effect sizes are never used.
5. **Meta-analysis** (code): effect sizes from the raw numbers, then DerSimonian–Laird random effects: pooled estimate,
   95% CI, p, τ², Q and I². The code was checked against metafor on the BCG vaccine data (`tests/test_review.py`).
   With fewer than two traceable studies there is no pooled estimate and the result is `inconclusive`, whatever the
   models say. The Analyst applies the preregistered criterion, and the Critic reviews the result blind.
6. **Verification** (Verifier): every source is fetched again. A changed text is reported. Each quote and number is
   re-checked against the fresh text and the pooled estimate recomputed. `reproduced = yes` only on an exact match.

The report adds the PRISMA counts, the included studies with their effect sizes or the reason a study was dropped, and
the pooled estimate.

## Limits
- Data comes from abstracts, or from the first 30,000 characters of an open-access full text (results and tables first).
  A study whose numbers are only in a paywalled article is dropped with the reason. Many abstracts give no SDs, so in
  practice only a few studies per review are traceable. In the first live run on exercise and depression, 2 of 12
  included studies were; the result was an honest `inconclusive`, and the Verifier reproduced it.
- One outcome and one effect measure per review. Risk of bias is not assessed, and there is no funnel plot or Egger test.
- A single model extracts the data; the quote check catches invented numbers, not a correct number taken from the wrong
  arm or time point. The Critic and a human reading the quotes in the report are the safeguard for now.
- Screening sees titles and abstracts only.

## Next steps
1. Double extraction by a second model family (`crossModel: true` verification), as two human reviewers do.
2. Risk of bias (RoB 2) per study, done by a model and checked against quotes like the numbers.
3. Responder outcomes converted to SMD (log OR × √3/π), and a longer text window.
4. Small-study effects: funnel plot and Egger's test when k ≥ 10. Subgroups that are declared in the protocol.
5. An eval set: published Cochrane meta-analyses whose pooled estimates the loop should reproduce.
