"""Hata incelemesi: işaretler, vaka kaydı, kişisel bilgi temizliği ve beklenti denetimi."""

import getpass
import io
import json
import zipfile

import pytest

from quaera import prompts, triage

from test_orchestrator import Script, make


def test_clean_run_has_no_signals_and_stopped_run_has(tmp_path):
    ok = make(tmp_path, Script(), name="temiz")
    ok.run()
    assert {s["kind"] for s in triage.signals(ok.store)} == {"citation_rejected"}   # betik bir sahte kaynak veriyor
    stopped = make(tmp_path, Script(), name="durdu", limit=0.0)   # onay sınırı 0: deney onaylanmaz, araştırma durur
    stopped.run()
    kinds = {s["kind"] for s in triage.signals(stopped.store)}
    assert "stopped" in kinds
    rows = triage.scan(tmp_path)
    assert {r["project"] for r in rows if any(s["kind"] == "stopped" for s in r["signals"])} == {"durdu"}


def test_add_case_validates_and_export_redacts(tmp_path):
    home = tmp_path / "projects"
    orch = make(home, Script(), name="vaka")
    orch.run()
    with pytest.raises(ValueError):
        triage.add_case(home, "vaka", "kısa", {"answer": "no"})
    with pytest.raises(ValueError):
        triage.add_case(home, "vaka", "Eleştirmen aşırı genellemeyi kaçırdı.", {"answer": "belki"})
    path = triage.add_case(home, "vaka", f"Eleştirmen aşırı genellemeyi kaçırdı; /home/{getpass.getuser()}/veri dosyasında.",
                           {"answer": "no", "mustObject": True})
    assert path.parent == tmp_path / "triage"
    z = zipfile.ZipFile(io.BytesIO(triage.export(home)))
    text = z.read(path.name).decode()
    assert "/home/" not in text and "Eleştirmen aşırı genellemeyi kaçırdı" in text


def test_check_flags_open_question_reported_as_answered(tmp_path):
    full = make(tmp_path, Script(), name="tam")
    full.run()
    assert triage.check({"expect": {"notAnswered": True}}, full.store)      # tam kapsamlı "destekleniyor" → işaretlenir
    script = Script(critic_objects_first=False)

    def restricted(system, prompt):
        out = script(system, prompt)
        if system == prompts.HYPOTHESIS:
            data = json.loads(out)
            data["hypotheses"][0]["scope"] = {"relation": "restricted", "note": "Yalnızca n ≤ 100 için."}
            return json.dumps(data)
        return out

    narrow = make(tmp_path, restricted, name="dar")
    narrow.run()
    assert triage.check({"expect": {"notAnswered": True, "mustStop": False}}, narrow.store) == []
