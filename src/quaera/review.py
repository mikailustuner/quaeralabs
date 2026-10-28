"""Systematic review and meta-analysis loop (review domain).

Differences from the ML loop:
- The protocol (eligibility criteria, outcome, effect measure) is locked in the preregistration before any study is screened.
- Screening: the Literature agent judges candidates against the locked criteria; only DOIs that Crossref/DataCite confirm are included.
- Extraction: the Analyst copies each study's numbers together with a verbatim quote. The code accepts a row only if the quote is in
  the source text that was fetched (Europe PMC full text or an abstract) and every number is in the quote; the model's own effect
  sizes are never used, the code computes them from the raw numbers.
- Analysis: DerSimonian–Laird random-effects meta-analysis computed in code; fewer than two traceable studies is inconclusive.
- Verification: the Verifier fetches every source again, re-checks every quote and number and recomputes the pooled estimate.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata

from . import prompts
from .ml_loop import MLOrchestrator, compare
from .orchestrator import StopResearch
from .store import now

REVIEW_STAGES = [
    "literature", "hypothesis", "hypothesis_approval", "design", "plan_review", "preregistration", "experiment_approval",
    "screening", "extraction", "analysis", "critique", "verification", "conclude", "report",
]
MEASURES = ("SMD", "MD", "OR", "RR")
RATIO = ("OR", "RR")
KINDS = {"SMD": ("means", "effect", "smd_n"), "MD": ("means", "effect"), "OR": ("events", "effect"), "RR": ("events", "effect")}
FIELDS = {"means": ("mean1", "sd1", "n1", "mean2", "sd2", "n2"), "events": ("events1", "n1", "events2", "n2"),
          "effect": ("estimate", "ci_low", "ci_high"), "smd_n": ("estimate", "n1", "n2")}
Z95 = 1.959964
MAX_STUDIES = max(2, int(os.environ.get("QUAERA_REVIEW_MAX_STUDIES", "12")))
SOURCE_CHARS = 30000      # source text given to the extracting model
MIN_K = 2                 # a pooled estimate needs at least this many traceable studies
# Written by the code, not the designer: the preregistration states exactly what is computed (a live run's designer promised
# risk-of-bias, sensitivity and prediction-interval analyses that nothing runs).
ANALYSIS_PLAN = ("DerSimonian–Laird random-effects meta-analysis computed in code: effect sizes from the extracted raw numbers, "
                 "pooled estimate with a Wald 95% CI, p, τ², Q and I². Studies whose numbers cannot be traced to a verbatim quote "
                 f"in the source are listed and left out; fewer than {MIN_K} traceable studies give no pooled estimate.")


# --- source text checks ------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """For quote matching: Unicode compatibility form, one kind of dash and minus, no soft hyphens, single spaces, lowercase."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = re.sub(r"[‐-―−﹣－]", "-", text)
    text = re.sub(r"[‘’‛]", "'", re.sub(r"[“”‟]", '"', text))
    return " ".join(text.split()).lower()


NUMBER = re.compile(r"(?<![\w.])(?<![a-z]-)-?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?(?![\w])")


def numbers_in(text: str) -> list[float]:
    """Every number written in the text: 1,234 → 1234, .45 → 0.45, −0.3 → -0.3 (after normalize_text)."""
    out = []
    for m in NUMBER.finditer(normalize_text(text)):
        token = m.group(0)
        if not re.search(r"\d", token):
            continue
        out.append(float(token.replace(",", "")))
    return out


