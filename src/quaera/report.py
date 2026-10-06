"""Rapor yazıcısı (Faz 1: şablonla çalışan Yazar).

Rapordaki her iddia bir araştırma nesnesinin kimliğine bağlanır. Rapor, QuaeraLabs AI
etiketini taşır ve yazılmadan önce projenin tüm kuralları (final=True) kontrol edilir.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import __version__
from .gateway import Gateway
from .store import Store, now

LABEL = ("This research was produced by the **QuaeraLabs AI agent team**. Models used: {models}. "
         "Cross-model review: {cross}. QuaeraLabs version: {version}. "
         "This report is a local draft; publishing it requires human approval.")


def write_report(store: Store, gateway: Gateway, out_dir: Path) -> Path:
    objs = store.latest()
    by = lambda t: [o for o in objs if o["type"] == t]  # noqa: E731
    q = by("question")[0]
    hyps, results, crits = by("hypothesis"), by("result"), by("critique")
    vers, papers = by("verification"), [a for a in by("artifact") if a["kind"] == "paper"]
    runs = by("run")
    calls = [e["payload"] for e in store.events("model.call")]
    models = sorted({f"{c['role']}: {c['model']}" for c in calls}) or ["—"]
    fam = lambda role: {c["family"] for c in calls if c["role"] == role}  # noqa: E731
    producers = fam("engineer") | fam("analyst") | fam("hypothesis")
    reviewed = [e["payload"] for e in store.events("strategy.reviewed")]
    if reviewed:                                    # discovery mode: every strategy has its own reviewer
        n = sum(1 for r in reviewed if r.get("crossFamily"))
        cross = f"{'yes' if n == len(reviewed) else 'partly'} ({n} of {len(reviewed)} strategy reviews by a different model family)"
    elif fam("critic") and not (fam("critic") & producers):
        cross = "yes (Critic in a different model family)"
    elif len({c["family"] for c in calls}) <= 1:
        cross = "no (single provider family)"
    else:
        cross = "no (the Critic shared a model family with a role whose work it reviewed)"
    formal = next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "formal"), None)
    proof = next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "proof"), None)
    stopped = next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "stopped"), None)
    if stopped:                                     # older runs stored raw model output in the stop reason
        stopped = " ".join(str(stopped).replace("*", "").replace("`", "").replace("#", "").split()).rstrip(".")
        stopped = stopped if len(stopped) <= 240 else stopped[:240].rstrip() + "…"
    lit = next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "literature"), None)

    chosen = next((h for h in hyps if h["status"] not in ("draft", "rejected")), None)
    scope_reviews = [e["payload"] for e in store.events("scope.review")]
    critic_weaker = scope_reviews[-1] if scope_reviews and scope_reviews[-1]["weakerThanQuestion"] else None
    self_restricted = chosen and chosen.get("scopeRelation", {}).get("relation") in ("restricted", "related")
    if chosen and chosen["status"] == "refuted":
        how = ("the negation of the formal statement (a counterexample) was proved in Lean 4 and recompiled in a clean environment; this "
               "relies on the assumption that the statement encodes the hypothesis correctly" if q["domain"] == "math"
               else "by the preregistered criterion the result contradicts the hypothesis, and the Verifier reproduced it with the same seed")
        verdict = f"**Hypothesis refuted** ({chosen['id']}): {how}."
    elif chosen and chosen["status"] == "supported" and (critic_weaker or self_restricted):
        notes = [n for n in ((chosen.get("scopeRelation") or {}).get("note"), critic_weaker and critic_weaker["note"]) if n]
        verdict = (f"**Only a restricted version of the question was proved; the original question remains open.** Hypothesis {chosen['id']} was supported "
                   f"(the Lean 4 proof was recompiled in a clean environment), but this hypothesis does not fully cover the question. "
                   + " ".join(f"Scope note: {n}" for n in notes))
    elif chosen and chosen["status"] == "supported":
        how = ("the Lean 4 proof was recompiled in a clean environment" if q["domain"] == "math"
               else "the preregistered criterion was met and the Verifier reproduced it with the same seed")
        verdict = f"**Hypothesis supported** ({chosen['id']}): {how}."
    elif chosen:
        why = "no verified proof was found" if q["domain"] == "math" else "no conclusive, verified result was reached under the preregistered criterion"
        verdict = f"**Inconclusive** ({chosen['id']}, status: {chosen['status']}): {why}."
    else:
        verdict = "**The research did not get past the hypothesis stage.**"
    if stopped:
        verdict += f" Research stopped: {stopped}" + ("" if stopped.endswith("…") else ".")

    lines = [f"# {q['title']}", "", f"_{now()[:10]} · {q['id']} · QuaeraLabs research report_", "",
             "> " + LABEL.format(models="; ".join(models), cross=cross, version=__version__), "", "## Summary", "", verdict, ""]
    if lit:
        lines += ["## Literature", "", lit["summary"] or "—", "", f"Novelty assessment: `{lit['verdict']}`."]
        if papers:
            lines += ["", "Verified sources: " + ", ".join(f"{p['uri']} ({p['id']})" for p in papers)]
        lines.append("")
    lines += ["## Hypotheses", ""]
    scope_tr = {"full": "fully covers the question", "restricted": "restricted", "related": "related"}
    for h in hyps:
        sc = h.get("scopeRelation")
        lines.append(f"- **{h['id']}** ({h['status']}" + (f", scope: {scope_tr[sc['relation']]}" if sc else "") + f"): {h['statement']}")
    if chosen:
        lines += ["", f"Tested: {chosen['id']}. Falsifiability note: {chosen['falsifiabilityNote']}", ""]
    if formal:
        lines += ["## Formal statement", "", "```lean", formal["source"].strip(), "```", ""]
    if proof:
        lines += ["## Proof", "", "```lean", proof["source"].strip(), "```", ""]
    analysis = next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "analysis"), None)
    writer = next((e["payload"] for e in reversed(store.events("writer.output"))), None)
    pre = by("preregistration")
    if analysis:
        lines += ["## Preregistration", ""]
        for p_ in pre:
            lines.append(f"- **{p_['id']}** ({p_['lockedAt'][:16]}): primary metric `{p_['primaryMetric']}` · success criterion: {p_['successCriterion']} · {p_.get('seeds')} seed")
        lines += ["", "## Runs", "", "| ID | Kind | Seed | Status | Metrics |", "| --- | --- | --- | --- | --- |"]
        for r in runs:
            lines.append(f"| {r['id']} | {r['kind']} | {r['seed']} | {r['status']} | " + ", ".join(f"{k}={v:.4g}" for k, v in (r.get('metrics') or {}).items()) + " |")
        lines += ["", "## Statistics (computed by code)", "", "| Metric | n | Mean | Std | 95% CI |", "| --- | --- | --- | --- | --- |"]
        for k, v in analysis["table"].items():
            ci = f"[{v['ci95'][0]:.4g}, {v['ci95'][1]:.4g}]" if v.get("ci95") else "—"
            lines.append(f"| {k} | {v['n']} | {v['mean']:.4g} | {v['std']:.3g} | {ci} |")
        lines += ["", f"Analyst's verdict under the preregistered criterion: **{analysis['relation']}**", ""]
    if writer:
        tr = {"yes": "Yes", "no": "No", "unclear": "Unclear"}
        lines += ["## Answer to the question", "", f"**{tr[writer['answer']]}.** {writer['answerReason']}", "", "## Discussion", ""]
        lines += [f"- {x}" for x in writer["kept"]] or ["- (No sentences could be tied to a source.)"]
        if writer["dropped"]:
            lines += ["", f"_{len(writer['dropped'])} of the Writer's sentences were removed from the report because they were not tied to a research object._"]
        lines.append("")
    lines += ["## Results", ""]
    for r in results:
        lines.append(f"- **{r['id']}**: {r['summary']}" + (" _(negative result)_" if r.get("negative") else ""))
        for lim in r["limitations"]:
            lines.append(f"  - Limitation: {lim}")
    lines += discovery_section(store)
    failed = [r for r in runs if r["status"] == "failed"]
    lines += ["", "## What did not work?", ""]
    lines += [f"- {len(failed)} failed runs: " + ", ".join(r["id"] for r in failed)] if failed else ["- No failed runs."]
    if stopped:
        lines.append(f"- The research stopped before finishing: {stopped}" + ("" if stopped.endswith("…") else "."))
    invalid = Counter(f"{e['payload']['role']} ({e['actor']['modelFamily']})" for e in store.events("model.invalid_json"))
    if invalid:
        lines.append(f"- {sum(invalid.values())} model answers could not be read as JSON: "
                     + ", ".join(f"{k} ×{v}" for k, v in invalid.items()) + ".")
    if store.events("model.error"):
        lines.append(f"- {len(store.events('model.error'))} model calls failed (model.error events).")
    lines += ["", "## Critic review", ""]
    lines += [f"- **{c['id']}** [{c['severity']}, {c['status']}]: {c['body']}" + (f" → {c['resolution']['text']}" if c.get("resolution") else "")
              for c in crits] or ["- No objections recorded" + (", but the research stopped before the review was complete." if stopped else ".")]
    lines += ["", "## Verification", ""]
    how_v = "one-off compilation in a clean environment" if q["domain"] == "math" else "the recorded commit was re-run in a clean directory with the same seed"
    lines += [f"- **{v['id']}**: reproduced = `{v['reproduced']}` ({how_v}; cross-model: {'yes' if v['crossModel'] else 'no'})"
              + (f" · differences: {'; '.join(v['differences'])}" if v.get("differences") else "") for v in vers] or ["- No verification performed."]
    cost = sum(c["costUsd"] for c in calls)
    fams = Counter(c.get("family", "?") for c in calls)
    subs = sum(1 for c in calls if c.get("billing") == "subscription")
    lines += ["", "## Cost and record", "",
              f"- Model calls: {len(calls)} · total ${cost:.4f} · budget cap ${gateway.cap_usd:.2f}",
              "- Model families: " + ", ".join(f"{k} {v}" for k, v in fams.items())
              + (f" · {subs} call(s) via subscription CLIs (no per-call charge reported)" if subs else ""),
              f"- Events: {len(store.events())} · objects: " + ", ".join(f"{k} {v}" for k, v in Counter(o['type'] for o in objs).items()),
              "", "## Reproduce", "", "```bash", f"quaera verify {store.path.parent}", "```", ""]

    errors = store.check_final()
    if errors:
        lines += ["## Rule violations", "", *[f"- {e}" for e in errors], ""]
    text = "\n".join(lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rapor.md"
    path.write_text(text, encoding="utf-8")
    (out_dir / "bundle.json").write_text(json.dumps(store.bundle(), ensure_ascii=False, indent=2), encoding="utf-8")
    store.append("report.written", {"kind": "agent", "role": "writer", "model": "quaera/deterministic", "modelFamily": "quaera"},
                 {"path": str(path), "sha256": store.put_blob(text.encode()), "ruleViolations": errors})
    return path


def discovery_section(store: Store) -> list[str]:
    """Keşif kipi: stratejiler (kim önerdi, kim inceledi), lemma programı ve her lemmanın Lean durumu."""
    proposed = [e["payload"] for e in store.events("strategy.proposed")]
    if not proposed:
        return []
    reviews = {e["payload"]["id"]: e["payload"] for e in store.events("strategy.reviewed")}
    for st in next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "strategies"), []):
        if st["id"] not in reviews and str((st.get("review") or {}).get("summary", "")).startswith("review failed"):
            reviews[st["id"]] = {"reviewerFamily": "review failed", "summary": st["review"]["summary"][len("review failed: "):]}
    chosen = [e["payload"]["id"] for e in store.events("strategy.chosen")]
    dead = {e["payload"]["id"]: e["payload"]["reason"] for e in store.events("strategy.dead")}
    lines = ["", "## Discovery program", "",
             "Strategies were proposed in parallel by several model families with creative lenses and each was reviewed by a "
             "different family. Only Lean-verified statements count as results; numerical checks are evidence, not proof.", "",
             "| Strategy | Proposed by | Lens | Aim | Cross-review | Score | Outcome |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for p in proposed:
        r = reviews.get(p["id"], {})
        outcome = "chosen" if chosen and chosen[0] == p["id"] else ("abandoned: " + dead[p["id"]] if p["id"] in dead else "fallback")
        lines.append(f"| {p['id']} {p['title']} | {p['family']} | {p.get('lensName', p.get('lens'))} | {p.get('direction')} | "
                     f"{r.get('reviewerFamily', '—')}: {(r.get('summary') or '—')[:120]} | {r.get('score', '—')} | {outcome} |")
    lemmas: dict[str, dict] = {}
    for e in store.events("program.lemma"):
        lemmas[e["payload"]["id"]] = {**e["payload"], "history": []}
    for e in store.events("lemma.status"):
        if e["payload"]["id"] in lemmas:
            lemmas[e["payload"]["id"]]["history"].append(e["payload"])
    rever = {e["payload"]["id"]: e["payload"]["verified"] for e in store.events("lemma.reverified")}
    if lemmas:
        lines += ["", "### Lemmas", "", "| Lemma | Statement | Final status | Attempts | Clean recompile |", "| --- | --- | --- | --- | --- |"]
        for lid, l in lemmas.items():
            final = next((h["status"] for h in reversed(l["history"]) if h["status"] in ("verified", "refuted")), "open")
            tries = ", ".join(f"r{h['round']} {h['family']}: {h['status']}" for h in l["history"])
            lines.append(f"| {lid}{' (repair of ' + l['repairOf'] + ')' if l.get('repairOf') else ''} | `{l.get('lean') or l['statement']}` | "
                         f"{final} | {tries or '—'} | {'yes' if rever.get(lid) else ('no' if lid in rever else '—')} |")
    syn = store.events("synthesis.done") or store.events("synthesis.skipped")
    if syn:
        p = syn[-1]["payload"]
        lines += ["", f"Synthesis of the main theorem: {'succeeded' if p.get('solved') else 'not achieved'}"
                  + (f" ({p['reason']})" if p.get("reason") else "") + "."]
    return lines
