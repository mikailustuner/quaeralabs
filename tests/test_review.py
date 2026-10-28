"""Review domain: effect sizes and pooling against a published reference, the quote and number checks, and the whole loop
with scripted models and scripted literature services."""

import json

import pytest

from quaera import prompts
from quaera.gateway import Gateway, ScriptedProvider
from quaera.orchestrator import AutoApprover
from quaera.permissions import Permissions
from quaera.review import ReviewOrchestrator, check_extraction, effect, numbers_in, random_effects
from quaera.store import Store

# BCG vaccine trials (Colditz et al. 1994; metafor's dat.bcg): treated positive/negative, control positive/negative.
BCG = [(4, 119, 11, 128), (6, 300, 29, 274), (3, 228, 11, 209), (62, 13536, 248, 12619), (33, 5036, 47, 5761),
       (180, 1361, 372, 1079), (8, 2537, 10, 619), (505, 87886, 499, 87892), (29, 7470, 45, 7232), (17, 1699, 65, 1600),
       (186, 50448, 141, 27197), (5, 2493, 3, 2338), (27, 16886, 29, 17825)]


def test_dersimonian_laird_matches_metafor():
    # metafor: rma(measure="RR", ai=tpos, bi=tneg, ci=cpos, di=cneg, data=dat.bcg, method="DL")
    rows = [effect("RR", "events", {"events1": a, "n1": a + b, "events2": c, "n2": c + d}) for a, b, c, d in BCG]
    m = random_effects(rows, "RR")
    assert m["k"] == 13
    assert m["pooled_effect"] == pytest.approx(-0.7141, abs=1e-4)
    assert m["se"] == pytest.approx(0.1787, abs=1e-4)
    assert (m["ci_low"], m["ci_high"]) == (pytest.approx(-1.0644, abs=1e-4), pytest.approx(-0.3638, abs=1e-4))
    assert m["tau2"] == pytest.approx(0.3088, abs=1e-4)
    assert m["q"] == pytest.approx(152.2330, abs=1e-3)
    assert m["i2"] == pytest.approx(0.9212, abs=1e-4)
    assert m["pooled_ratio"] == pytest.approx(0.4896, abs=1e-4)


def test_one_study_gives_no_pooled_estimate():
    assert random_effects([{"yi": 0.3, "vi": 0.04}], "SMD") == {"k": 1}


def test_effect_sizes():
    g = effect("SMD", "means", {"mean1": 12, "sd1": 4, "n1": 20, "mean2": 10, "sd2": 4, "n2": 20})
    assert g["yi"] == pytest.approx(0.5 * (1 - 3 / 151))                       # d = 0.5, Hedges' correction
    assert g["vi"] == pytest.approx(40 / 400 + g["yi"] ** 2 / 80)
    md = effect("MD", "means", {"mean1": 12, "sd1": 4, "n1": 20, "mean2": 10, "sd2": 2, "n2": 10})
    assert (md["yi"], md["vi"]) == (pytest.approx(2.0), pytest.approx(16 / 20 + 4 / 10))
    zero = effect("OR", "events", {"events1": 0, "n1": 10, "events2": 3, "n2": 10})    # 0.5 continuity correction
    assert zero["yi"] == pytest.approx(__import__("math").log((0.5 * 7.5) / (10.5 * 3.5)))
    gn = effect("SMD", "smd_n", {"estimate": 0.66, "n1": 35, "n2": 31})
    assert gn["vi"] == pytest.approx(66 / (35 * 31) + 0.66 ** 2 / 132)
    ci = effect("OR", "effect", {"estimate": 0.5, "ci_low": 0.25, "ci_high": 1.0})
    assert ci["yi"] == pytest.approx(__import__("math").log(0.5))
    with pytest.raises(ValueError):
        effect("OR", "events", {"events1": 0, "n1": 10, "events2": 0, "n2": 12})
    with pytest.raises(ValueError):
        effect("MD", "effect", {"estimate": 3, "ci_low": 0.1, "ci_high": 2})


def test_numbers_in_text():
    assert numbers_in("n = 1,204 patients; OR .45 (95% CI −0.8 to −0.1), p<0.001.") == [1204, 0.45, 95, -0.8, -0.1, 0.001]
    assert numbers_in("range 1.2-3.4 and COVID-19") == [1.2, 3.4]


SOURCE = ("Results: In the intervention group (n = 40) the mean score was 14.2 (SD 3.1) versus 11.8 (SD 3.4) in the control "
          "group (n = 38). Adverse events were rare.")


