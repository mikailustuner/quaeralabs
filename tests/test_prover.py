"""#2 ispat araması (Lean'siz, kurallı sahte denetleyiciyle): otomasyon, model hatasına dayanıklılık, taslak + lemmalar."""

import re

from quaera import prompts
from quaera.gateway import ModelError
from quaera.prover import AUTOMATION, prove_search

STMT = "import Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  sorry\n"


def block(src, name):
    m = re.search(rf"(?:theorem|lemma)\s+{name}\b(.*?)(?=\n(?:lemma|theorem)\s|\Z)", src, re.S)
    return m.group(0) if m else ""


def fake_check(src, name=None, approved=None):
    """Kurallar: EASY içeren ifade otomasyonla çözülür; 'GOOD' ispat geçer; 'BAD' geçmez; ana teorem için dosyada sorry kalmamalı."""
    name = name or "main"
    b = block(src, name)
    problems = []
    if ("sorry" in src if name == "main" else "sorry" in b):
        problems.append("Proof contains `sorry`.")
    ok_proof = ("GOOD" in b) or (AUTOMATION in b and "EASY" in b.split(":=")[0]) or (name == "main" and "use_lemmas" in b)
    compiled = "SYNTAXERR" not in src
    verified = compiled and not problems and ok_proof and "BAD" not in b
    return {"compiled": compiled, "verified": verified, "errors": [] if compiled else ["satır 1: hata"], "problems": problems}


class Asker:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, system, prompt, n):
        self.calls.append(system)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_automation_needs_no_model():
    easy = STMT.replace("P n", "EASY n")
    ask = Asker([])
    r = prove_search(easy, "main", ask, fake_check)
    assert r.solved and ask.calls == [] and r.log[0]["kind"] == "automation"


def test_model_overflow_does_not_stop_the_search():
    ask = Asker([ModelError("Claude's response exceeded the 16000 output token maximum"),
                 "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  GOOD\n```"])
    r = prove_search(STMT, "main", ask, fake_check, attempts=2)
    assert r.solved and r.log[1]["kind"] == "model_error" and r.attempts == 2


SKETCH = """```lean
import Mathlib

lemma quaera_step_1 (n : ℕ) : Q n := by sorry

lemma quaera_step_2 (n : ℕ) : EASY n := by sorry

theorem main (n : ℕ) : P n := by
  use_lemmas
```"""


def test_sketch_lemmas_are_proved_separately_and_assembled():
    lemma1 = ("```lean\nimport Mathlib\n\nlemma quaera_step_1 (n : ℕ) : Q n := by\n  GOOD\n\n"
              "lemma quaera_step_2 (n : ℕ) : EASY n := by sorry\n\ntheorem main (n : ℕ) : P n := by\n  use_lemmas\n```")
    ask = Asker(["```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  BAD\n```",     # bütün ispat denemesi 1
                 "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  BAD\n```",     # bütün ispat denemesi 2
                 SKETCH, lemma1])
    r = prove_search(STMT, "main", ask, fake_check, attempts=2)
    assert r.solved, r.log
    assert ask.calls.count(prompts.SKETCH.replace("`quaera_main`", "`main`")) == 1
    assert "GOOD" in r.source and AUTOMATION in r.source and "sorry" not in r.source
    kinds = [e["kind"] for e in r.log]
    assert kinds.index("sketch") < kinds.index("lemma") < kinds.index("assembled")


def test_failed_lemma_triggers_one_new_sketch():
    bad_lemma = SKETCH.replace("lemma quaera_step_1 (n : ℕ) : Q n := by sorry", "lemma quaera_step_1 (n : ℕ) : R n := by sorry")
    reply_bad = "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  BAD\n```"
    ask = Asker([reply_bad, reply_bad, bad_lemma, "```lean\nlemma quaera_step_1 (n : ℕ) : R n := by\n  BAD\n```",
                 "```lean\nlemma quaera_step_1 (n : ℕ) : R n := by\n  BAD\n```", reply_bad.replace("BAD", "SYNTAXERR")])
    r = prove_search(STMT, "main", ask, fake_check, attempts=2, sketches=2)
    assert not r.solved
    assert ask.calls.count(prompts.SKETCH.replace("`quaera_main`", "`main`")) == 2       # ikinci taslak istendi


def test_orchestrator_proof_search_keeps_budget_for_later_stages(tmp_path):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_tree import always_failing_proof
    from test_orchestrator import make
    orch = make(tmp_path, always_failing_proof(), cap=1.0, cost=0.05)   # her çağrı 0,05 $; ispat hiç bulunamaz
    orch.run()
    searched = orch.store.events("proof.search")[-1]["payload"]
    assert not searched["solved"]
    assert orch.state("report_path") and orch.gateway.spent_usd <= 1.0 + 1e-9
    assert orch.store.events("report.written")          # bütçe ispat aramasında tükenmedi; rapor yazıldı


def test_first_round_writes_two_candidates_in_parallel():
    lanes, steps = [], []

    def ask(system, prompt, n, lane=None):
        lanes.append(lane)
        good = lane == "B"   # yalnızca adımlara bölünmüş aday doğru
        return f"```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  {'GOOD' if good else 'BAD'}\n```"
    r = prove_search(STMT, "main", ask, fake_check, attempts=2, parallel=2,
                     progress=lambda step, status, detail="", lane=None: steps.append((step, status, lane)))
    assert r.solved and sorted(lanes) == ["A", "B"] and r.log[-1]["lane"] == "B"
    assert ("Compiling with Lean", "done", "B") in steps and any(s[1] == "fail" and s[2] == "A" for s in steps)