def check_extraction(source: str, row: dict, measure: str) -> tuple[dict | None, list[str]]:
    """A model's extraction row → ({"yi", "vi"} computed by code, problems). The row counts only without problems."""
    problems = []
    kind = row.get("kind")
    if kind not in KINDS[measure]:
        return None, [f"data kind '{kind}' does not fit the effect measure {measure} (allowed: {', '.join(KINDS[measure])})"]
    values = {}
    for f in FIELDS[kind]:
        try:
            values[f] = float(row[f])
        except (KeyError, TypeError, ValueError):
            problems.append(f"missing or non-numeric field {f}")
    quote = (row.get("quote") or "").strip()
    if len(quote) < 15:
        problems.append("no verbatim quote")
    elif normalize_text(quote) not in normalize_text(source):
        problems.append("the quote is not in the source text")
    if problems:
        return None, problems
    written = numbers_in(quote)
    for f, v in values.items():
        if not any(abs(w - v) <= 1e-9 * max(1.0, abs(v)) for w in written):
            problems.append(f"{f}={v:g} is not written in the quote")
    if problems:
        return None, problems
    try:
        es = effect(measure, kind, values)
    except ValueError as exc:
        return None, [str(exc)]
    if row.get("reversed") is True and kind in ("effect", "smd_n"):   # reported as group 2 vs group 1: the code flips the sign
        es["yi"] = -es["yi"]
    return es, []


def effect(measure: str, kind: str, v: dict) -> dict:
    """Effect size yi and its sampling variance vi on the analysis scale (log scale for OR and RR)."""
    if kind == "effect":
        est, lo, hi = v["estimate"], v["ci_low"], v["ci_high"]
        if not lo <= est <= hi or lo == hi:
            raise ValueError(f"the interval [{lo:g}, {hi:g}] does not contain the estimate {est:g}")
        if measure in RATIO:
            if lo <= 0:
                raise ValueError("a ratio and its interval must be positive")
            est, lo, hi = math.log(est), math.log(lo), math.log(hi)
        se = (hi - lo) / (2 * Z95)
        return {"yi": est, "vi": se * se}
    if kind == "smd_n":          # a reported standardized mean difference with the group sizes (Borenstein et al. 2009, eq. 4.20)
        g, n1, n2 = v["estimate"], v["n1"], v["n2"]
        if min(n1, n2) < 2:
            raise ValueError("group sizes must be at least 2")
        return {"yi": g, "vi": (n1 + n2) / (n1 * n2) + g * g / (2 * (n1 + n2))}
    if kind == "means":
        n1, n2, sd1, sd2 = v["n1"], v["n2"], v["sd1"], v["sd2"]
        if min(n1, n2) < 2 or min(sd1, sd2) <= 0:
            raise ValueError("group sizes must be at least 2 and standard deviations positive")
        if measure == "MD":
            return {"yi": v["mean1"] - v["mean2"], "vi": sd1 * sd1 / n1 + sd2 * sd2 / n2}
        sp = math.sqrt(((n1 - 1) * sd1 * sd1 + (n2 - 1) * sd2 * sd2) / (n1 + n2 - 2))
        g = (1 - 3 / (4 * (n1 + n2) - 9)) * (v["mean1"] - v["mean2"]) / sp     # Hedges' g
        return {"yi": g, "vi": (n1 + n2) / (n1 * n2) + g * g / (2 * (n1 + n2))}
    a, n1, c, n2 = v["events1"], v["n1"], v["events2"], v["n2"]
    if not (0 <= a <= n1 and 0 <= c <= n2) or min(n1, n2) < 1:
        raise ValueError("event counts must lie between 0 and the group size")
    if (a == 0 and c == 0) or (a == n1 and c == n2):
        raise ValueError("no events (or only events) in both groups: the ratio cannot be estimated")
    if 0 in (a, c, n1 - a, n2 - c):          # continuity correction for a zero cell
        a, c, n1, n2 = a + 0.5, c + 0.5, n1 + 1, n2 + 1
    b, d = n1 - a, n2 - c
    if measure == "OR":
        return {"yi": math.log(a * d / (b * c)), "vi": 1 / a + 1 / b + 1 / c + 1 / d}
    return {"yi": math.log((a / n1) / (c / n2)), "vi": 1 / a - 1 / n1 + 1 / c - 1 / n2}


