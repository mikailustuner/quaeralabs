"""Yerel veri deposu (ADR 0004).

- Her proje tek bir SQLite dosyasıdır; yanında içerik adresli bir dosya deposu (cas/) durur.
- `events` tablosu yalnızca eklenebilir: UPDATE ve DELETE tetikleyicilerle engellenir.
- Araştırma nesneleri her yazmada şemaya ve proje genelindeki kurallara karşı doğrulanır;
  kuralı bozan yazma reddedilir.
- Yazma yetkisi rol bazında kontrol edilir (izin matrisi, agents/*.yaml).
- Mevcut durum olaylardan yeniden üretilebilir; dallanma bir olay noktasına kadar yeniden oynatmadır.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import contracts
from .permissions import Permissions, PermissionDenied

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS objects (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    seq INTEGER NOT NULL REFERENCES events(seq),
    PRIMARY KEY (id, revision)
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'olay kaydı değiştirilemez'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'olay kaydı silinemez'); END;
CREATE TRIGGER IF NOT EXISTS objects_no_update BEFORE UPDATE ON objects
BEGIN SELECT RAISE(ABORT, 'yayınlanmış revizyon değiştirilemez'); END;
CREATE TRIGGER IF NOT EXISTS objects_no_delete BEFORE DELETE ON objects
BEGIN SELECT RAISE(ABORT, 'revizyon silinemez'); END;
"""

PREFIX = {
    "question": "Q", "hypothesis": "H", "preregistration": "PRE", "experiment": "E", "run": "RUN",
    "result": "RES", "critique": "CR", "evidence_link": "EL", "verification": "VER",
    "artifact": "ART", "message": "MSG",
}


