"""Doğrulayıcının hem geçerli örnekleri kabul ettiğini hem de her kuralı ihlal eden
bozuk kopyaları reddettiğini kontrol eder."""

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
from quaera import contracts as validate  # noqa: E402

ML = json.loads((ROOT / "examples" / "ml-isinma.json").read_text(encoding="utf-8"))
MATH = json.loads((ROOT / "examples" / "matematik-tek-sayilar.json").read_text(encoding="utf-8"))


def obj(bundle, id_):
    return next(o for o in bundle["objects"] if o["id"] == id_)


def mutated(bundle, fn):
    b = copy.deepcopy(bundle)
    fn(b)
    return validate.check_bundle(b, "test")


def test_repo_is_valid():
    assert validate.run_all() == []


@pytest.mark.parametrize("bundle", [ML, MATH])
def test_examples_valid(bundle):
    assert validate.check_bundle(bundle, "test") == []


def expect(errors, fragment):
    assert errors, "hata bekleniyordu"
    assert any(fragment in e for e in errors), errors


def test_dangling_reference():
    expect(mutated(ML, lambda b: obj(b, "RES-0031").update(experimentId="E-9999")), "nonexistent object E-9999")


def test_duplicate_id():
    expect(mutated(ML, lambda b: b["objects"].append(copy.deepcopy(obj(b, "Q-0012")))), "defined twice")


def test_tampered_preregistration():
    expect(mutated(ML, lambda b: obj(b, "PRE-0031").update(successCriterion="Herhangi bir fark yeterli.")), "contentHash")


def test_full_run_without_pilot():
    expect(mutated(ML, lambda b: b["objects"].remove(obj(b, "RUN-0001"))), "pilot")


def test_run_before_preregistration_lock():
    expect(mutated(ML, lambda b: obj(b, "RUN-0002").update(startedAt="2026-11-02T11:00:00Z")), "before the preregistration was locked")


def test_supported_without_verification():
    expect(mutated(ML, lambda b: obj(b, "VER-0031").update(reproduced="partial")), "passed the Verifier")


def test_supported_with_open_critique():
    def f(b):
        b["objects"].append({
            "id": "CR-0099", "type": "critique", "revision": 1, "createdAt": "2026-11-04T10:00:00Z",
            "createdBy": obj(b, "CR-0001")["createdBy"], "targetId": "RES-0031", "category": "leakage",
            "severity": "blocking", "body": "Doğrulama verisi eğitim verisiyle örtüşüyor olabilir.", "status": "open",
        })
    expect(mutated(ML, f), "an open objection")


def test_verification_by_wrong_role():
    expect(mutated(ML, lambda b: obj(b, "VER-0031")["createdBy"].update(role="engineer")), "someone other than the Verifier")


def test_cross_model_claim_false():
    expect(mutated(ML, lambda b: obj(b, "VER-0031")["createdBy"].update(modelFamily="anthropic")), "same model family")


def test_unanswered_objection():
    expect(mutated(ML, lambda b: b["objects"].remove(obj(b, "MSG-0002"))), "unanswered")


def test_lean_verified_without_lean_output():
    expect(mutated(MATH, lambda b: obj(b, "ART-0002").update(kind="log")), "not a Lean output")


def test_lean_verified_with_sorry_is_schema_error():
    expect(mutated(MATH, lambda b: obj(b, "RES-0001")["leanProof"].update(sorryFree=False)), "RES-0001")


def test_hypothesis_accepted_without_human_approval():
    expect(mutated(ML, lambda b: obj(b, "H-0002").pop("approval")), "H-0002")


def test_approval_by_agent_is_schema_error():
    expect(mutated(ML, lambda b: obj(b, "E-0031")["approval"].update(by=obj(b, "RUN-0002")["createdBy"])), "E-0031")


def test_approved_experiment_needs_novelty_check():
    expect(mutated(ML, lambda b: obj(b, "E-0031").pop("noveltyCheck")), "E-0031")


def test_debate_round_limit():
    expect(mutated(ML, lambda b: obj(b, "MSG-0002").update(round=4)), "MSG-0002")


# --- Ajan ve skill kuralları -------------------------------------------------

def agents_with(tmp_path, fn):
    agents = tmp_path / "agents"
    shutil_copy(ROOT / "agents", agents)
    fn(agents)
    return validate.check_agents_and_skills(agents, ROOT / "skills")


def shutil_copy(src, dst):
    dst.mkdir()
    for p in src.glob("*.yaml"):
        (dst / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")


def edit(path, fn):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_agent_cannot_publish(tmp_path):
    errs = agents_with(tmp_path, lambda d: edit(d / "writer.yaml", lambda a: a["permissions"].update(publish=True)))
    expect(errs, "writer.yaml")


def test_verifier_cannot_write_code(tmp_path):
    errs = agents_with(tmp_path, lambda d: edit(d / "verifier.yaml", lambda a: a["permissions"].update(codeWrite="sandbox")))
    expect(errs, "Verifier cannot write code")


def test_skill_exceeding_agent_permissions(tmp_path):
    errs = agents_with(tmp_path, lambda d: edit(d / "analyst.yaml", lambda a: a["skills"].append("gpu-job-submit")))
    expect(errs, "gpuSpend")


def test_unknown_skill(tmp_path):
    errs = agents_with(tmp_path, lambda d: edit(d / "hypothesis.yaml", lambda a: a["skills"].append("missing-skill")))
    expect(errs, "undefined skill")


def test_missing_role(tmp_path):
    errs = agents_with(tmp_path, lambda d: (d / "writer.yaml").unlink())
    expect(errs, "nine roles")


def test_ai_label_must_name_quaeralabs():
    pkg = json.loads((ROOT / "examples" / "packages" / "ml-isinma.manifest.json").read_text(encoding="utf-8"))
    pkg["aiLabel"]["text"] = "Bu araştırma AI ile üretildi."
    assert validate.schema_errors("evidence-package", pkg, "test")


def test_concurrent_writers_from_two_connections_get_distinct_ids(tmp_path):
    """Sunucu isteği (insan mesajı) ile araştırma iş parçacığı aynı veritabanına ayrı bağlantılardan yazar: kimlik çakışmamalı."""
    import threading

    from quaera.permissions import Permissions
    from quaera.store import Store

    perms = Permissions.load()
    path = tmp_path / "quaera.db"
    first = Store(path, perms)
    q = first.put({"type": "question", "createdBy": {"kind": "human", "userId": "u"}, "title": "Soru nedir burada?",
                   "domain": "math", "scope": "x"})
    errors = []

    def writer():
        s = Store(path, perms)
        try:
            for i in range(25):
                s.put({"type": "message", "createdBy": {"kind": "human", "userId": "u"}, "kind": "proposal", "to": "director",
                       "subjectId": q["id"], "body": f"not {i}"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=writer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[0]
    assert len({m["id"] for m in first.latest("message")}) == 75
