"""Research memory: indexing, search, excluding fault-injected projects, passing memory to agents as context."""

import math
import re

from quaera.memory import LabMemory, summarize

from test_orchestrator import Script, make


def test_index_and_recall_finished_projects(tmp_path):
    a = make(tmp_path, Script(), name="odd-numbers")
    a.run()
    mem = LabMemory(tmp_path / "memory.db")
    assert mem.index_project(tmp_path / "odd-numbers")
    hits = mem.recall("What is the sum of the first n odd numbers?")
    assert hits and hits[0]["project"] == "odd-numbers" and hits[0]["hypotheses"][0]["status"] == "supported"
    assert mem.recall("sum of odd numbers", exclude="odd-numbers") == []
    assert mem.recall("Riemann zeta zeros") == []


def test_unfinished_and_fault_injected_projects_are_not_remembered(tmp_path):
    unfinished = make(tmp_path, Script(), name="unfinished")
    assert summarize(unfinished.store, "unfinished") is None
    faulty = make(tmp_path, Script(), name="faulty")
    faulty.run()
    faulty.store.append("fault.injected", faulty.det("director"), {"kind": "flip"})
    mem = LabMemory(tmp_path / "memory.db")
    assert not mem.index_project(tmp_path / "faulty")
    assert mem.rebuild(tmp_path) == 0


def test_hypothesis_agent_sees_memory_and_it_is_recorded(tmp_path):
    mem = LabMemory(tmp_path / "memory.db")
    first = make(tmp_path, Script(), name="alpha-proj")
    first.memory = mem
    first.run()                                   # indexes itself at the report stage
    script = Script()
    seen = []
    def spy(system, prompt):
        seen.append(prompt)
        return script(system, prompt)
    second = make(tmp_path, spy, name="beta-proj")
    second.memory = mem
    second.run()
    recalled = second.store.events("memory.recalled")
    assert recalled and recalled[0]["payload"]["items"][0]["project"] == "alpha-proj"
    assert any("Lab memory" in p and "[alpha-proj]" in p and "must not be cited" in p for p in seen)
    # Memory is not evidence: the second project's report does not link to the first project's objects
    assert "alpha-proj" not in (tmp_path / "beta-proj" / "report.md").read_text(encoding="utf-8")


# Fake multilingual embedder: words map to language-independent concepts, so "tek sayıların toplamı" and
# "sum of odd numbers" land on the same vector while bm25 sees no shared word.
CONCEPTS = {"odd": 0, "tek": 0, "sum": 1, "topla": 1, "number": 2, "sayı": 2, "riemann": 3, "zeta": 3}


class FakeEmbedder:
    name = "fake-v1"

    def __init__(self):
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 4
            for w in re.findall(r"\w+", t.lower()):
                for key, dim in CONCEPTS.items():
                    if w.startswith(key):
                        v[dim] += 1
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def test_hybrid_recall_crosses_languages(tmp_path):
    make(tmp_path, Script(), name="odd-numbers").run()
    bm25_only = LabMemory(tmp_path / "bm25.db", embedder=False)
    bm25_only.index_project(tmp_path / "odd-numbers")
    assert bm25_only.recall("Tek sayıların toplamı nedir?") == []          # no shared word across languages
    mem = LabMemory(tmp_path / "memory.db", embedder=FakeEmbedder())
    mem.index_project(tmp_path / "odd-numbers")
    assert [h["project"] for h in mem.recall("Tek sayıların toplamı nedir?")] == ["odd-numbers"]
    assert mem.recall("Riemann zeta zeros") == []                          # below the similarity floor
    assert mem.recall("tek sayıların toplamı", exclude="odd-numbers") == []


def test_vectors_are_backfilled_and_recomputed_when_the_model_changes(tmp_path):
    make(tmp_path, Script(), name="odd-numbers").run()
    LabMemory(tmp_path / "memory.db", embedder=False).index_project(tmp_path / "odd-numbers")   # indexed without vectors
    fake = FakeEmbedder()
    mem = LabMemory(tmp_path / "memory.db", embedder=fake)
    assert mem.recall("tek sayıların toplamı")[0]["project"] == "odd-numbers" and fake.calls == 2   # backfill + query
    mem.recall("tek sayıların toplamı")
    assert fake.calls == 3                                                  # vector reused, only the query embedded
    other = FakeEmbedder()
    other.name = "fake-v2"
    assert LabMemory(tmp_path / "memory.db", embedder=other).recall("tek sayıların toplamı") and other.calls == 2


def test_embedder_failure_falls_back_to_bm25(tmp_path):
    make(tmp_path, Script(), name="odd-numbers").run()

    def broken(texts):
        raise OSError("no network")
    broken.name = "broken"
    mem = LabMemory(tmp_path / "memory.db", embedder=broken)
    assert mem.index_project(tmp_path / "odd-numbers")
    assert mem.recall("sum of the first n odd numbers")[0]["project"] == "odd-numbers" and mem.embedder is None
