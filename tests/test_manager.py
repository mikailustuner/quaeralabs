"""Project manager: only watches, answers from its own budget, never sends notes to the team (the human does); short titles;
lessons learned across projects (contextual memory)."""

from starlette.testclient import TestClient

import quaera.server as server
from quaera.gateway import ScriptedProvider
from quaera.manager import Manager, digest, fallback_title, split_forward
from quaera.memory import LabMemory, lessons

from test_orchestrator import Script, make
from test_server import home  # noqa: F401  (fixture)


def scripted(text, cost=0.0):
    calls = []
    def respond(system, prompt):
        calls.append((system, prompt))
        return text(system, prompt) if callable(text) else text
    return {"scripted": ScriptedProvider(respond, "scripted", cost)}, calls


def test_manager_answers_from_record_without_touching_research(tmp_path):
    orch = make(tmp_path, Script(), name="odd")
    orch.run()
    before = len(orch.store.events("model.call")), len(orch.store.latest("message"))
    providers, calls = scripted("The hypothesis was proved in Lean.\nFORWARD: Director: also state the n=0 case in the report.", cost=0.01)
    m = Manager(tmp_path / "odd", providers)
    reply = m.ask("What is the status?")
    assert reply["text"] == "The hypothesis was proved in Lean." and reply["forward"].startswith("Director:")
    system, prompt = calls[0]
    assert "only OBSERVE" in system and "the sum of the first n odd numbers" in prompt and "[supported]" in prompt
    assert [t["from"] for t in m.history()] == ["human", "manager"]
    # the research budget and messages are unchanged; the manager's cost is in a separate event
    assert (len(orch.store.events("model.call")), len(orch.store.latest("message"))) == before
    assert m.usage()["spentUsd"] == 0.01 and orch.store.events("manager.call")
    m.close()


def test_manager_has_own_budget_cap(tmp_path):
    orch = make(tmp_path, Script(), name="odd")
    orch.store.append("manager.error", orch.det("director"), {"costUsd": 0.5})   # billed failed calls count too
    providers, calls = scripted("ok", cost=0.01)
    m = Manager(tmp_path / "odd", providers, cap_usd=0.5)
    reply = m.ask("hello")
    assert reply["error"] == "budget" and not calls and "not affected" in reply["text"]
    m.close()


def test_short_title_and_fallback(tmp_path):
    make(tmp_path, Script(), name="odd")
    providers, _ = scripted('"Sum of First n Odds."')
    m = Manager(tmp_path / "odd", providers)
    assert m.name() == "Sum of First n Odds"
    m.close()
    assert fallback_title("Is the sum of the first n odd numbers n²? Another long explanation goes here") .endswith("…")
    assert split_forward("a\nFORWARD: b") == ("a", "b") and split_forward("just a") == ("just a", None)


def test_lessons_cover_outcome_method_and_are_cross_project(tmp_path):
    orch = make(tmp_path, Script(), name="odd")
    mem = LabMemory(tmp_path / "memory.db")
    orch.memory = mem
    orch.run()
    items = mem.learnings(project="odd")
    kinds = {i["kind"] for i in items}
    assert "finding" in kinds and "method" in kinds
    assert any(i["text"].startswith("Supported:") for i in items)
    assert lessons(orch.store) and "Recent events" in digest(orch.store)
    orch.store.append("fault.injected", orch.det("director"), {"kind": "flip"})
    assert lessons(orch.store) == []


def test_api_manager_learnings_memory_and_rename(home, monkeypatch):  # noqa: F811
    providers, _ = scripted("All is well.")
    monkeypatch.setattr(server, "MANAGER_PROVIDERS", providers)
    orch = make(home, Script(), name="odd")
    orch.memory = LabMemory(home.parent / "memory.db")
    orch.run()
    c = TestClient(server.create_app())
    r = c.post("/api/projects/odd/manager", json={"text": "Summarize"}).json()
    assert r["reply"]["text"] == "All is well." and r["usage"]["capUsd"] > 0
    assert len(c.get("/api/projects/odd/manager").json()["history"]) == 2
    assert c.post("/api/projects/odd/manager", json={"text": ""}).status_code == 400
    assert c.post("/api/projects/odd/rename", json={"shortTitle": "Odd sums"}).json()["shortTitle"] == "Odd sums"
    assert c.get("/api/projects").json()[0]["shortTitle"] == "Odd sums"
    feed = c.get("/api/memory/learnings").json()
    assert feed and feed[0]["title"] == "Odd sums"
    assert c.get("/api/memory").json()[0]["project"] == "odd"    # memory is listed even without a search
