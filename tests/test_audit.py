"""Dürüstlük denetimi: temiz proje geçer; uydurma alıntı, kopuk atıf, sahte doğrulama ve değiştirilmiş rapor yakalanır."""

from quaera import audit

from test_ml_loop import MLScript, make as make_ml
from test_orchestrator import Script, make


def test_clean_math_project_passes(tmp_path):
    orch = make(tmp_path, Script(), name="temiz")
    orch.run()
    a = audit.audit_project(tmp_path / "temiz", online=False)
    assert a.findings == [] and a.checked["verifications"] == 1


def test_fabricated_citation_dangling_reference_and_tampering_are_caught(tmp_path):
    orch = make(tmp_path, Script(), name="kurcalanmis")
    orch.run()
    path = tmp_path / "kurcalanmis" / "rapor.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAyrıca bkz. arXiv:2501.99999 ve [RES-0099].\n", encoding="utf-8")
    kinds = {f.kind for f in audit.audit_project(tmp_path / "kurcalanmis", online=False).findings}
    assert kinds == {"unverified_citation", "dangling_reference", "tampered_report"}   # çevrimdışı: varlığı sorulmaz


def test_fake_ml_verification_is_caught(tmp_path):
    orch = make_ml(tmp_path, MLScript(plan_objection=False, broken_first=False))
    orch.run()
    assert audit.audit_project(tmp_path / "p", online=False).findings == []
    s = orch.store
    ver = s.latest("verification")[-1]
    vrun = s.get(ver["verificationRunIds"][0])
    s.put({**vrun, "metrics": {k: v + 0.01 for k, v in vrun["metrics"].items()}}, by=vrun["createdBy"])
    kinds = {f.kind for f in audit.audit_project(tmp_path / "p", online=False).findings}
    assert "fake_verification" in kinds


def test_literature_summary_cannot_smuggle_unverified_citations():
    from quaera.literature import scrub_unverified
    text = ("Bulunanlar: moderasyon (10.6339/jds.2009.07(3).462), etkileşim (10.48550/arxiv.1801.01003) "
            "ve arXiv:2009.02314; ayrıca 10.1007/978-981-97-0700-3_56.")
    out, removed = scrub_unverified(text, {"10.6339/jds.2009.07(3).462", "arXiv:1801.01003"})
    assert "10.6339/jds.2009.07(3).462" in out and "10.48550/arxiv.1801.01003" in out      # doğrulanmış (arXiv DOI biçimi dahil)
    assert removed == ["arXiv:2009.02314", "10.1007/978-981-97-0700-3_56"]
    assert out.count("[unverified source removed]") == 2


def test_lean_timeout_is_inconclusive_not_fake(tmp_path, monkeypatch):
    import quaera.lean as lean
    orch = make(tmp_path, Script(), name="yavas")
    orch.run()

    class SlowChecker:
        def __init__(self, **kw):
            pass

        def check(self, *a, **kw):
            return lean.LeanReport(compiled=False, verified=False, theorem=None, errors=["zaman aşımı"], timed_out=True)
    monkeypatch.setattr(lean, "LeanChecker", SlowChecker)
    a = audit.audit_project(tmp_path / "yavas", online=False, lean=True)
    assert a.findings == [] and a.checked.get("lean_timeout")
    assert audit.summary([a])["lean_inconclusive"] == 1 and audit.summary([a])["fake_verifications"] == 0
