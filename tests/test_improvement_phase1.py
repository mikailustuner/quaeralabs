"""Improvement plan phase 1 (2026-10-20): literature verdict rule (B1), Mathlib probe (B2),
project status from the event log (D2). No model calls, no real Lean."""

import json

from starlette.testclient import TestClient

import quaera.server as server
from quaera import contracts
from quaera.literature import decide
from quaera.orchestrator import mathlib_decl, suggested_term

from test_orchestrator import FakeTools, Script, make
from test_server import home  # noqa: F401  (fixture)


# --- B1: the code decides what may be claimed ------------------------------------------------------------------------

def test_novel_without_a_verified_source_becomes_unknown():
    verdict, basis, reason = decide("novel", "search", 0, searched=16, service_down=False)
    assert (verdict, basis) == ("unknown", "search") and "none of the 16 search results" in reason
    assert decide("partially_done", "mixed", 0, searched=0, service_down=False)[0] == "unknown"
    assert "could not be reached" in decide("novel", "search", 0, searched=0, service_down=True)[2]


def test_verified_sources_and_textbook_facts_keep_their_verdict():
    assert decide("novel", "search", 2, searched=9, service_down=False) == ("novel", "search", "")
    # a textbook fact without a verified source stays already_done, marked as the model's own knowledge
    assert decide("already_done", "search", 0, searched=12, service_down=False) == ("already_done", "model_knowledge", "")
    assert decide("unknown", "search", 0, searched=0, service_down=False) == ("unknown", "search", "")


class IrrelevantSearch(Script):
    """The 2026-10-04 failure: the search returns unrelated papers and the model calls a textbook fact novel."""

    def __call__(self, system, prompt):
        from quaera import prompts
        if system == prompts.LITERATURE_SUMMARY:
            self.n["LITERATURE_SUMMARY"] = self.n.get("LITERATURE_SUMMARY", 0) + 1
            return json.dumps({"relevant": [], "summary": "The results are about gravitational waves.", "verdict": "novel",
                               "basis": "search", "cited": [], "relevant_mathlib": []})
        return super().__call__(system, prompt)


def test_research_with_irrelevant_search_never_reports_novel(tmp_path):
    orch = make(tmp_path, IrrelevantSearch())
    report = open(orch.run(), encoding="utf-8").read()
    lit = orch.state("literature")
    assert lit["verdict"] == "unknown" and lit["relevant"] == 0 and lit["searched"] == 1
    assert orch.store.events("literature.verdict_changed")[0]["payload"]["to"] == "unknown"
    assert orch.store.latest("experiment")[-1]["noveltyCheck"]["verdict"] == "unknown"
    assert "Novelty assessment: `unknown`" in report and "by the code rule" in report
    assert "Search: 1 results found · 0 judged relevant · 0 verified." in report


def test_contract_rejects_novelty_without_prior_work():
    bundle = json.load(open(contracts.ROOT / "examples" / "ml-warmup.json", encoding="utf-8"))
    assert contracts.check_bundle(bundle, "ok") == []
    exp = next(o for o in bundle["objects"] if o["type"] == "experiment")
    exp["noveltyCheck"]["priorWork"] = []
    assert any("without any priorWork source" in e for e in contracts.check_bundle(bundle, "bad"))


def test_old_literature_state_is_read_safely(tmp_path):
    """A project started before the rule may hold `novel` without sources; its experiment must still be writable."""
    orch = make(tmp_path, Script())
    nov = orch.novelty_check({"verdict": "novel", "basis": "search", "summary": "s", "papers": []})
    assert nov["verdict"] == "unknown"
    nov = orch.novelty_check({"verdict": "already_done", "basis": "mathlib", "summary": "s", "papers": [],
                              "mathlibKnown": {"decl": "Nat.Prime.two_le", "term": "Nat.Prime.two_le hp"}})
    assert nov["basis"] == "search" and nov["priorWork"] == ["Mathlib: Nat.Prime.two_le"]


# --- B2: Mathlib probe -----------------------------------------------------------------------------------------------

def test_suggested_term_from_repl_and_compiler_output():
    repl = "info:4: Try this: exact Finset.sum_range_id_mul_two n\ninfo:6: 'quaera_main' depends on axioms: [propext]"
    assert suggested_term(repl) == "Finset.sum_range_id_mul_two n"
    oneshot = "/tmp/x.lean:4:2: info: Try this:\n  [apply] exact fun n =>\n    Nat.odd_sum n\n/tmp/x.lean:6:0: info: 'q' depends on axioms"
    assert suggested_term(oneshot) == "fun n => Nat.odd_sum n"
    assert suggested_term("info:4: Try this: exact sorry") is None and suggested_term("ok") is None
    assert mathlib_decl("fun n => Nat.odd_sum n") == "Nat.odd_sum"
    assert mathlib_decl("le_refl n") == "le_refl"


