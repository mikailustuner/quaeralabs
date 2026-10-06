"""Keşif kipi: çok modelli fikir üretimi, çapraz inceleme, lemma programı, çürütme + onarım, sentez ve yeniden doğrulama.
Gerçek model ve Lean yerine betik ve sahte Lean (teorem adına duyarlı)."""

import json
import re

from quaera import prompts
from quaera.discovery import DISCOVERY_STAGES, DiscoveryOrchestrator, lemma_file
from quaera.gateway import Gateway, ScriptedProvider
from quaera.lean import normalize, statement_of
from quaera.orchestrator import AutoApprover
from quaera.permissions import Permissions
from quaera.store import Store

from test_orchestrator import PROOF, STATEMENT, FakeTools

L1 = "theorem quaera_L1 (n : ℕ) : ∑ i ∈ Finset.range (n + 1), (2 * i + 1) = ∑ i ∈ Finset.range n, (2 * i + 1) + (2 * n + 1) := by sorry"
L2_FALSE = "theorem quaera_L2 (n : ℕ) : n ^ 2 = n := by sorry"
L2_FIXED = "theorem quaera_L2 (n : ℕ) : (n + 1) ^ 2 = n ^ 2 + 2 * n + 1 := by sorry"


class LemmaTools(FakeTools):
    """Sahte Lean, teorem adına duyarlı: onaylı ifade verilen ad için karşılaştırılır."""

    def call(self, role, tool, **args):
        if tool != "lean.compile":
            return super().call(role, tool, **args)
        self.permissions.check_tool(role, tool)
        self.calls.append((role, tool))
        src, approved, name = args["source"], args.get("approved_statement"), args.get("theorem") or "quaera_main"
        problems, errors = [], []
        if "sorry" in src:
            problems.append("Proof contains `sorry`.")
        if "BADPROOF" in src or "first | " in src or "import Mathlib" not in src:
            errors.append("line 3: unsolved goals")
        if approved and statement_of(src, name) != normalize(approved):
            problems.append("The approved theorem statement was changed; it must be kept exactly.")
        ok = not errors
        return json.dumps({"compiled": ok, "verified": ok and not problems, "theorem": name, "errors": errors, "warnings": [],
                           "axioms": ["propext"], "problems": problems, "output": "ok", "timed_out": False, "mode": "fake", "seconds": 0.0})


def key_of(system):
    keys = ("LITERATURE_PLAN", "LITERATURE_SUMMARY", "LANDSCAPE", "TARGET", "IDEATE", "CROSS_REVIEW", "PROGRAM",
                            "REPAIR", "FORMALIZE", "CRITIC_STATEMENT", "BACKTRANSLATE", "PROVE", "SKETCH", "REFUTE",
                            "EXPLORE_MATH", "CRITIC_RESULT")
    for norm in (system, re.sub(r"quaera_L\d+", "quaera_main", system),
                 re.sub(r"`quaera_step_\d+`", "`quaera_main`", re.sub(r"quaera_L\d+", "quaera_main", system))):
        k = next((k for k in keys if getattr(prompts, k) == norm), None)
        if k:
            return k
    raise AssertionError(system[-200:])


def proved(lean_stmt: str) -> str:
    return "```lean\nimport Mathlib\n\n" + lean_stmt.replace(":= by sorry", ":= by\n  simp") + "\n```"


