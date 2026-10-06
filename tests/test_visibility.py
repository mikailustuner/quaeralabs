"""Visibility: what agents say, live steps and Hypothesis agents running in parallel are recorded."""

from test_orchestrator import Script, make


def test_agents_speech_activity_and_parallel_lanes_are_recorded(tmp_path):
    orch = make(tmp_path, Script(critic_objects_first=False))
    orch.run()
    s = orch.store
    said = [e["payload"] for e in s.events("agent.said")]
    hyp = [p for p in said if p["role"] == "hypothesis"]
    assert {p["lane"] for p in hyp} == {"A", "B"} and all(p["purpose"] == "HYPOTHESIS" for p in hyp)
    full = s.blob(hyp[0]["sha256"]).decode()
    assert full.startswith(hyp[0]["preview"][:50]) and len(full) == hyp[0]["chars"]
    purposes = {p["purpose"] for p in said}
    assert {"LITERATURE_PLAN", "HYPOTHESIS", "RANK_HYPOTHESES", "FORMALIZE", "BACKTRANSLATE", "CRITIC_STATEMENT", "PROVE"} <= purposes
    acts = [e["payload"] for e in s.events("activity")]
    assert any(a["status"] == "start" for a in acts) and any(a["status"] == "done" for a in acts)
    assert any("Compiling with Lean" in a["step"] for a in acts)          # the proof search reports its own steps
    steps = s.events("proof.search")[-1]["payload"]["steps"]
    assert all("source" not in st for st in steps) and any(st.get("sha256") for st in steps)
