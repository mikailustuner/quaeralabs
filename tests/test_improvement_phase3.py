"""Improvement plan phase 3 (2026-10-20): Lean and ML images (A2), literature sources and the
second search pass (B3), answers split by thinking and cut-off JSON (D3). No model calls. The image tests run only where
the image has been pulled or built (QUAERA_LEAN_IMAGE / QUAERA_ML_IMAGE)."""

import json
import shutil

import pytest

from quaera import images, literature, lean as lean_mod
from quaera.mcp_servers import literature_server as L
from quaera.structured import looks_cut

ENGINE = shutil.which("podman") or shutil.which("docker")


@pytest.fixture(autouse=True)
def fresh_choice():
    lean_mod.configured_image.cache_clear()
    images.layout.cache_clear()
    yield
    lean_mod.configured_image.cache_clear()
    images.layout.cache_clear()


# --- A2: images ------------------------------------------------------------------------------------------------------

def test_image_mode_setting(monkeypatch):
    monkeypatch.setattr(images.sys, "platform", "linux")
    monkeypatch.setenv("QUAERA_LEAN", "host")
    assert images.mode("lean") == "host"
    monkeypatch.setenv("QUAERA_LEAN", "sometimes")
    with pytest.raises(images.ImageUnavailable):
        images.mode("lean")
    monkeypatch.setattr(images.sys, "platform", "darwin")        # no host tools inside a Linux container on macOS
    monkeypatch.setenv("QUAERA_LEAN", "host")
    assert images.mode("lean") == "image"


LAYOUT = {"kind": "lean", "workspace": "/lean", "toolchain": "/opt/elan/toolchains/lt", "toolchainName": "leanprover/lean4:v9",
          "lean": "/opt/elan/toolchains/lt/bin/lean", "repl": "/lean/repl", "leanPath": ["/lean/a", "/opt/elan/toolchains/lt/lib/lean"],
          "mathlibRev": "v9.9.9"}


@pytest.mark.skipif(not ENGINE, reason="no container engine")
def test_lean_from_an_image_binds_nothing_from_the_host(monkeypatch):
    monkeypatch.setenv("QUAERA_LEAN", "image")
    monkeypatch.setenv("QUAERA_LEAN_IMAGE", "example/quaera-lean:test")
    monkeypatch.setattr(images, "layout", lambda image: LAYOUT)
    c = lean_mod.LeanChecker()
    assert c.image == "example/quaera-lean:test" and c.has_repl() and c.binds == []
    argv, stop = c._repl_argv()
    assert "example/quaera-lean:test" in argv and argv[-1] == "/lean/repl" and stop[1:3] == ["rm", "-f"]
    volumes = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert len(volumes) == 1 and volumes[0].endswith(":rw")          # only the fresh working directory
    assert f"LEAN_PATH=/lean/a:/opt/elan/toolchains/lt/lib/lean" in argv
    assert lean_mod.mathlib_rev() == "v9.9.9" and lean_mod.lean_versions() == ("leanprover/lean4:v9", "v9.9.9")


def test_auto_prefers_the_host_workspace(monkeypatch):
    monkeypatch.setattr(images.sys, "platform", "linux")
    monkeypatch.setenv("QUAERA_LEAN", "auto")
    monkeypatch.setattr(lean_mod, "host_ready", lambda workspace=None: True)
    assert lean_mod.configured_image() is None
    lean_mod.configured_image.cache_clear()
    monkeypatch.setattr(lean_mod, "host_ready", lambda workspace=None: False)
    monkeypatch.setattr(images, "present", lambda image: True)
    assert lean_mod.configured_image() == images.lean_image()


def test_gpu_jobs_are_refused_in_the_ml_image(monkeypatch, tmp_path):
    from quaera.mcp_servers import sandbox_server
    monkeypatch.setattr(sandbox_server, "ml_image_in_use", lambda: "example/quaera-ml:test")
    res = json.loads(sandbox_server.sandbox_exec(str(tmp_path), ["python", "-c", "1"], gpu=True))
    assert res["returncode"] == -1 and "CPU-only" in res["stderr"]


