"""Araştırma hafızası: dizinleme, arama, hata enjekte edilmiş projelerin dışlanması, ajana bağlam olarak verilmesi."""

from quaera.memory import LabMemory, summarize

from test_orchestrator import Script, make


def test_index_and_recall_finished_projects(tmp_path):
    a = make(tmp_path, Script(), name="tek-sayilar")
    a.run()
    mem = LabMemory(tmp_path / "memory.db")
    assert mem.index_project(tmp_path / "tek-sayilar")
    hits = mem.recall("What is the sum of the first n odd numbers?")
    assert hits and hits[0]["project"] == "tek-sayilar" and hits[0]["hypotheses"][0]["status"] == "supported"
    assert mem.recall("sum of odd numbers", exclude="tek-sayilar") == []
    assert mem.recall("Riemann zeta zeros") == []


def test_unfinished_and_fault_injected_projects_are_not_remembered(tmp_path):
    unfinished = make(tmp_path, Script(), name="yarim")
    assert summarize(unfinished.store, "yarim") is None
    faulty = make(tmp_path, Script(), name="hatali")
    faulty.run()
    faulty.store.append("fault.injected", faulty.det("director"), {"kind": "flip"})
    mem = LabMemory(tmp_path / "memory.db")
    assert not mem.index_project(tmp_path / "hatali")
    assert mem.rebuild(tmp_path) == 0


def test_hypothesis_agent_sees_memory_and_it_is_recorded(tmp_path):
    mem = LabMemory(tmp_path / "memory.db")
    first = make(tmp_path, Script(), name="birinci")
    first.memory = mem
    first.run()                                   # rapor aşamasında kendini dizine yazar
    script = Script()
    seen = []
    def spy(system, prompt):
        seen.append(prompt)
        return script(system, prompt)
    second = make(tmp_path, spy, name="ikinci")
    second.memory = mem
    second.run()
    recalled = second.store.events("memory.recalled")
    assert recalled and recalled[0]["payload"]["items"][0]["project"] == "birinci"
    assert any("Lab memory" in p and "[birinci]" in p and "must not be cited" in p for p in seen)
    # Hafıza kanıt değildir: ikinci projenin raporu birinci projenin nesnelerine bağlanmaz
    assert "birinci" not in (tmp_path / "ikinci" / "rapor.md").read_text(encoding="utf-8")