class IntegrityError(Exception):
    """Yazma, şemayı ya da proje kurallarını bozuyor."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path, permissions: Permissions | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cas_dir = self.path.parent / "cas"
        # Sunucuda araştırma ayrı bir iş parçacığında çalışır: bağlantı paylaşılır, erişim bir kilitle sıralanır.
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA_SQL)
        self.permissions = permissions or Permissions.load()

    # --- meta -----------------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        with self.lock:
            return self._set_meta_impl(key, value)

    def _set_meta_impl(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def meta(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self._meta_impl(key, default)

    def _meta_impl(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # --- olaylar ---------------------------------------------------------------
    def append(self, kind: str, actor: dict, payload: dict) -> int:
        with self.lock:
            return self._append_impl(kind, actor, payload)

    def _append_impl(self, kind: str, actor: dict, payload: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO events (at, kind, actor, payload) VALUES (?, ?, ?, ?)",
            (now(), kind, json.dumps(actor, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)),
        )
        return cur.lastrowid

    def events(self, kind: str | None = None, upto: int | None = None) -> list[dict]:
        with self.lock:
            return self._events_impl(kind, upto)

    def _events_impl(self, kind: str | None = None, upto: int | None = None) -> list[dict]:
        sql, args = "SELECT seq, at, kind, actor, payload FROM events WHERE 1=1", []
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        if upto is not None:
            sql, args = sql + " AND seq <= ?", args + [upto]
        rows = self.db.execute(sql + " ORDER BY seq", args).fetchall()
        return [{"seq": s, "at": a, "kind": k, "actor": json.loads(ac), "payload": json.loads(p)} for s, a, k, ac, p in rows]

    # --- nesneler --------------------------------------------------------------
    def next_id(self, type_: str) -> str:
        with self.lock:
            return self._next_id_impl(type_)

    def _next_id_impl(self, type_: str) -> str:
        prefix = PREFIX[type_]
        n = self.db.execute("SELECT COUNT(DISTINCT id) FROM objects WHERE type = ?", (type_,)).fetchone()[0]
        return f"{prefix}-{n + 1:04d}"

    def latest(self, type_: str | None = None) -> list[dict]:
        with self.lock:
            return self._latest_impl(type_)

    def _latest_impl(self, type_: str | None = None) -> list[dict]:
        sql = """SELECT o.data FROM objects o
                 JOIN (SELECT id, MAX(revision) r FROM objects GROUP BY id) m ON o.id = m.id AND o.revision = m.r"""
        rows = self.db.execute(sql + (" WHERE o.type = ?" if type_ else "") + " ORDER BY o.seq", (type_,) if type_ else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, id_: str) -> dict | None:
        with self.lock:
            return self._get_impl(id_)

    def _get_impl(self, id_: str) -> dict | None:
        row = self.db.execute("SELECT data FROM objects WHERE id = ? ORDER BY revision DESC LIMIT 1", (id_,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, obj: dict, by: dict | None = None, final: bool = False) -> dict:
        """Nesnenin yeni bir revizyonunu yazar. id yoksa atanır, revizyon otomatik artar.

        `by`: revizyonu yazan aktör (varsayılan: createdBy). Örneğin bir hipotezin onay
        revizyonunu insan yazar ama hipotezin yazarı hâlâ Hipotez ajanıdır.
        Yazmadan önce: rol yazma izni, nesne şeması ve proje genelindeki kurallar kontrol edilir.
        """
        with self.lock:
            return self._put_impl(obj, by, final)

    def _put_impl(self, obj: dict, by: dict | None = None, final: bool = False) -> dict:
        obj = json.loads(json.dumps(obj))
        actor = by or obj["createdBy"]
        self.permissions.check_graph_write(actor, obj["type"], obj.get("kind") if obj["type"] == "message" else None)
        # Kimlik/revizyon ataması ve yazma tek bir yazma kilidi altında: sunucu isteği (insan mesajı) ile araştırma
        # iş parçacığı aynı veritabanına ayrı bağlantılardan yazar; BEGIN IMMEDIATE olmadan ikisi aynı kimliği alabiliyordu.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if "id" not in obj:
                obj["id"] = self.next_id(obj["type"])
            prev = self.get(obj["id"])
            obj["revision"] = (prev["revision"] + 1) if prev else 1
            obj.setdefault("createdAt", now())

            errors = contracts.schema_errors(contracts.TYPE_TO_SCHEMA[obj["type"]], obj, obj["id"])
            if not errors:
                snapshot = [o for o in self.latest() if o["id"] != obj["id"]] + [obj]
                bundle = {"schemaVersion": "v1", "title": self.meta("title", "proje"), "objects": snapshot}
                errors = contracts.check_bundle(bundle, "depo", final=final)
            if errors:
                raise IntegrityError("; ".join(errors))
            seq = self.append("object.put", actor, {"object": obj})
            self.db.execute(
                "INSERT INTO objects VALUES (?, ?, ?, ?, ?)",
                (obj["id"], obj["revision"], obj["type"], json.dumps(obj, ensure_ascii=False), seq),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return obj

    def bundle(self) -> dict:
        return {"schemaVersion": "v1", "title": self.meta("title", "proje"), "objects": self.latest()}

    def check_final(self) -> list[str]:
        return contracts.check_bundle(self.bundle(), "proje", final=True)

    # --- içerik adresli dosya deposu -------------------------------------------
    def put_blob(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.cas_dir / digest[:2] / digest
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return digest

    def blob(self, digest: str) -> bytes:
        return (self.cas_dir / digest[:2] / digest).read_bytes()

    # --- dallanma ----------------------------------------------------------------
    def branch(self, target: Path, at_seq: int) -> "Store":
        """`at_seq` dahil olmak üzere o noktaya kadarki olayları yeni bir projeye yeniden oynatır."""
        with self.lock:
            return self._branch_impl(target, at_seq)

    def _branch_impl(self, target: Path, at_seq: int) -> "Store":
        new = Store(target, self.permissions)
        if new.events():
            raise IntegrityError(f"{target} boş değil")
        for key, value in self.db.execute("SELECT key, value FROM meta"):
            new.set_meta(key, json.loads(value))
        new.set_meta("branchOf", {"project": str(self.path), "atSeq": at_seq})
        new.db.execute("BEGIN")
        for ev in self.events(upto=at_seq):
            seq = new.append(ev["kind"], ev["actor"], ev["payload"])
            if ev["kind"] == "object.put":
                o = ev["payload"]["object"]
                new.db.execute("INSERT INTO objects VALUES (?, ?, ?, ?, ?)",
                               (o["id"], o["revision"], o["type"], json.dumps(o, ensure_ascii=False), seq))
        new.db.execute("COMMIT")
        if self.cas_dir.exists():
            for f in self.cas_dir.rglob("*"):
                if f.is_file():
                    dst = new.cas_dir / f.relative_to(self.cas_dir)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        dst.write_bytes(f.read_bytes())
        new.append("branch.created", {"kind": "human", "userId": "system"}, {"from": str(self.path), "atSeq": at_seq})
        return new

    def close(self) -> None:
        with self.lock:
            return self._close_impl()

    def _close_impl(self) -> None:
        self.db.close()


def iter_types(objs: Iterable[dict], type_: str) -> list[dict]:
    return [o for o in objs if o["type"] == type_]


__all__ = ["Store", "IntegrityError", "PermissionDenied", "now"]