def random_effects(rows: list[dict], measure: str) -> dict:
    """DerSimonian–Laird random-effects pooling of rows with yi and vi. Fewer than MIN_K rows: no pooled estimate."""
    k = len(rows)
    if k < MIN_K:
        return {"k": k}
    y, w = [r["yi"] for r in rows], [1 / r["vi"] for r in rows]
    sw = sum(w)
    fixed = sum(wi * yi for wi, yi in zip(w, y)) / sw
    q = sum(wi * (yi - fixed) ** 2 for wi, yi in zip(w, y))
    tau2 = max(0.0, (q - (k - 1)) / (sw - sum(wi * wi for wi in w) / sw))
    ws = [1 / (r["vi"] + tau2) for r in rows]
    mu = sum(wi * yi for wi, yi in zip(ws, y)) / sum(ws)
    se = math.sqrt(1 / sum(ws))
    z = mu / se
    out = {"k": k, "pooled_effect": mu, "se": se, "ci_low": mu - Z95 * se, "ci_high": mu + Z95 * se, "tau2": tau2,
           "q": q, "i2": max(0.0, (q - (k - 1)) / q) if q > 0 else 0.0, "p_value": math.erfc(abs(z) / math.sqrt(2))}
    if measure in RATIO:
        out.update(pooled_ratio=math.exp(mu), ratio_ci_low=math.exp(out["ci_low"]), ratio_ci_high=math.exp(out["ci_high"]))
    return {key: round(val, 6) if isinstance(val, float) else val for key, val in out.items()}


# --- orchestrator -------------------------------------------------------------------------