class DiscoveryScript:
    def __init__(self, family):
        self.family, self.calls = family, []

    def __call__(self, system, prompt):
        k = key_of(system)
        self.calls.append(k)
        if k == "LITERATURE_PLAN":
            return '{"arxiv_queries": ["odd sums"], "openalex_queries": [], "mathlib_queries": ["sum_range_succ"]}'
        if k == "LITERATURE_SUMMARY":
            return '{"summary": "Known.", "verdict": "already_done", "cited": ["arXiv:1111.11111"], "relevant_mathlib": ["Finset.sum_range_succ"]}'
        if k == "LANDSCAPE":
            return json.dumps({"approaches": [{"name": "Induction", "idea": "add one term", "bestResult": "full", "obstruction": "none"}],
                               "barriers": [{"name": "None", "body": "elementary"}], "partialResults": [], "openAngles": ["telescoping"]})
        if k == "TARGET":
            return '{"statement": "For every n, the sum of the first n odd numbers is n².", "negation": "Some n breaks it.", "note": ""}'
        if k == "IDEATE":
            title = "Induction with a step lemma" if "Your lens (Free)" in prompt else "Telescoping squares"
            return json.dumps({"strategies": [{"title": title, "idea": f"{title}: reduce to a one-step identity. ({self.family})",
                                               "keySteps": ["step identity", "square identity"], "barrierCheck": "elementary",
                                               "killTest": "check n ≤ 10", "novelty": "known", "direction": "prove"}]})
        if k == "CROSS_REVIEW":
            score = 9 if "Induction" in prompt else 5
            return json.dumps({"plausibility": score, "novelty": 3, "barrierAwareness": 8, "testability": 9, "fatalFlaw": None,
                               "strongestPoint": "simple", "suggestedKillTest": "n ≤ 10", "summary": f"reviewed by {self.family}"})
        if k == "PROGRAM":
            return json.dumps({"lemmas": [{"id": "L1", "statement": "step", "lean": L1, "kind": "bridge", "dependsOn": [], "numericCheck": "small n"},
                                          {"id": "L2", "statement": "square", "lean": L2_FALSE, "kind": "new", "dependsOn": ["L1"], "numericCheck": None}],
                               "assembly": "induction using L1 and L2"})
        if k == "REPAIR":
            return json.dumps({"decision": "repair", "lemma": {"statement": "binomial square", "lean": L2_FIXED}, "reason": "n² = n is false for n=2"})
        if k == "FORMALIZE":
            return f"```lean\n{STATEMENT}```"
        if k == "CRITIC_STATEMENT":
            return '{"faithful": true, "issues": [], "weakerThanQuestion": false, "summary": "ok"}'
        if k == "BACKTRANSLATE":
            return '{"translation": "sum of first n odd numbers is n squared", "oddities": []}'
        if k == "PROVE":
            if "theorem quaera_main" in prompt:          # sentez: doğrulanmış lemmalar dosyada, ana teoremi kur
                return f"```lean\n{PROOF}```"
            if "quaera_L1" in prompt.split("Approved statement", 1)[-1][:400]:
                return proved(L1)
            if "(n + 1) ^ 2 = n ^ 2 + 2 * n + 1" in prompt:
                return proved(L2_FIXED)
            if "n ^ 2 = n" in prompt:
                return "```lean\nimport Mathlib\n\ntheorem quaera_L2 (n : ℕ) : n ^ 2 = n := by BADPROOF\n```"
            return f"```lean\n{PROOF}```"
        if k == "SKETCH":
            return "no sketch"
        if k == "REFUTE":
            if "n ^ 2 = n" in prompt:
                return "```lean\nimport Mathlib\n\ntheorem quaera_refute : ¬ (∀ (n : ℕ), n ^ 2 = n) := by\n  intro h; have := h 2; omega\n```"
            return "no counterexample"
        if k == "EXPLORE_MATH":
            return "```python\nprint('QUAERA_EXPLORE {\"checked\": \"n ≤ 50\", \"counterexample\": null, \"observations\": []}')\n```"
        if k == "CRITIC_RESULT":
            return '{"concerns": []}'
        raise AssertionError(k)


class Fam(ScriptedProvider):
    """Betikli sağlayıcı, gerçek aile adıyla (fiyatı sıfır olan 'scripted' modeli kullanır)."""

    def model_for(self, profile):
        return "scripted"


