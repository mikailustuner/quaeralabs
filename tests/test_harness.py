"""Capacity plan M1: the evaluation harness (set loading, hidden split, arms, caps, failure classes, discovery scoring)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

import _harness as h  # noqa: E402
from quaera.gateway import Gateway, Limits, ScriptedProvider  # noqa: E402
from quaera.permissions import Permissions  # noqa: E402

from test_discovery import make_discovery  # noqa: E402
from test_prover import STMT, fake_check  # noqa: E402

GOOD = "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  GOOD\n```"
BAD = "```lean\nimport Mathlib\n\ntheorem main (n : ℕ) : P n := by\n  BAD\n```"


def test_sets_load_and_the_hidden_split_is_disjoint():
    dev, hidden = h.load_set("capacity-synth"), h.load_set("capacity-synth", split="hidden")
    assert len(dev) + len(hidden) == 30 and not {t.name for t in dev} & {t.name for t in hidden}
    assert {t.tier for t in dev} == {1, 2, 3} and all(t.statement_file.endswith("sorry\n") for t in dev)
    assert not any(t.name.startswith("mathd") for t in h.load_set("minif2f-valid"))
    assert len(h.load_set("putnam", n=3)) == 3


def gateway(answer, calls=None):
    events = []
    gw = Gateway({"scripted": ScriptedProvider(answer)}, 5.0, Permissions.load().agents, lambda k, p: events.append({"kind": k, **p}),
                 limits=Limits(calls=calls))
    return gw, events


def test_run_set_scores_both_arms_with_caps_and_failure_classes():
    gw, events = gateway(lambda s, p: GOOD)
    tasks = [h.Task("main", STMT, 1), h.Task("main", STMT.replace("P n", "Q n"), 2)]
    out = h.run_set(tasks, ["before", "after"], gw, lambda t: (fake_check, None), per_task_usd=None, per_task_calls=4,
                    events=events, log=lambda m: None)
    assert out["arms"]["before"]["solved"] == 2 and out["arms"]["after"]["solved"] == 2
    assert out["arms"]["after"]["byTier"] == {"tier1": {"tasks": 1, "solved": 1}, "tier2": {"tasks": 1, "solved": 1}}
    assert all(r["failure"] == "solved" and r["calls"] >= 1 for rs in out["rows"].values() for r in rs)

    gw, events = gateway(lambda s, p: BAD)
    out = h.run_set([h.Task("main", STMT)], ["before"], gw, lambda t: (fake_check, None), per_task_usd=None, per_task_calls=3,
                    events=events, log=lambda m: None)
    row = out["rows"]["before"][0]
    assert not row["solved"] and row["calls"] == 3 and row["failure"] == "budget"


def test_classifier_names_each_failure():
    assert h.classify({"solved": False, "stopped": None, "errors": ["line 3: unsolved goals"]}, []) == "compile_error"
    assert h.classify({"solved": False, "stopped": None, "errors": ["timed out (300 s)"]}, []) == "timeout"
    assert h.classify({"solved": False, "stopped": None}, [{"kind": "model.error", "error": "answer was cut off at the 9000-token output limit"}]) == "output_cut"
    assert h.classify({"solved": False, "stopped": "model error: 500"}, []) == "model_error"
    assert h.classify({"solved": False, "stopped": None}, [{"kind": "model.invalid_json"}]) == "invalid_json"


def test_discovery_scoring(tmp_path):
    orch, _, _ = make_discovery(tmp_path, "score")
    orch.run()
    s = h.score_discovery(orch.store)
    assert s["verifiedLemmas"] >= 2 and s["refutedLemmas"] == 1 and s["mainSolved"] and s["lemmas"] >= 3