class ReviewOrchestrator(MLOrchestrator):
    stages = REVIEW_STAGES
    critic_prompt = prompts.CRITIC_REVIEW

    def compute_label(self) -> str:
        return "no compute: literature services and code"

    def _design(self, feedback: str = "") -> tuple[dict, dict]:
        h, q = self.selected_hypothesis(), self.question()
        plan, actor = self.ask_json("experiment_designer", prompts.DESIGN_REVIEW,
                                   f"Question: {q['title']}\nScope: {q['scope']}\nHypothesis: {h['statement']}\n{feedback}", 3000)
        if plan.get("effectMeasure") not in MEASURES:
            plan["effectMeasure"] = "SMD"
        plan["seeds"] = 1
        plan["primaryMetric"] = "pooled_ratio" if plan["effectMeasure"] in RATIO else "pooled_effect"
        # The locked analysis plan carries the protocol, so changing the criteria after screening breaks the preregistration hash.
        crit = plan.get("eligibility") or {}
        plan["analysisPlan"] = (f"{ANALYSIS_PLAN}\n"
                                f"Effect measure: {plan['effectMeasure']}. Outcome: {plan.get('outcome', '—')}.\n"
                                f"Include: {'; '.join(crit.get('include') or ['—'])}.\nExclude: {'; '.join(crit.get('exclude') or ['—'])}.")
        return plan, actor

    def _put_experiment(self, plan: dict, actor: dict, prev: dict | None = None) -> dict:
        h, lit = self.selected_hypothesis(), self.state("literature")
        direction = plan.get("direction") if plan.get("direction") in ("higher_is_better", "lower_is_better") else "binary"
        return self.store.put({**(prev or {}), "type": "experiment", "createdBy": actor, "hypothesisIds": [h["id"]], "domain": "review",
                               "method": plan.get("method") or "Systematic review and random-effects meta-analysis.",
                               "metrics": [{"name": plan["primaryMetric"], "direction": direction}],
                               "environment": {"runtime": "quaera.review (DerSimonian–Laird random effects, computed in code)"},
                               "budget": {"estimatedUsd": round(self.gateway.remaining(), 2)},
                               "noveltyCheck": self.novelty_check(lit), "status": "draft"})

    def _measure(self) -> str:
        return self.state("plan")["effectMeasure"]

    # stages ---------------------------------------------------------------
    def stage_screening(self) -> None:
        plan, actor = self.state("plan"), self.det("literature")
        queries = plan.get("searchQueries") or {}
        found: dict[str, dict] = {}
        hits, errors, open_access = 0, [], {}
        for tool, key in (("openalex.search", "openalex"), ("europepmc.search", "europepmc")):
            for query in [x for x in queries.get(key, []) if isinstance(x, str) and x.strip()][:4]:
                try:
                    res = self.tools.call_json("literature", tool, query=query, max_results=20)
                except Exception as exc:     # a service that is down is recorded; screening continues with the others
                    res = {"error": str(exc)[:200]}
                if isinstance(res, dict):
                    errors.append({"tool": tool, "query": query, "error": res.get("error")})
                    continue
                for r in res:
                    hits += 1
                    if r.get("doi"):
                        c = found.setdefault(r["doi"].lower(), {"id": r["doi"].lower(), "title": r.get("title"), "year": r.get("year"),
                                                                "summary": (r.get("summary") or "")[:700]})
                        open_access[c["id"]] = open_access.get(c["id"]) or bool(r.get("openAccess"))
        for e in errors:
            self.store.append("tool.error", actor, e)
        crit = plan["analysisPlan"]
        decisions, cands = [], [{**c, "openAccessFullText": open_access.get(c["id"], False)} for c in found.values()]
        for i in range(0, len(cands), 20):
            out, screener = self.ask_json("literature", prompts.SCREEN,
                                          f"Review question: {self.question()['title']}\nLocked protocol:\n{crit}\n\n"
                                          f"Candidates:\n{json.dumps(cands[i:i + 20], ensure_ascii=False)}", 3000)
            for d in out.get("decisions", []):
                if isinstance(d, dict) and str(d.get("id", "")).lower() in found:
                    decisions.append({"id": str(d["id"]).lower(), "include": bool(d.get("include")), "reason": str(d.get("reason", ""))[:300]})
        # Over the study limit, open-access full texts come first: abstracts rarely report the numbers a pooled estimate needs.
        chosen = sorted(dict.fromkeys(d["id"] for d in decisions if d["include"]), key=lambda doi: not open_access.get(doi))
        included, rejected = [], []
        for doi in chosen[:MAX_STUDIES]:
            status = self.tools.call("literature", "crossref.lookup", doi=doi)
            if status != "real":
                rejected.append({"ref": doi, "reason": status})
                self.store.append("citation.rejected", actor, {"ref": doi, "reason": status})
                continue
            art = self.store.put({"type": "artifact", "createdBy": actor, "kind": "paper", "uri": doi, "provider": "doi",
                                  "citationVerified": True})
            included.append({"ref": doi, "artifactId": art["id"], "title": found[doi]["title"]})
        self.set_state("screening", {"identified": hits, "unique": len(found), "screened": len({d["id"] for d in decisions}),
                                     "excluded": len({d["id"] for d in decisions if not d["include"]}),
                                     "overLimit": max(0, len(chosen) - MAX_STUDIES), "notVerified": rejected,
                                     "included": included, "decisions": decisions})
        if not included:
            raise StopResearch("screening found no eligible study with a verified DOI")

    def _fetch(self, role: str, ref: str) -> dict:
        try:
            return self.tools.call_json(role, "source.text", ref=ref)
        except Exception as exc:
            return {"error": str(exc)[:200]}

    def stage_extraction(self) -> None:
        """The pilot of a review: can the numbers of the included studies be traced to their sources?"""
        plan, measure = self.state("plan"), self._measure()
        started, rows = now(), []
        analyst = self.det("analyst")
        for study in self.state("screening")["included"]:
            src = self._fetch("literature", study["ref"])
            row = {"ref": study["ref"], "artifactId": study["artifactId"]}
            if not src.get("text"):
                rows.append({**row, "ok": False, "problems": [f"source text not available: {src.get('error', 'empty')}"]})
                continue
            digest = self.store.put_blob(src["text"].encode())
            art = self.store.get(study["artifactId"])
            self.store.put({**art, "sha256": digest, "mediaType": "text/plain"}, by=art["createdBy"])
            out, analyst = self.ask_json("analyst", prompts.EXTRACT,
                                         f"Outcome: {plan.get('outcome', '—')}\nEffect measure: {measure} (allowed data kinds: "
                                         f"{', '.join(KINDS[measure])})\nTreatment/comparison as defined by the protocol:\n{plan['analysisPlan']}\n\n"
                                         f"Source ({src['from']}, {study['ref']}):\n{src['text'][:SOURCE_CHARS]}", 2000)
            data = {k: out.get(k) for k in ("kind", "quote", "reversed", *{f for fs in FIELDS.values() for f in fs}) if out.get(k) is not None}
            row.update(sha256=digest, source=src["from"], data=data)
            if not out.get("usable"):
                rows.append({**row, "ok": False, "problems": [f"not usable: {str(out.get('reason', ''))[:200]}"]})
                continue
            es, problems = check_extraction(src["text"], data, measure)
            rows.append({**row, "ok": es is not None, "problems": problems, **(es or {})})
        ok = [r for r in rows if r["ok"]]
        digest = self.store.put_blob(json.dumps(rows, ensure_ascii=False, indent=1).encode())
        log = self.store.put({"type": "artifact", "createdBy": analyst, "kind": "log", "uri": f"cas:{digest}", "provider": "local",
                              "sha256": digest, "mediaType": "application/json"})
        self.store.put({"type": "run", "createdBy": self.det("engineer"), "experimentId": self.state("experiment_id"), "kind": "pilot",
                        "status": "succeeded" if ok else "failed", "seed": 0, "config": {"step": "extraction", "measure": measure},
                        "hardware": self.compute_label(), "startedAt": started, "endedAt": now(), "logs": log["id"],
                        "metrics": {"included": len(rows), "traceable": len(ok)}})
        self.set_state("extractions", rows)
        if not ok:
            raise StopResearch("no included study's numbers could be traced to its source text")

    def _pooled_run(self, actor: dict, kind: str, rows: list[dict], started: str) -> dict:
        measure = self._measure()
        meta = random_effects(rows, measure)
        config = {"model": "DerSimonian–Laird random effects", "measure": measure,
                  "studies": [{"ref": r["ref"], "yi": round(r["yi"], 6), "vi": round(r["vi"], 6)} for r in rows]}
        return self.store.put({"type": "run", "createdBy": actor, "experimentId": self.state("experiment_id"), "kind": kind,
                               "status": "succeeded" if meta["k"] >= MIN_K else "failed", "seed": 0, "config": config,
                               "hardware": self.compute_label(), "startedAt": started, "endedAt": now(),
                               "metrics": {k: float(v) for k, v in meta.items()}})

    def stage_analysis(self) -> None:
        rows = [r for r in self.state("extractions") if r["ok"]]
        run = self._pooled_run(self.det("engineer"), "full", rows, now())     # the code computes; no model is involved
        self.set_state("full_runs", [run["id"]])
        pre, table, ok = self._analysis_input()
        if run["metrics"]["k"] < MIN_K:
            out, analyst = {"relation": "inconclusive", "negative": True,
                            "summary": f"Only {len(rows)} study could be traced to its source; a pooled estimate needs at least {MIN_K}.",
                            "limitations": ["Too few studies with traceable numbers."]}, self.det("analyst")
            ok = [run]
        else:
            out, analyst = self.ask_json("analyst", prompts.ANALYST,
                                         f"Pre-registration:\n{json.dumps({k: pre[k] for k in ('primaryMetric', 'successCriterion', 'analysisPlan')}, ensure_ascii=False)}\n"
                                         f"Meta-analysis computed by code ({run['metrics']['k']:.0f} studies): {json.dumps(run['metrics'])}\n"
                                         f"Per-study effects (analysis scale): {json.dumps(run['config']['studies'])}", 2000)
        self._write_result(analyst, out, ok, table)

    def _analysis_input(self) -> tuple[dict, dict, list[dict]]:
        runs = [self.store.get(i) for i in self.state("full_runs")]
        ok = [r for r in runs if r["status"] == "succeeded"]
        m = runs[0]["metrics"]
        table = {}
        if "pooled_effect" in m:
            ratio = self._measure() in RATIO
            table[self.state("plan")["primaryMetric"]] = {
                "n": int(m["k"]), "mean": m["pooled_ratio" if ratio else "pooled_effect"], "std": m["se"],
                "ci95": [m["ratio_ci_low"], m["ratio_ci_high"]] if ratio else [m["ci_low"], m["ci_high"]]}
            table["i2"] = {"n": int(m["k"]), "mean": m["i2"], "std": 0.0, "ci95": None}
            table["tau2"] = {"n": int(m["k"]), "mean": m["tau2"], "std": 0.0, "ci95": None}
        return self.store.latest("preregistration")[-1], table, ok

    def _write_result(self, analyst: dict, out: dict, ok: list[dict], table: dict, prev: dict | None = None) -> dict:
        if not table:          # the code decides: without a pooled estimate nothing is supported or refuted
            out = {**out, "relation": "inconclusive"}
        return super()._write_result(analyst, out, ok or [self.store.get(self.state("full_runs")[0])], table, prev)

    def stage_verification(self) -> None:
        """Every source is fetched again; quotes and numbers are re-checked against the fresh text and the pooled estimate recomputed."""
        verifier, measure, started = self.det("verifier"), self._measure(), now()
        rows, diffs = [], []
        for row in self.state("extractions"):
            if not row["ok"]:
                continue
            src = self._fetch("verifier", row["ref"])
            if not src.get("text"):
                diffs.append(f"{row['ref']}: source not available again ({src.get('error', 'empty')})")
                continue
            if self.store.put_blob(src["text"].encode()) != row["sha256"]:
                diffs.append(f"{row['ref']}: the source text changed since extraction")
            es, problems = check_extraction(src["text"], row["data"], measure)
            if es is None:
                diffs.append(f"{row['ref']}: {'; '.join(problems)}")
                continue
            rows.append({**row, **es})
        run = self._pooled_run(verifier, "verification", rows, started)
        ref = self.store.get(self.state("full_runs")[0])
        reproduced, metric_diffs = compare(ref["metrics"], run["metrics"])
        if ref["metrics"]["k"] < MIN_K:
            reproduced = "no"
            diffs.append("no pooled estimate to reproduce")
        self.store.put({"type": "verification", "createdBy": verifier, "resultId": self.state("result_id"),
                        "verificationRunIds": [run["id"]], "reproduced": reproduced, "crossModel": False,
                        "tolerance": "exact (|diff| ≤ 1e-9); partial up to 1%", "differences": (diffs + metric_diffs)[:10]})


def recheck(store) -> dict:
    """`quaera verify` for a review project: fetches the sources again and recomputes the pooled estimate."""
    from .mcp_servers.literature_server import source_text
    states = {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}
    measure = states["plan"]["effectMeasure"]
    rows, problems = [], []
    for row in (r for r in states.get("extractions", []) if r["ok"]):
        src = json.loads(source_text(row["ref"]))
        es, why = check_extraction(src["text"], row["data"], measure) if src.get("text") else (None, [src.get("error", "no text")])
        if es is None:
            problems.append(f"{row['ref']}: {'; '.join(why)}")
        else:
            rows.append({**row, **es})
    recorded = next(r for r in store.latest("run") if r["kind"] == "full")["metrics"]
    meta = random_effects(rows, measure)
    reproduced, diffs = compare(recorded, {k: float(v) for k, v in meta.items()})
    return {"reproduced": reproduced, "studies": len(rows), "problems": problems, "differences": diffs, "pooled": meta}