def test_extraction_needs_the_quote_and_every_number():
    row = {"kind": "means", "quote": "the mean score was 14.2 (SD 3.1) versus 11.8 (SD 3.4) in the control group (n = 38)",
           "mean1": 14.2, "sd1": 3.1, "n1": 40, "mean2": 11.8, "sd2": 3.4, "n2": 38}
    es, problems = check_extraction(SOURCE, row, "SMD")
    assert es is None and problems == ["n1=40 is not written in the quote"]
    row["quote"] = "intervention group (n = 40) the mean score was 14.2 (SD 3.1) versus 11.8 (SD 3.4) in the control group (n = 38)"
    es, problems = check_extraction(SOURCE, row, "SMD")
    assert problems == [] and es["yi"] > 0
    invented = {**row, "quote": "intervention group (n = 40) the mean score was 15.2 (SD 3.1) versus 11.8 (SD 3.4) in the control group (n = 38)"}
    assert check_extraction(SOURCE, invented, "SMD")[1] == ["the quote is not in the source text"]
    assert "does not fit" in check_extraction(SOURCE, row, "OR")[1][0]
    g = {"kind": "smd_n", "quote": "Hedges' g = 0.66 favouring exercise (n = 35 vs n = 31)", "estimate": 0.66, "n1": 35, "n2": 31}
    text = "Depression improved more with exercise: Hedges' g = 0.66 favouring exercise (n = 35 vs n = 31)."
    assert check_extraction(text, g, "SMD")[0]["yi"] == pytest.approx(0.66)
    assert check_extraction(text, {**g, "reversed": True}, "SMD")[0]["yi"] == pytest.approx(-0.66)


# --- the loop ------------------------------------------------------------------------------

TEXTS = {
    "10.1/a": "Trial A. Exercise group (n = 30): depression score 10.0 (SD 4.0); control group (n = 30): 13.0 (SD 4.0).",
    "10.1/b": "Trial B. Exercise arm (n = 25) scored 11.5 (SD 5.0) and controls (n = 25) scored 13.5 (SD 5.0) at 12 weeks.",
    "10.1/c": "Trial C. Mean scores were 9.0 (SD 3.0) with exercise (n = 40) and 12.0 (SD 3.0) with usual care (n = 40).",
}
GOOD = {
    "10.1/a": {"quote": "Exercise group (n = 30): depression score 10.0 (SD 4.0); control group (n = 30): 13.0 (SD 4.0)",
               "mean1": 10.0, "sd1": 4.0, "n1": 30, "mean2": 13.0, "sd2": 4.0, "n2": 30},
    "10.1/b": {"quote": "Exercise arm (n = 25) scored 11.5 (SD 5.0) and controls (n = 25) scored 13.5 (SD 5.0)",
               "mean1": 11.5, "sd1": 5.0, "n1": 25, "mean2": 13.5, "sd2": 5.0, "n2": 25},
    # an invented number: the source says 9.0, the model writes 8.0
    "10.1/c": {"quote": "Mean scores were 8.0 (SD 3.0) with exercise (n = 40) and 12.0 (SD 3.0) with usual care (n = 40)",
               "mean1": 8.0, "sd1": 3.0, "n1": 40, "mean2": 12.0, "sd2": 3.0, "n2": 40},
}


class LiteratureTools:
    """The literature tools a review uses, answered from TEXTS; permission checks are real."""

    def __init__(self, permissions, changed: dict | None = None):
        self.permissions, self.changed, self.calls = permissions, changed or {}, []

    def call(self, role, tool, **a):
        self.permissions.check_tool(role, tool)
        self.calls.append((role, tool))
        if tool in ("arxiv.search", "openalex.search", "s2.search"):
            return "[]"
        if tool == "europepmc.search":
            return json.dumps([{"doi": d.upper(), "title": f"Trial {d[-1].upper()}", "year": 2020, "summary": t} for d, t in TEXTS.items()]
                              + [{"doi": "10.1/review", "title": "A narrative review", "year": 2021, "summary": "Review."}])
        if tool == "crossref.lookup":
            return "real"
        if tool == "source.text":
            text = self.changed.get(a["ref"], TEXTS[a["ref"]]) if role == "verifier" else TEXTS[a["ref"]]
            return json.dumps({"text": text, "from": "europepmc_abstract"})
        raise AssertionError(tool)

    def call_json(self, role, tool, **a):
        return json.loads(self.call(role, tool, **a))

    def close(self):
        pass


