"""Web sunucusu, web onaycısı, @ notları ve kanıt paketi testleri (model çağrısı yok)."""

import io
import json
import threading
import zipfile

import pytest
from starlette.testclient import TestClient

import quaera.cli as cli
import quaera.server as server
from quaera import package
from quaera.server import WebApprover

from test_orchestrator import Script, make


@pytest.fixture()
def home(tmp_path, monkeypatch):
    projects = tmp_path / "projects"            # hafıza ve vakalar HOME'un üst dizinine yazılır: tmp_path içinde kalsın
    projects.mkdir()
    monkeypatch.setattr(cli, "HOME", projects)
    monkeypatch.setattr(server, "HOME", projects)
    monkeypatch.setattr(package, "KEY_PATH", tmp_path / "signing-key")
    # Kısa ad / yönetici sohbeti testlerde asla gerçek modele gitmesin.
    from quaera.gateway import ScriptedProvider
    monkeypatch.setattr(server, "MANAGER_PROVIDERS", {"scripted": ScriptedProvider(lambda s, p: "Test title")})
    return projects


def finished_project(home):
    orch = make(home, Script(), name="tek-sayilar")
    orch.run()
    orch.store.set_meta("budgetCapUsd", 5.0)
    return "tek-sayilar", orch


def test_project_list_detail_graph_report(home):
    pid, _ = finished_project(home)
    c = TestClient(server.create_app())
    projects = c.get("/api/projects").json()
    assert projects[0]["id"] == pid and projects[0]["hypothesis"]["status"] == "supported"
    detail = c.get(f"/api/projects/{pid}").json()
    assert detail["report"] and any(o["type"] == "verification" for o in detail["objects"])
    g = c.get(f"/api/projects/{pid}/graph").json()
    types = {n["type"] for n in g["nodes"]}
    assert {"question", "hypothesis", "result", "verification"} <= types and g["edges"]
    assert "QuaeraLabs" in c.get(f"/api/projects/{pid}/report.md").text
    replay = c.get(f"/api/projects/{pid}/replay.json").json()
    assert replay["events"] and replay["stages"][0] == "literature" and "QuaeraLabs" in replay["report"]


def test_branch_shows_parent_name(home):
    pid, _ = finished_project(home)
    c = TestClient(server.create_app())
    child = c.post(f"/api/projects/{pid}/branch", json={"at": 5}).json()["id"]
    s = c.get(f"/api/projects/{child}").json()
    assert s["branchOf"] == pid and s["branchAt"] == 5


def test_create_validates_input(home):
    c = TestClient(server.create_app())
    assert c.post("/api/projects", json={"question": "kısa", "domain": "math", "budget": 1}).status_code == 400
    assert c.post("/api/projects", json={"question": "Yeterince uzun bir soru mu?", "domain": "ml", "budget": 1}).status_code == 400
    assert c.post("/api/projects", json={"question": "Yeterince uzun bir soru mu?", "domain": "math", "budget": 999}).status_code == 400
    r = c.post("/api/projects", json={"question": "Uzun soru " * 200, "domain": "math", "budget": 1})
    assert r.status_code == 400 and "1500" in r.json()["error"]
    assert list(home.iterdir()) == []                      # reddedilen istek yarım proje bırakmaz


def test_human_message_reaches_agent_once(home):
    pid, orch = finished_project(home)
    c = TestClient(server.create_app())
    assert c.post(f"/api/projects/{pid}/messages", json={"to": "critic", "text": "Sınır durumlarını kontrol et."}).status_code == 201
    assert c.post(f"/api/projects/{pid}/messages", json={"to": "hacker", "text": "x"}).status_code == 400
    note = orch.human_notes("critic")
    assert "Sınır durumlarını kontrol et." in note
    assert orch.human_notes("critic") == ""          # yalnızca bir kez iletilir
    assert orch.human_notes("engineer") == ""         # başka role gitmez


def test_web_approver_waits_for_human_and_autonomy_modes(home):
    pid, orch = finished_project(home)
    ap = WebApprover(orch.store, "manual")
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("d", ap.decide("approve_experiment", "plan", 0.5)))
    t.start()
    for _ in range(50):
        if ap.pending:
            break
        threading.Event().wait(0.05)
    pending = next(iter(ap.pending.values()))
    assert orch.store.events("approval.pending")[-1]["payload"]["id"] == pending.id
    pending.decision = server.Decision(True, "manual", 0)
    pending.event.set()
    t.join(5)
    assert result["d"].approved and orch.store.events("approval.resolved")
    under = WebApprover(orch.store, "under", 1.0)
    assert under.decide("approve_experiment", "", 0.5).approved


def test_evidence_package_signed_and_tamper_evident(home):
    pid, _ = finished_project(home)
    data = package.build_package(home / pid, approver="test")
    assert package.verify_package(data) == []
    z = zipfile.ZipFile(io.BytesIO(data))
    crate = json.loads(z.read("ro-crate-metadata.json"))
    assert crate["@context"].endswith("1.1/context")
    manifest = json.loads(z.read("quaera-manifest.json"))
    assert "QuaeraLabs" in manifest["aiLabel"]["text"]

    def rewrite(name, fn):
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
            for n in src.namelist():
                dst.writestr(n, fn(src.read(n)) if n == name else src.read(n))
        return buf.getvalue()

    no_label = rewrite("quaera-manifest.json", lambda b: b.replace("QuaeraLabs AI agent team".encode(), b"some team"))
    assert package.verify_package(no_label)
    edited_report = rewrite("rapor.md", lambda b: b + b"\nekleme")
    assert any("modified" in p for p in package.verify_package(edited_report))