@pytest.mark.skipif(not (ENGINE and images.present(images.ml_image())), reason="ML image not pulled")
def test_experiment_runs_in_the_ml_image(monkeypatch, tmp_path):
    from quaera.mcp_servers import sandbox_server
    monkeypatch.setenv("QUAERA_ML", "image")
    (tmp_path / "exp.py").write_text("import os, sklearn\nprint(sklearn.__version__ > '1', os.path.exists(os.path.expanduser('~/.ssh')))\n")
    res = json.loads(sandbox_server.sandbox_exec(str(tmp_path), ["python", "exp.py"], timeout_s=300))
    assert res["returncode"] == 0 and res["stdout"].split() == ["True", "False"] and images.ml_image() in res["sandbox"]


@pytest.mark.lean
@pytest.mark.skipif(not (ENGINE and images.present(images.lean_image())), reason="Lean image not pulled")
def test_lean_image_checks_a_proof(monkeypatch):
    """One-shot and REPL checks inside the image (no Mathlib import, so it runs in seconds on any machine)."""
    monkeypatch.setenv("QUAERA_LEAN", "image")
    c = lean_mod.LeanChecker(header="import Init", load_timeout_s=300)
    try:
        good = "theorem t (n : Nat) : n + 0 = n := rfl\n"
        assert c.check(good, "t", clean=True).verified
        assert c.check(good, "t").verified and c.check(good, "t").mode == "repl"
        bad = c.check("theorem t (n : Nat) : n + 1 = n := by\n  sorry\n", "t", clean=True)
        assert not bad.verified and "Proof contains `sorry`." in bad.problems
    finally:
        c.close()


# --- B3: literature ----------------------------------------------------------------------------------------------------

def test_openalex_abstract_is_put_back_in_order():
    assert L.abstract_of({"world": [1], "hello": [0], "again": [3], "hello,": [2]}) == "hello world hello, again"
    assert L.abstract_of(None) == ""


def test_semantic_scholar_keeps_only_verifiable_papers(monkeypatch):
    body = {"data": [{"title": "A", "year": 2020, "abstract": "x", "citationCount": 5, "externalIds": {"ArXiv": "2001.00001"}},
                     {"title": "B", "year": 2021, "externalIds": {"DOI": "10.1/b"}},
                     {"title": "C", "year": 2022, "externalIds": {"CorpusId": 1}}]}
    seen = {}

    def get(url, headers=None):
        seen["headers"] = headers
        return 200, json.dumps(body)
    monkeypatch.setattr(L.citations, "_get", get)
    monkeypatch.setenv("QUAERA_CACHE", "off")
    monkeypatch.setenv("S2_API_KEY", "k")
    out = json.loads(L.s2_search("q"))
    assert [r["id"] for r in out] == ["arXiv:2001.00001", "10.1/b"] and seen["headers"] == {"x-api-key": "k"}


def test_search_results_are_cached_but_errors_are_not(monkeypatch, tmp_path):
    monkeypatch.setenv("QUAERA_HOME", str(tmp_path))
    monkeypatch.delenv("QUAERA_CACHE", raising=False)
    L._db.cache_clear()
    calls = []

    def get(url, headers=None):
        calls.append(url)
        return (503, "") if "fail" in url else (200, json.dumps({"results": [{"title": "T", "doi": "https://doi.org/10.1/t"}]}))
    monkeypatch.setattr(L.citations, "_get", get)
    assert L.openalex_search("ok") == L.openalex_search("ok") and len(calls) == 1
    assert "error" in json.loads(L.openalex_search("fail")) and "error" in json.loads(L.openalex_search("fail"))
    assert len(calls) == 3
    monkeypatch.setenv("QUAERA_CACHE", "off")
    L.openalex_search("ok")
    assert len(calls) == 4
    L._db.cache_clear()