def make_discovery(tmp_path, name="rh"):
    perms = Permissions.load()
    store = Store(tmp_path / name / "quaera.db", perms)
    store.set_meta("title", "Odd sums (discovery)")
    store.set_meta("mode", "discover")
    record = lambda k, p: store.append(k, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, p)  # noqa: E731
    a, b = DiscoveryScript("anthropic"), DiscoveryScript("openai")
    gw = Gateway({"anthropic": Fam(a, "anthropic"), "openai": Fam(b, "openai")}, 5.0, perms.agents, record)
    orch = DiscoveryOrchestrator(store, gw, LemmaTools(perms), AutoApprover(100.0), perms, log=lambda m: None, reports_dir=tmp_path / name)
    store.put({"type": "question", "createdBy": orch.human(), "title": "Is the sum of the first n odd numbers n²?", "domain": "math",
               "scope": "Lean 4 + Mathlib"})
    return orch, a, b


def test_lemma_file_normalizes_name_and_proof():
    f = lemma_file("import Mathlib\nlemma foo (n : ℕ) : n = n := by\n  rfl", "quaera_L3")
    assert f == "import Mathlib\n\ntheorem quaera_L3 (n : ℕ) : n = n := by\n  sorry\n"


def test_discovery_program_end_to_end(tmp_path):
    orch, a, b = make_discovery(tmp_path)
    orch.run()
    s = orch.store
    assert [e["payload"]["stage"] for e in s.events("stage.done")] == DISCOVERY_STAGES
    # fikir üretimi iki aileye dağıldı; her strateji yazarından farklı bir aileye incelendi
    proposed = [e["payload"] for e in s.events("strategy.proposed")]
    assert {p["family"] for p in proposed} == {"anthropic", "openai"} and proposed[0]["lens"] == "free"
    reviews = [e["payload"] for e in s.events("strategy.reviewed")]
    assert reviews and all(r["crossFamily"] for r in reviews)
    assert "IDEATE" in a.calls and "IDEATE" in b.calls
    # hedef daraltılmadı: soru olduğu gibi, tam kapsam
    h = s.get(orch.state("hypothesis_id"))
    assert h["scopeRelation"]["relation"] == "full" and h["status"] == "supported"
    # L2 çürütüldü (Lean), onarıldı ve onarım doğrulandı; L1 doğrulandı
    status = [(e["payload"]["id"], e["payload"]["status"]) for e in s.events("lemma.status")]
    assert ("L1", "verified") in status and ("L2", "refuted") in status and ("L2'", "verified") in status
    # sentez ana teoremi kurdu, Doğrulayıcı hem ana ispatı hem lemmaları yeniden derledi
    assert s.events("synthesis.done")[-1]["payload"]["solved"] and orch.state("proof")
    assert s.latest("verification")[-1]["reproduced"] == "yes"
    assert {e["payload"]["id"] for e in s.events("lemma.reverified") if e["payload"]["verified"]} >= {"L1", "L2'"}
    assert s.check_final() == []
    report = (tmp_path / "rh" / "rapor.md").read_text(encoding="utf-8")
    assert "## Discovery program" in report and "L2" in report and "refuted" in report


def test_discovery_reports_discoveries_when_main_claim_stays_open(tmp_path):
    orch, a, b = make_discovery(tmp_path, "open")
    # ana teoremin ispatı hiç bulunamasın: sentez başarısız, ana iddia açık kalır
    for sc in (a, b):
        orig = sc.__call__
        sc.__class__ = type("NoMain", (DiscoveryScript,), {"__call__": lambda self, system, prompt, _o=orig:
                                                          "```lean\nimport Mathlib\n\ntheorem quaera_main (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2 := by BADPROOF\n```"
                                                          if key_of(system) == "PROVE" and "theorem quaera_main" in prompt else _o(system, prompt)})
    orch.run()
    s = orch.store
    res = s.latest("result")[-1]
    assert "neither proved nor refuted" in res["summary"] and "verified in Lean" in res["summary"]
    h = s.get(orch.state("hypothesis_id"))
    assert h["status"] == "inconclusive"