class MathlibLean(FakeTools):
    """Fake Lean in which `exact?` finds the statement in Mathlib."""

    def call(self, role, tool, **args):
        if tool == "lean.compile" and "exact?" in args["source"]:
            self.calls.append((role, tool))
            return json.dumps({"compiled": True, "verified": True, "theorem": "quaera_main", "errors": [], "warnings": [],
                               "axioms": ["propext"], "problems": [], "timed_out": False, "mode": "fake", "seconds": 0.0,
                               "output": "info:4: Try this: exact Finset.sum_range_odd n\ninfo:5: 'quaera_main' depends on axioms: [propext]"})
        return super().call(role, tool, **args)


def test_statement_found_in_mathlib_skips_the_proof_search(tmp_path):
    script = Script()
    orch = make(tmp_path, script)
    orch.tools = MathlibLean(orch.permissions)
    report = open(orch.run(), encoding="utf-8").read()
    assert script.n.get("PROVE", 0) == 0 and script.n.get("SKETCH", 0) == 0
    lit = orch.state("literature")
    assert lit["verdict"] == "already_done" and lit["basis"] == "mathlib" and lit["mathlib"][0] == "Finset.sum_range_odd"
    assert orch.store.get(orch.state("hypothesis_id"))["status"] == "supported"
    assert "exact Finset.sum_range_odd n" in orch.state("proof")["source"]
    assert "Known in Mathlib: `Finset.sum_range_odd`" in report and "already in Mathlib" in report
    assert orch.store.check_final() == []


def test_probe_without_a_suggestion_falls_through_to_the_search(tmp_path):
    script = Script()
    orch = make(tmp_path, script)        # the default fake Lean prints no "Try this"
    orch.run()
    assert script.n["PROVE"] >= 1 and orch.state("literature")["basis"] != "mathlib"
    assert not orch.store.events("literature.mathlib_known")


# --- D2: project status survives a server restart --------------------------------------------------------------------

def test_status_comes_from_the_event_log(home):  # noqa: F811
    done = make(home, Script(), name="finished")
    done.run()
    crashed = make(home, Script(), name="crashed")
    crashed.mark("literature")
    crashed.store.append("run.crashed", crashed.det("director"), {"error": "Error executing tool lean_compile"})
    resumed = make(home, Script(), name="resumed")
    resumed.store.append("run.crashed", resumed.det("director"), {"error": "earlier crash"})
    resumed.mark("literature")
    make(home, Script(), name="idle").mark("literature")
    c = TestClient(server.create_app())
    by_id = {p["id"]: p for p in c.get("/api/projects").json()}
    assert by_id["finished"]["status"] == "done"
    assert by_id["crashed"]["status"] == "crashed" and by_id["crashed"]["error"] == "Error executing tool lean_compile"
    assert by_id["resumed"]["status"] == "paused" and by_id["idle"]["status"] == "paused"


# --- D1: Discover sets expectations ----------------------------------------------------------------------------------

def test_famous_open_problem_states_the_expectation_and_report_leads_with_the_tally(tmp_path):
    from test_discovery import DiscoveryScript, key_of, make_discovery
    from quaera.discovery import FAMOUS_OPEN_NOTE

    orch, a, b = make_discovery(tmp_path, "famous")
    for sc in (a, b):
        orig = sc.__call__
        sc.__class__ = type("Famous", (DiscoveryScript,), {"__call__": lambda self, system, prompt, _o=orig: (
            json.dumps({**json.loads(_o(system, prompt)), "difficulty": "famous_open"}) if key_of(system) == "LANDSCAPE"
            else _o(system, prompt))})
    orch.run()
    s = orch.store
    assert orch.state("landscape")["difficulty"] == "famous_open"
    card = next(m for m in s.latest("message") if m["kind"] == "approval_request" and m["subjectId"] == orch.state("hypothesis_id"))
    assert card["body"].startswith(FAMOUS_OPEN_NOTE)
    assert s.events("discovery.expectation")
    report = (tmp_path / "famous" / "report.md").read_text(encoding="utf-8")
    summary = report.split("## Summary", 1)[1].strip()
    assert summary.startswith("**Main claim: proved** · verified lemmas ") and "refuted lemmas 1" in summary


def test_ordinary_discovery_target_has_no_expectation_note(tmp_path):
    from test_discovery import make_discovery
    orch, _, _ = make_discovery(tmp_path, "plain")
    orch.run()
    assert orch.state("landscape")["difficulty"] is None and not orch.store.events("discovery.expectation")
