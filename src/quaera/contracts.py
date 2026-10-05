"""QuaeraLabs sözleşmeleri: şemalar ve şemanın tek başına ifade edemediği kurallar.

Referans bütünlüğü, ön kayıt özeti, pilot zorunluluğu, Eleştirmen/Doğrulayıcı
kapısı ve izin tutarlılığı burada kontrol edilir. Aynı kurallar hem
`tools/validate.py` tarafından repo üzerinde hem de veri deposu tarafından her
yazmada uygulanır.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v1"

TYPE_TO_SCHEMA = {
    "question": "question",
    "hypothesis": "hypothesis",
    "preregistration": "preregistration",
    "experiment": "experiment",
    "run": "run",
    "result": "result",
    "critique": "critique",
    "evidence_link": "evidence-link",
    "verification": "verification",
    "artifact": "artifact",
    "message": "message",
}

ROLES = {
    "director", "literature", "hypothesis", "experiment_designer",
    "engineer", "analyst", "critic", "verifier", "writer", "manager",
}

# Ön kayıtta kilitlenen alanlar; contentHash bunların kanonik JSON özetidir.
PREREG_LOCKED_FIELDS = ("hypothesisId", "primaryMetric", "successCriterion", "analysisPlan", "seeds")

# Bir nesnenin başka nesnelere referans veren alanları.
REF_FIELDS = (
    "questionId", "hypothesisId", "experimentId", "resultId", "targetId", "subjectId",
    "inReplyTo", "preregistrationId", "logs", "parentId",
)
REF_LIST_FIELDS = ("hypothesisIds", "runIds", "verificationRunIds", "derivedFrom")

# İzin seviyeleri: bir skill, ajanın seviyesini aşan izin isteyemez.
LEVELS = {
    "codeWrite": ["none", "analysis_scripts", "sandbox"],
    "codeExecute": ["none", "readonly_checks", "sandbox_no_gpu", "sandbox", "clean_sandbox"],
    "gpuSpend": ["none", "within_approved_plan"],
}


def load_registry() -> tuple[Registry, dict[str, dict]]:
    schemas: dict[str, dict] = {}
    registry = Registry()
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        name = path.name.removesuffix(".schema.json")
        schemas[name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry, schemas


REGISTRY, SCHEMAS = load_registry()


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker())


def schema_errors(name: str, instance: object, where: str) -> list[str]:
    return [
        f"{where}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in sorted(validator(name).iter_errors(instance), key=lambda e: list(e.absolute_path))
    ]


def check_schemas() -> list[str]:
    errors = []
    for name, schema in SCHEMAS.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # şema bozuksa geri kalan her şey anlamsız
            errors.append(f"schema {name}: {exc}")
    return errors


def prereg_hash(prereg: dict) -> str:
    locked = {k: prereg[k] for k in PREREG_LOCKED_FIELDS if k in prereg}
    canonical = json.dumps(locked, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_bundle(bundle: dict, where: str, final: bool = True) -> list[str]:
    """final=False: araştırma sürerken geçici olarak sağlanamayan kurallar (ör. henüz
    cevaplanmamış itiraz) atlanır."""
    errors = schema_errors("bundle", bundle, where)
    if errors:
        return errors

    objs = bundle["objects"]
    for i, obj in enumerate(objs):
        errors += schema_errors(TYPE_TO_SCHEMA[obj["type"]], obj, f"{where} [{obj.get('id', i)}]")
    if errors:
        return errors

    by_id: dict[str, dict] = {}
    for obj in objs:
        if obj["id"] in by_id:
            errors.append(f"{where}: {obj['id']} is defined twice")
        by_id[obj["id"]] = obj

    def of_type(t: str) -> list[dict]:
        return [o for o in objs if o["type"] == t]

    # 1. Referans bütünlüğü
    for obj in objs:
        refs = [obj[f] for f in REF_FIELDS if f in obj]
        refs += [r for f in REF_LIST_FIELDS for r in obj.get(f, [])]
        if "leanProof" in obj and "compilerOutput" in obj["leanProof"]:
            refs.append(obj["leanProof"]["compilerOutput"])
        if "resolution" in obj and "followUpId" in obj["resolution"]:
            refs.append(obj["resolution"]["followUpId"])
        for ref in refs:
            if ref not in by_id:
                errors.append(f"{where}: {obj['id']} references nonexistent object {ref}")
    if errors:
        return errors

    # 2. Ön kayıt: özet doğru olmalı
    for pre in of_type("preregistration"):
        if pre["contentHash"] != prereg_hash(pre):
            errors.append(f"{where}: {pre['id']} contentHash does not match the locked fields (preregistration was modified)")

    # 3. Deneyler: onaylı deney ön kayda bağlı, tam çalıştırmalar ön kayıttan ve başarılı bir pilottan sonra
    for exp in of_type("experiment"):
        runs = sorted((r for r in of_type("run") if r["experimentId"] == exp["id"]), key=lambda r: r["startedAt"])
        if "preregistrationId" in exp:
            pre = by_id[exp["preregistrationId"]]
            if pre["type"] != "preregistration":
                errors.append(f"{where}: {exp['id']} preregistrationId is not a preregistration")
            elif pre["hypothesisId"] not in exp["hypothesisIds"]:
                errors.append(f"{where}: {exp['id']} preregistration belongs to another hypothesis")
            for run in runs:
                if run["kind"] != "verification" and run["startedAt"] < pre["lockedAt"]:
                    errors.append(f"{where}: {run['id']} started before the preregistration was locked")
        full_runs = [r for r in runs if r["kind"] == "full"]
        if full_runs:
            first_full = full_runs[0]["startedAt"]
            pilots_ok = [r for r in runs if r["kind"] == "pilot" and r["status"] == "succeeded" and r["startedAt"] < first_full]
            if not pilots_ok:
                errors.append(f"{where}: {exp['id']} moved to full runs without a successful pilot run")
        for run in runs:
            if run["kind"] == "verification" and run["createdBy"].get("role") != "verifier":
                errors.append(f"{where}: {run['id']} verification run was started by someone other than the Verifier")

    # 4. Lean: 'verified' yalnızca lean_output artefaktıyla
    for res in of_type("result"):
        proof = res.get("leanProof")
        if proof and proof.get("verified"):
            art = by_id[proof["compilerOutput"]]
            if art["type"] != "artifact" or art["kind"] != "lean_output":
                errors.append(f"{where}: {res['id']} claims verified but compilerOutput is not a Lean output")

    # 5. Doğrulama: rol ve çapraz model iddiası tutarlı olmalı
    for ver in of_type("verification"):
        if ver["createdBy"].get("role") != "verifier":
            errors.append(f"{where}: {ver['id']} was created by someone other than the Verifier")
        if ver["crossModel"]:
            res = by_id[ver["resultId"]]
            producer_families = {by_id[r]["createdBy"].get("modelFamily") for r in res["runIds"]}
            if ver["createdBy"].get("modelFamily") in producer_families:
                errors.append(f"{where}: {ver['id']} claims cross-model but is in the same model family as the producing agent")

    # 6. Kapı: desteklendi/çürütüldü durumu için doğrulanmış kanıt ve açık itiraz olmaması
    open_targets = {c["targetId"] for c in of_type("critique") if c["status"] == "open"}
    for hyp in of_type("hypothesis"):
        if hyp["status"] not in ("supported", "refuted"):
            continue
        links = [l for l in of_type("evidence_link") if l["hypothesisId"] == hyp["id"]]
        wanted = "supports" if hyp["status"] == "supported" else "contradicts"
        verified = [
            l for l in links
            if l["relation"] == wanted
            and any(v["resultId"] == l["resultId"] and v["reproduced"] == "yes" for v in of_type("verification"))
        ]
        if not verified:
            errors.append(f"{where}: {hyp['id']} is '{hyp['status']}' but has no '{wanted}' evidence that passed the Verifier")
        blocked = {hyp["id"]} | {l["resultId"] for l in links}
        if blocked & open_targets:
            errors.append(f"{where}: {hyp['id']} is '{hyp['status']}' but has an open objection")

    # 7. İletişim: soru ve itirazlar cevapsız kalamaz (yalnızca araştırma kapanırken)
    if not final:
        return errors
    replied = {m["inReplyTo"] for m in of_type("message") if "inReplyTo" in m}
    for msg in of_type("message"):
        if msg.get("requiresResponse") and msg["id"] not in replied:
            errors.append(f"{where}: {msg['id']} ({msg['kind']}) was left unanswered")

    return errors


def load_yaml_dir(path: Path) -> dict[str, dict]:
    return {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(path.glob("*.yaml"))}


def check_agents_and_skills(agents_dir: Path = ROOT / "agents", skills_dir: Path = ROOT / "skills") -> list[str]:
    errors: list[str] = []
    skills = load_yaml_dir(skills_dir)
    for name, skill in skills.items():
        errors += schema_errors("skill", skill, f"skills/{name}.yaml")
        if skill.get("id") != name:
            errors.append(f"skills/{name}.yaml: id must match the file name")
    agents = load_yaml_dir(agents_dir)
    for name, agent in agents.items():
        errors += schema_errors("agent", agent, f"agents/{name}.yaml")
    if errors:
        return errors

    roles = [a["role"] for a in agents.values()]
    if sorted(roles) != sorted(ROLES):
        errors.append(f"agents/: each of the nine roles must be defined exactly once, found: {sorted(roles)}")

    for name, agent in agents.items():
        where = f"agents/{name}.yaml"
        perms = agent["permissions"]
        for sid in agent["skills"]:
            if sid not in skills:
                errors.append(f"{where}: undefined skill '{sid}'")
                continue
            need = skills[sid]["requiredPermissions"]
            if need.get("literatureRead") and not perms["literatureRead"]:
                errors.append(f"{where}: '{sid}' requires literature read permission, which the agent lacks")
            for key, order in LEVELS.items():
                if key in need and order.index(need[key]) > order.index(perms[key]):
                    errors.append(f"{where}: '{sid}' requires {key}={need[key]}, agent has {perms[key]}")
        if "approval_request" in perms["sendMessages"] and not perms["requestApproval"]:
            errors.append(f"{where}: can send approval requests but requestApproval is off")
        if agent["requiresApproval"] and not perms["requestApproval"]:
            errors.append(f"{where}: has actions requiring approval but cannot request approval")
        if agent["role"] == "verifier" and perms["codeWrite"] != "none":
            errors.append(f"{where}: the Verifier cannot write code")
        if agent["role"] == "verifier" and perms["codeExecute"] != "clean_sandbox":
            errors.append(f"{where}: the Verifier only executes in a clean sandbox")
        if perms["gpuSpend"] != "none" and "extra_spend" not in agent["requiresApproval"]:
            errors.append(f"{where}: an agent that can spend GPU must request approval for unplanned spending")
    return errors


def check_packages(path: Path = ROOT / "examples" / "packages") -> list[str]:
    errors = []
    for p in sorted(path.glob("*.json")):
        errors += schema_errors("evidence-package", json.loads(p.read_text(encoding="utf-8")), f"examples/packages/{p.name}")
    return errors


def run_all() -> list[str]:
    errors = check_schemas()
    if errors:
        return errors
    for path in sorted((ROOT / "examples").glob("*.json")):
        errors += check_bundle(json.loads(path.read_text(encoding="utf-8")), f"examples/{path.name}")
    errors += check_agents_and_skills()
    errors += check_packages()
    return errors


def main() -> int:
    errors = run_all()
    counts = {
        "schemas": len(SCHEMAS),
        "example bundles": len(list((ROOT / "examples").glob("*.json"))),
        "agents": len(list((ROOT / "agents").glob("*.yaml"))),
        "skills": len(list((ROOT / "skills").glob("*.yaml"))),
        "evidence packages": len(list((ROOT / "examples" / "packages").glob("*.json"))),
    }
    print("Checked: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if errors:
        print(f"\n{len(errors)} errors:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("All valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
