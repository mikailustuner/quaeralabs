"""Kanıt paketi (ADR 0007): RO-Crate 1.1 + imzalı quaera-manifest.json.

Paket içeriği:
  ro-crate-metadata.json   RO-Crate standardı (dosyalar, ajanlar, onaylayan insan, AI etiketi)
  quaera-manifest.json     schemas/v1/evidence-package.schema.json; ed25519 ile imzalı
  bundle.json              tüm araştırma nesneleri
  rapor.md                 rapor
  events.json              olay kaydı (tekrar oynatma için)
  artifacts/<sha256>       loglar, Lean çıktıları, kod

İmza anahtarı kullanıcının cihazında üretilir (~/.quaera/signing-key) ve paketten ayrı saklanır.
`verify_package` etiketin kaldırıldığı ya da bir dosyanın değiştirildiği paketi reddeder.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import __version__, contracts
from .store import Store, now

KEY_PATH = Path(os.environ.get("QUAERA_HOME", Path.home() / ".quaera")) / "signing-key"
LABEL = "This research was produced by the QuaeraLabs AI agent team and exported with approval by {who}."


def signing_key() -> Ed25519PrivateKey:
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    KEY_PATH.chmod(0o600)
    return key


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_package(project: Path, approver: str | None = None) -> bytes:
    store = Store(project / "quaera.db")
    objs = store.latest()
    calls = [e["payload"] for e in store.events("model.call")]
    who = approver or os.environ.get("USER", "user")
    files: dict[str, bytes] = {
        "bundle.json": json.dumps(store.bundle(), ensure_ascii=False, indent=2).encode(),
        "events.json": json.dumps(store.events(), ensure_ascii=False, indent=2).encode(),
    }
    if (project / "rapor.md").exists():
        files["rapor.md"] = (project / "rapor.md").read_bytes()
    for o in objs:
        if o["type"] == "artifact" and o.get("sha256"):
            try:
                files[f"artifacts/{o['sha256']}"] = store.blob(o["sha256"])
            except FileNotFoundError:
                pass
    roles = sorted({(c["role"], c["model"]) for c in calls})
    fam = lambda r: {c["family"] for c in calls if c["role"] == r}  # noqa: E731
    cross = bool(fam("critic")) and not (fam("critic") & (fam("engineer") | fam("analyst") | fam("hypothesis")))
    manifest = {
        "manifestVersion": 1, "quaeraVersion": __version__, "createdAt": now(), "roCrateVersion": "1.1",
        "aiLabel": {"text": LABEL.format(who=who), "models": [{"role": r, "model": m} for r, m in roles] or
                    [{"role": "director", "model": "quaera/deterministic"}],
                    "approvedBy": {"kind": "human", "userId": who}, "publishedAt": now(), "crossModelReview": cross},
        "objects": [o["id"] for o in objs],
        "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in sorted(files.items())],
    }
    key = signing_key()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    manifest["signature"] = {"alg": "ed25519", "publicKey": base64.b64encode(pub).decode(),
                             "value": base64.b64encode(key.sign(canonical(manifest))).decode(),
                             "covers": "sha256 of canonical JSON of this manifest without the signature field"}
    errors = contracts.schema_errors("evidence-package", manifest, "manifest")
    if errors:
        raise ValueError("; ".join(errors))
    crate = {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "./"},
             "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}},
            {"@id": "./", "@type": "Dataset", "name": store.meta("title"), "datePublished": now()[:10],
             "description": manifest["aiLabel"]["text"], "license": "https://creativecommons.org/licenses/by/4.0/",
             "hasPart": [{"@id": p} for p in sorted(files) + ["quaera-manifest.json"]],
             "author": [{"@id": f"#agent-{r}"} for r, _ in roles], "creditText": "QuaeraLabs AI ajan ekibi"},
            *[{"@id": f"#agent-{r}", "@type": "SoftwareApplication", "name": f"QuaeraLabs {r}", "softwareVersion": m}
              for r, m in roles],
            {"@id": f"#human-{who}", "@type": "Person", "name": who, "description": "onaylayan insan"},
            *[{"@id": p, "@type": "File", "sha256": hashlib.sha256(b).hexdigest()} for p, b in sorted(files.items())],
            *[{"@id": f"#{o['id']}", "@type": "CreativeWork", "identifier": o["id"], "additionalType": o["type"]} for o in objs],
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ro-crate-metadata.json", json.dumps(crate, ensure_ascii=False, indent=2))
        z.writestr("quaera-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for p, b in files.items():
            z.writestr(p, b)
    store.append("package.exported", {"kind": "human", "userId": who},
                 {"files": len(files), "manifestSha256": hashlib.sha256(canonical(manifest)).hexdigest()})
    return buf.getvalue()


def verify_package(data: bytes) -> list[str]:
    """Boş liste = paket geçerli. İmza, AI etiketi ve dosya özetleri kontrol edilir."""
    problems = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest = json.loads(z.read("quaera-manifest.json"))
        sig = manifest.pop("signature", None)
        if not sig:
            return ["no signature"]
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(sig["publicKey"])).verify(
                base64.b64decode(sig["value"]), canonical(manifest))
        except Exception:
            problems.append("invalid signature (manifest or AI label was modified)")
        if "QuaeraLabs" not in manifest.get("aiLabel", {}).get("text", ""):
            problems.append("QuaeraLabs AI label is missing")
        names = set(z.namelist())
        for f in manifest.get("files", []):
            if f["path"] not in names:
                problems.append(f"missing file: {f['path']}")
            elif hashlib.sha256(z.read(f["path"])).hexdigest() != f["sha256"]:
                problems.append(f"modified file: {f['path']}")
    return problems