class Tools:
    """arXiv returns an unrelated paper for the first queries and the relevant one for the rewritten query."""

    def __init__(self, down=False):
        self.queries, self.down = [], down

    def call_json(self, role, tool, **kw):
        if self.down:
            raise ConnectionError("offline")
        self.queries.append((tool, kw.get("query")))
        if tool == "arxiv.search":
            hit = "arXiv:2001.00002" if kw["query"] == "dropout delayed generalization" else "arXiv:2001.00001"
            return [{"id": hit, "title": "Paper " + hit, "year": "2020", "summary": "s"}]
        return []

    def call(self, role, tool, **kw):
        return "real"


def llm_for(replies):
    def llm(system, prompt, n):
        from quaera import prompts
        name = next(k for k in ("LITERATURE_PLAN", "LITERATURE_REPLAN", "LITERATURE_SUMMARY") if getattr(prompts, k) == system)
        replies.setdefault("_log", []).append(name)
        return json.dumps(replies[name](prompt)), {"role": "literature"}
    return llm


def test_nothing_relevant_rewrites_the_queries_once():
    plan = lambda p: {"arxiv_queries": ["a b", "c d", "e f", "g h"], "openalex_queries": ["i j", "k l", "m n"]}
    summary = lambda p: ({"relevant": ["arXiv:2001.00002"], "cited": ["arXiv:2001.00002"], "summary": "s", "verdict": "partially_done",
                          "basis": "search"} if "2001.00002" in p else {"relevant": [], "cited": [], "summary": "s", "verdict": "unknown"})
    replies = {"LITERATURE_PLAN": plan, "LITERATURE_SUMMARY": summary,
               "LITERATURE_REPLAN": lambda p: {"arxiv_queries": ["dropout delayed generalization", "a b", "x y"], "openalex_queries": ["z w"]}}
    tools = Tools()
    res = literature.run_literature("q", "", "ml", llm_for(replies), tools)
    assert res.replanned and res.verdict == "partially_done" and [v["ref"] for v in res.verified] == ["arXiv:2001.00002"]
    assert res.queries == ["a b", "c d", "e f", "i j", "k l", "dropout delayed generalization", "x y", "z w"]
    assert len(res.queries) <= literature.MAX_QUERIES and "a b" not in res.queries[5:]
    assert replies["_log"] == ["LITERATURE_PLAN", "LITERATURE_SUMMARY", "LITERATURE_REPLAN", "LITERATURE_SUMMARY"]


def test_no_second_pass_when_something_relevant_was_found_or_the_services_are_down():
    replies = {"LITERATURE_PLAN": lambda p: {"arxiv_queries": ["a b"], "openalex_queries": []},
               "LITERATURE_SUMMARY": lambda p: {"relevant": [], "cited": ["arXiv:2001.00001"], "summary": "s", "verdict": "already_done"}}
    res = literature.run_literature("q", "", "math", llm_for(replies), Tools())
    assert not res.replanned and "LITERATURE_REPLAN" not in replies["_log"]
    replies = {"LITERATURE_PLAN": lambda p: {"arxiv_queries": ["a b"], "openalex_queries": []},
               "LITERATURE_SUMMARY": lambda p: {"relevant": [], "cited": [], "summary": "s", "verdict": "unknown"}}
    res = literature.run_literature("q", "", "math", llm_for(replies), Tools(down=True))
    assert not res.replanned and res.tool_errors and "LITERATURE_REPLAN" not in replies["_log"]


def test_service_errors_are_recorded():
    class ErrTools(Tools):
        def call_json(self, role, tool, **kw):
            return {"error": "Semantic Scholar HTTP 429"}
    errors = []
    assert literature._safe(ErrTools(), errors, "literature", "s2.search", query="q") == []
    assert errors[0]["error"] == "Semantic Scholar HTTP 429"


# --- D3: answers split by thinking, cut-off JSON -------------------------------------------------------------------------

def test_cut_answers_are_told_apart_from_malformed_ones():
    assert looks_cut('{"strategies": [{"title": "a", "idea": "b') and looks_cut('```json\n{"a": [1, 2')
    assert not looks_cut('{"a": 1}') and not looks_cut('```json\n{"a": 1}\n```') and not looks_cut("")
