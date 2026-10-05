"""Proje yöneticisi: yalnızca izler, kendi bütçesiyle yanıtlar, ekibe not göndermez (insan gönderir); kısa adlar;
projeler arası öğrenilenler (bağlamsal hafıza)."""

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
    orch = make(tmp_path, Script(), name="tek")
    orch.run()
    before = len(orch.store.events("model.call")), len(orch.store.latest("message"))
    providers, calls = scripted("Hipotez Lean ile ispatlandı.\nFORWARD: Direktör: n=0 durumunu da raporda belirtin.", cost=0.01)
    m = Manager(tmp_path / "tek", providers)
    reply = m.ask("Durum ne?")
    assert reply["text"] == "Hipotez Lean ile ispatlandı." and reply["forward"].startswith("Direktör:")
    system, prompt = calls[0]
    assert "only OBSERVE" in system and "the sum of the first n odd numbers" in prompt and "[supported]" in prompt
    assert [t["from"] for t in m.history()] == ["human", "manager"]
    # araştırmanın bütçesi ve mesajları değişmez; yöneticinin maliyeti ayrı olayda
    assert (len(orch.store.events("model.call")), len(orch.store.latest("message"))) == before
    assert m.usage()["spentUsd"] == 0.01 and orch.store.events("manager.call")
    m.close()


def test_manager_has_own_budget_cap(tmp_path):
    orch = make(tmp_path, Script(), name="tek")
    orch.store.append("manager.error", orch.det("director"), {"costUsd": 0.5})   # ücretlenen başarısız çağrılar da sayılır
    providers, calls = scripted("ok", cost=0.01)
    m = Manager(tmp_path / "tek", providers, cap_usd=0.5)
    reply = m.ask("merhaba")
    assert reply["error"] == "budget" and not calls and "not affected" in reply["text"]
    m.close()


def test_short_title_and_fallback(tmp_path):
    make(tmp_path, Script(), name="tek")
    providers, _ = scripted('"Sum of First n Odds."')
    m = Manager(tmp_path / "tek", providers)
    assert m.name() == "Sum of First n Odds"
    m.close()
    assert fallback_title("İlk n tek sayının toplamı n² midir? Uzun bir açıklama daha burada") .endswith("…")
    assert split_forward("a\nFORWARD: b") == ("a", "b") and split_forward("yalnız a") == ("yalnız a", None)


def test_lessons_cover_outcome_method_and_are_cross_project(tmp_path):
    orch = make(tmp_path, Script(), name="tek")
    mem = LabMemory(tmp_path / "memory.db")
    orch.memory = mem
    orch.run()
    items = mem.learnings(project="tek")
    kinds = {i["kind"] for i in items}
    assert "finding" in kinds and "method" in kinds
    assert any(i["text"].startswith("Supported:") for i in items)
    assert lessons(orch.store) and "Recent events" in digest(orch.store)
    orch.store.append("fault.injected", orch.det("director"), {"kind": "flip"})
    assert lessons(orch.store) == []


def test_api_manager_learnings_memory_and_rename(home, monkeypatch):  # noqa: F811
    providers, _ = scripted("Her şey yolunda.")
    monkeypatch.setattr(server, "MANAGER_PROVIDERS", providers)
    orch = make(home, Script(), name="tek")
    orch.memory = LabMemory(home.parent / "memory.db")
    orch.run()
    c = TestClient(server.create_app())
    r = c.post("/api/projects/tek/manager", json={"text": "Özetle"}).json()
    assert r["reply"]["text"] == "Her şey yolunda." and r["usage"]["capUsd"] > 0
    assert len(c.get("/api/projects/tek/manager").json()["history"]) == 2
    assert c.post("/api/projects/tek/manager", json={"text": ""}).status_code == 400
    assert c.post("/api/projects/tek/rename", json={"shortTitle": "Odd sums"}).json()["shortTitle"] == "Odd sums"
    assert c.get("/api/projects").json()[0]["shortTitle"] == "Odd sums"
    feed = c.get("/api/memory/learnings").json()
    assert feed and feed[0]["title"] == "Odd sums"
    assert c.get("/api/memory").json()[0]["project"] == "tek"    # arama olmadan da hafıza listelenir