def test_cross_site_and_rebinding_requests_are_rejected(home):
    c = TestClient(server.create_app())
    body = '{"question": "Yeterince uzun bir soru mu?", "domain": "math", "budget": 1}'
    # basit (ön kontrolsüz) çapraz site isteği: text/plain gövde
    assert c.post("/api/projects", content=body, headers={"content-type": "text/plain"}).status_code == 403
    assert c.post("/api/projects", content=body, headers={"content-type": "application/json",
                                                           "origin": "https://kotu.example"}).status_code == 403
    assert c.get("/api/projects", headers={"host": "kotu.example:8765"}).status_code == 403
    assert c.get("/api/projects", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert c.get("/api/projects", headers={"origin": "http://127.0.0.1:8765", "sec-fetch-site": "same-origin"}).status_code == 200


def test_allowed_hosts_admit_a_private_proxy_name(home, monkeypatch):
    monkeypatch.setenv("QUAERA_ALLOWED_HOSTS", "lab.example.ts.net")
    c = TestClient(server.create_app())
    assert c.get("/api/projects", headers={"host": "lab.example.ts.net:8443", "origin": "https://lab.example.ts.net:8443",
                                           "sec-fetch-site": "same-origin"}).status_code == 200
    assert c.get("/api/projects", headers={"host": "kotu.example"}).status_code == 403
    assert c.get("/api/projects", headers={"host": "lab.example.ts.net", "origin": "https://kotu.example"}).status_code == 403


def test_report_problem_creates_local_triage_case(home):
    pid, _ = finished_project(home)
    c = TestClient(server.create_app())
    assert c.post(f"/api/projects/{pid}/triage", json={"note": "kısa", "expect": {"answer": "no"}}).status_code == 400
    r = c.post(f"/api/projects/{pid}/triage", json={"note": "Sonuç tek veri setine dayanıyor ama genellenmiş.",
                                                     "expect": {"mustObject": True}})
    assert r.status_code == 201 and (home.parent / "triage" / f"{r.json()['case']}.yaml").exists()


def scripted_build(monkeypatch):
    from quaera.gateway import ScriptedProvider
    from test_orchestrator import FakeTools
    real = cli.build

    def fake(project, budget, auto_limit, providers=None, autonomy=None, domain=None, memory=True):
        orch = real(project, budget, auto_limit, {"scripted": ScriptedProvider(Script(), "scripted", 0.001)}, autonomy, domain, memory)
        orch.tools = FakeTools(orch.permissions)
        return orch
    monkeypatch.setattr(server, "build", fake)


def test_branch_with_changes_runs_and_shows_in_tree(home, monkeypatch):
    pid, _ = finished_project(home)
    scripted_build(monkeypatch)
    c = TestClient(server.create_app())
    assert c.post(f"/api/projects/{pid}/branch", json={"kind": "hypothesis", "reason": "x"}).status_code == 400
    r = c.post(f"/api/projects/{pid}/branch", json={"kind": "approach", "reason": "Tümevarımla deneyelim.",
                                                     "note": "Tümevarım kullan.", "autonomy": "under", "autoLimit": 5})
    assert r.status_code == 201
    child = r.json()["id"]
    server.RUNNER.threads[child].join(30)
    t = c.get(f"/api/projects/{child}/tree").json()
    assert t["root"] == pid and {n["id"] for n in t["nodes"]} == {pid, child}
    node = next(n for n in t["nodes"] if n["id"] == child)
    assert node["branch"]["kind"] == "approach" and node["branch"]["reason"] == "Tümevarımla deneyelim."
    assert node["outcome"] == "supported" and t["stats"]["branches"] == 2
    assert c.post(f"/api/projects/{pid}/iterate", json={"maxBranches": 9, "budgetPerBranch": 1}).status_code == 400


def test_blob_endpoint_serves_agent_text_only_from_project(home):
    pid, orch = finished_project(home)
    said = orch.store.events("agent.said")[0]["payload"]
    c = TestClient(server.create_app())
    r = c.get(f"/api/projects/{pid}/blob/{said['sha256']}")
    assert r.status_code == 200 and r.text.startswith(said["preview"][:40])
    assert c.get(f"/api/projects/{pid}/blob/../../etc").status_code in (400, 404)
    assert c.get(f"/api/projects/{pid}/blob/{'0' * 64}").status_code == 404


def test_keep_trying_project_starts_the_iteration_loop_with_its_budget(home, monkeypatch):
    calls = []
    monkeypatch.setattr(server.RUNNER, "start", lambda *a, **k: calls.append("start"))
    c = TestClient(server.create_app())
    r = c.post("/api/projects", json={"question": "Is the sum of odd numbers a square?", "domain": "math", "budget": 3,
                                      "keepTrying": True})
    assert r.status_code == 201 and server.open_store(r.json()["id"]).meta("keepTrying") is True

    from test_tree import always_failing_proof
    orch = make(home, always_failing_proof(), name="kt")
    orch.store.set_meta("keepTrying", True)
    orch.store.set_meta("budgetCapUsd", 3.0)
    monkeypatch.setattr(server, "build", lambda path, budget, auto, **k: orch)
    runner = server.Runner()
    monkeypatch.setattr(runner, "iterate_loop", lambda pid, *a, **k: calls.append((pid, a, k)))
    runner.start("kt", "cap", 0.0)
    runner.threads["kt"].join(30)
    pid, args, kwargs = calls[-1]
    assert pid == "kt" and kwargs["total_budget"] == 3.0 and args[2:] == ("cap", 0.0)