def script(system, prompt):
    key = next(k for k in ("LITERATURE_PLAN", "LITERATURE_REPLAN", "LITERATURE_SUMMARY", "HYPOTHESIS_REVIEW", "RANK_HYPOTHESES",
                           "DESIGN_REVIEW", "CRITIC_REVIEW", "SCREEN", "EXTRACT", "ANALYST", "WRITER") if getattr(prompts, k) == system)
    if key == "LITERATURE_PLAN":
        return '{"arxiv_queries": [], "openalex_queries": []}'
    if key == "LITERATURE_REPLAN":
        return '{"arxiv_queries": [], "openalex_queries": []}'
    if key == "LITERATURE_SUMMARY":
        return '{"summary": "Nothing relevant.", "verdict": "unknown", "basis": "search", "cited": []}'
    if key == "HYPOTHESIS_REVIEW":
        return json.dumps({"hypotheses": [{"statement": "Exercise lowers depression scores compared with usual care in adults.",
                                           "falsifiabilityNote": "Refuted if the pooled SMD CI lies above 0.", "scope": {"relation": "full", "note": ""}}]})
    if key == "RANK_HYPOTHESES":
        return '{"ranking": [{"index": 0, "testability": 8, "plausibility": 7, "novelty": 3, "scope": 9, "cost": 8, "reason": "Testable."}]}'
    if key == "DESIGN_REVIEW":
        return json.dumps({"method": "Search, screen, extract and pool the randomized trials.",
                           "eligibility": {"include": ["randomized trials of exercise in adults"], "exclude": ["reviews"]},
                           "outcome": "depression score; group 1 exercise, group 2 control", "effectMeasure": "SMD",
                           "searchQueries": {"openalex": ["exercise depression trial"], "europepmc": ["exercise depression randomized"]},
                           "successCriterion": "Supported if the pooled SMD's 95% CI lies entirely below 0.",
                           "analysisPlan": "Random-effects pooling.", "direction": "lower_is_better"})
    if key == "CRITIC_REVIEW":
        return '{"flawed": false, "categories": [], "severity": null, "evidence": "", "summary": "Sound."}'
    if key == "SCREEN":
        return json.dumps({"decisions": [{"id": d, "include": True, "reason": "Trial."} for d in TEXTS]
                          + [{"id": "10.1/review", "include": False, "reason": "A review."}]})
    if key == "EXTRACT":
        ref = next(d for d in TEXTS if d in prompt)
        return json.dumps({"usable": True, "reason": "", "kind": "means", **GOOD[ref]})
    if key == "ANALYST":
        return json.dumps({"relation": "supports", "summary": "The pooled SMD is below 0.", "negative": False, "limitations": ["Two trials."]})
    if key == "WRITER":
        return json.dumps({"discussion": ["Two trials were pooled [RES-0001]."], "answer": "yes", "answerReason": "Pooled SMD < 0 [RES-0001, VER-0001]."})


def make(tmp_path, tools_cls=LiteratureTools, **kw):
    perms = Permissions.load()
    store = Store(tmp_path / "p" / "quaera.db", perms)
    store.set_meta("title", "Exercise and depression"); store.set_meta("domain", "review")
    gw = Gateway({"scripted": ScriptedProvider(script)}, 5.0, perms.agents)
    orch = ReviewOrchestrator(store, gw, tools_cls(perms, **kw), AutoApprover(100), perms, log=lambda m: None, reports_dir=tmp_path / "p")
    store.put({"type": "question", "createdBy": orch.human(), "title": "Does exercise reduce depressive symptoms in adults?",
               "domain": "review", "scope": "Randomized trials in adults."})
    return orch


def test_review_loop_pools_only_traceable_studies(tmp_path):
    orch = make(tmp_path)
    report = orch.run()
    store = orch.store
    rows = orch.state("extractions")
    assert [r["ok"] for r in rows] == [True, True, False]
    assert rows[2]["problems"] == ["the quote is not in the source text"]
    full = next(r for r in store.latest("run") if r["kind"] == "full")
    assert full["metrics"]["k"] == 2 and full["metrics"]["ci_high"] < 0
    scr = orch.state("screening")
    assert (scr["unique"], scr["excluded"], len(scr["included"])) == (4, 1, 3)
    pre = store.latest("preregistration")[-1]
    assert "Effect measure: SMD" in pre["analysisPlan"] and "Include: randomized trials" in pre["analysisPlan"]
    assert pre["analysisPlan"].startswith("DerSimonian–Laird") and "Random-effects pooling." not in pre["analysisPlan"]
    assert store.latest("verification")[-1]["reproduced"] == "yes"
    assert orch.selected_hypothesis()["status"] == "supported"
    assert all(a.get("sha256") for a in store.latest("artifact") if a["kind"] == "paper")
    assert ("verifier", "source.text") in orch.tools.calls
    assert store.check_final() == []
    text = open(report, encoding="utf-8").read()
    assert "## Screening (PRISMA)" in text and "Pooled SMD" in text and "re-read every source" in text


def test_verifier_notices_a_source_that_changed(tmp_path):
    changed = {"10.1/b": TEXTS["10.1/b"].replace("11.5", "12.5")}
    orch = make(tmp_path, changed=changed)
    orch.run()
    ver = orch.store.latest("verification")[-1]
    assert ver["reproduced"] != "yes"
    assert any("10.1/b" in d and "changed" in d for d in ver["differences"])
    assert orch.selected_hypothesis()["status"] == "inconclusive"


def test_full_text_puts_results_and_tables_first():
    from quaera.mcp_servers.literature_server import _plain
    xml = ("<article><front><abstract><p>Abstract text.</p></abstract></front><body>"
           "<sec><title>Introduction</title><p>Long background.</p></sec>"
           "<sec sec-type='results'><title>Results</title><p>Scores fell to 10.0 (SD 4.0).</p></sec></body>"
           "<floats-group><table-wrap><caption><p>Table 1</p></caption><table><tr><td>BDI</td><td>10.0</td></tr></table></table-wrap>"
           "</floats-group></article>")
    assert _plain(xml).splitlines() == ["Abstract text.", "Results", "Scores fell to 10.0 (SD 4.0).", "Table 1", "BDI 10.0",
                                        "Introduction", "Long background."]
