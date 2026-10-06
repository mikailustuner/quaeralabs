"""Failure review: signals, case records, personal data redaction and expectation checks."""

import getpass
import io
import json
import zipfile

import pytest

from quaera import prompts, triage

from test_orchestrator import Script, make


def test_clean_run_has_no_signals_and_stopped_run_has(tmp_path):
    ok = make(tmp_path, Script(), name="clean")
    ok.run()
    assert {s["kind"] for s in triage.signals(ok.store)} == {"citation_rejected"}   # the script supplies one fake source
    stopped = make(tmp_path, Script(), name="halted", limit=0.0)   # approval limit 0: the experiment is not approved, research stops
    stopped.run()
    kinds = {s["kind"] for s in triage.signals(stopped.store)}
    assert "stopped" in kinds
    rows = triage.scan(tmp_path)
    assert {r["project"] for r in rows if any(s["kind"] == "stopped" for s in r["signals"])} == {"halted"}


def test_add_case_validates_and_export_redacts(tmp_path):
    home = tmp_path / "projects"
    orch = make(home, Script(), name="failcase")
    orch.run()
    with pytest.raises(ValueError):
        triage.add_case(home, "failcase", "short", {"answer": "no"})
    with pytest.raises(ValueError):
        triage.add_case(home, "failcase", "The Critic missed an overgeneralization.", {"answer": "maybe"})
    path = triage.add_case(home, "failcase", f"The Critic missed an overgeneralization; in the /home/{getpass.getuser()}/data file.",
                           {"answer": "no", "mustObject": True})
    assert path.parent == tmp_path / "triage"
    z = zipfile.ZipFile(io.BytesIO(triage.export(home)))
    text = z.read(path.name).decode()
    assert "/home/" not in text and "The Critic missed an overgeneralization" in text


def test_check_flags_open_question_reported_as_answered(tmp_path):
    full = make(tmp_path, Script(), name="full")
    full.run()
    assert triage.check({"expect": {"notAnswered": True}}, full.store)      # full-scope "supported" → flagged
    script = Script(critic_objects_first=False)

    def restricted(system, prompt):
        out = script(system, prompt)
        if system == prompts.HYPOTHESIS:
            data = json.loads(out)
            data["hypotheses"][0]["scope"] = {"relation": "restricted", "note": "Only for n ≤ 100."}
            return json.dumps(data)
        return out

    narrow = make(tmp_path, restricted, name="narrow")
    narrow.run()
    assert triage.check({"expect": {"notAnswered": True, "mustStop": False}}, narrow.store) == []