def test_discovery_lessons_and_server_create(tmp_path, monkeypatch):
    from quaera.memory import lessons
    orch, _, _ = make_discovery(tmp_path, "les")
    orch.run()
    texts = [l["text"] for l in lessons(orch.store)]
    assert any(t.startswith("Lean-verified lemma") for t in texts) and any(t.startswith("Refuted intermediate claim") for t in texts)

    import quaera.cli as cli
    import quaera.server as server
    from starlette.testclient import TestClient
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(cli, "HOME", projects)
    monkeypatch.setattr(server, "HOME", projects)
    monkeypatch.setattr(server, "MANAGER_PROVIDERS", {"scripted": ScriptedProvider(lambda s, p: "Title")})
    monkeypatch.setattr(server.RUNNER, "start", lambda *a, **k: None)      # araştırma başlatılmaz, yalnızca kayıt
    c = TestClient(server.create_app())
    assert c.post("/api/projects", json={"question": "Is RH true or false?", "domain": "math", "budget": 120}).status_code == 400
    r = c.post("/api/projects", json={"question": "Is RH true or false?", "domain": "math", "budget": 120, "mode": "discover",
                                      "families": ["anthropic", "openai"]})
    assert r.status_code == 201
    p = c.get(f"/api/projects/{r.json()['id']}").json()
    assert p["mode"] == "discover" and p["stages"] == DISCOVERY_STAGES and p["families"] == ["anthropic", "openai"]
    assert c.post("/api/projects", json={"question": "Some ML question here", "domain": "ml", "budget": 1, "mode": "discover",
                                         "dataDir": "/tmp"}).status_code == 400


def test_tolerates_malformed_model_fields():
    from quaera.discovery import score_of
    assert score_of("7/10") == 7.0 and score_of(None) == 0.0 and score_of(12) == 10.0 and score_of("high") == 0.0


def test_discovery_approach_branch_reruns_ideation_without_repeating_strategies(tmp_path):
    from quaera import tree
    orch, a, b = make_discovery(tmp_path, "rh")
    orch.run()
    child = tree.branch_project(tmp_path, "rh", "approach", "Both strategies were tried; look for a new idea.",
                                note="Avoid induction; try a combinatorial picture.", by={"kind": "agent", "role": "director",
                                "model": "m", "modelFamily": "anthropic"}, budget=5.0)
    store = Store(tmp_path / child / "quaera.db", Permissions.load())
    assert store.meta("branch")["atStage"] == "design"                   # the target is kept, ideation runs again
    seen = []

    class Again(DiscoveryScript):
        def __call__(self, system, prompt):
            if key_of(system) == "IDEATE":
                seen.append(prompt)
                return json.dumps({"strategies": [
                    {"title": "Telescoping squares", "idea": "old idea again", "direction": "prove"},
                    {"title": "Dot-grid picture of odd sums", "idea": "count L-shaped layers of a square grid", "direction": "prove"}]})
            return super().__call__(system, prompt)
    perms = Permissions.load()
    record = lambda k, p: store.append(k, {"kind": "agent", "role": "director", "model": "quaera/deterministic", "modelFamily": "quaera"}, p)  # noqa: E731
    gw = Gateway({"anthropic": Fam(Again("anthropic"), "anthropic"), "openai": Fam(Again("openai"), "openai")}, 5.0, perms.agents, record)
    DiscoveryOrchestrator(store, gw, LemmaTools(perms), AutoApprover(100.0), perms, log=lambda m: None, reports_dir=tmp_path / child).run()
    assert seen and all("Telescoping squares" in p and "combinatorial picture" in p for p in seen)   # every lane got the history
    new = [e["payload"]["title"] for e in store.events("strategy.proposed") if e["seq"] > store.meta("branch")["atSeq"]]
    assert new == ["Dot-grid picture of odd sums"] and store.events("strategy.repeat_skipped")
