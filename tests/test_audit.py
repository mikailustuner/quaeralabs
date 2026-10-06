"""Honesty audit: a clean project passes; fabricated citations, dangling references, fake verification and a tampered report are caught."""

from quaera import audit

from test_ml_loop import MLScript, make as make_ml
from test_orchestrator import Script, make


def test_clean_math_project_passes(tmp_path):
    orch = make(tmp_path, Script(), name="clean")
    orch.run()
    a = audit.audit_project(tmp_path / "clean", online=False)
    assert a.findings == [] and a.checked["verifications"] == 1


def test_fabricated_citation_dangling_reference_and_tampering_are_caught(tmp_path):
    orch = make(tmp_path, Script(), name="tampered")
    orch.run()
    path = tmp_path / "tampered" / "report.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nSee also arXiv:2501.99999 and [RES-0099].\n", encoding="utf-8")
    kinds = {f.kind for f in audit.audit_project(tmp_path / "tampered", online=False).findings}
    assert kinds == {"unverified_citation", "dangling_reference", "tampered_report"}   # offline: existence is not queried


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
    text = ("Findings: moderation (10.6339/jds.2009.07(3).462), interaction (10.48550/arxiv.1801.01003) "
            "and arXiv:2009.02314; also 10.1007/978-981-97-0700-3_56.")
    out, removed = scrub_unverified(text, {"10.6339/jds.2009.07(3).462", "arXiv:1801.01003"})
    assert "10.6339/jds.2009.07(3).462" in out and "10.48550/arxiv.1801.01003" in out      # verified (including the arXiv DOI form)
    assert removed == ["arXiv:2009.02314", "10.1007/978-981-97-0700-3_56"]
    assert out.count("[unverified source removed]") == 2


def test_lean_timeout_is_inconclusive_not_fake(tmp_path, monkeypatch):
    import quaera.lean as lean
    orch = make(tmp_path, Script(), name="slow")
    orch.run()

    class SlowChecker:
        def __init__(self, **kw):
            pass

        def check(self, *a, **kw):
            return lean.LeanReport(compiled=False, verified=False, theorem=None, errors=["timeout"], timed_out=True)
    monkeypatch.setattr(lean, "LeanChecker", SlowChecker)
    a = audit.audit_project(tmp_path / "slow", online=False, lean=True)
    assert a.findings == [] and a.checked.get("lean_timeout")
    assert audit.summary([a])["lean_inconclusive"] == 1 and audit.summary([a])["fake_verifications"] == 0


def test_report_file_prefers_report_md_and_still_reads_the_old_name(tmp_path):
    from quaera.store import report_file
    assert report_file(tmp_path) == tmp_path / "report.md"
    (tmp_path / "rapor.md").write_text("old")
    assert report_file(tmp_path).read_text() == "old"
    (tmp_path / "report.md").write_text("new")
    assert report_file(tmp_path).read_text() == "new"
