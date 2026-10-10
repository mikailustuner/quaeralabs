"""Lemma bank (capacity plan K1): every Lean-verified statement becomes reusable across branches and projects.

A row is written only after the Verifier's clean recompile (`lemma.reverified`, or the main theorem's verification),
so the bank holds nothing Lean did not accept twice. Rows: the normalized Lean statement (theorem name removed), the
natural-language statement, the complete verified file, the project and the Mathlib revision.

Use:
- `exact(lean)` finds a lemma with the same statement: the discovery attack recompiles its proof under the new name
  and records the lemma verified **without a model call**;
- `search(text)` (bm25 over statements) gives the prover and the lemma program "already verified in this lab" hints.

Honesty: a bank proof counts only after it compiles in the current project (the same Lean gate as any proof); the
report lists reused lemmas with their origin. Evals use no bank (or an isolated one), like the lab memory.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .lean import normalize

WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)


def statement_key(lean_statement: str) -> str:
    """The statement without its theorem name: `theorem quaera_L1 (n : ℕ) : P n` and `lemma foo (n : ℕ) : P n` match."""
    body = re.sub(r"^\s*(?:theorem|lemma)\s+\S+", "", normalize(lean_statement)).strip()
    return " ".join(body.split())


def rename(source: str, old: str, new: str) -> str:
    """Renames a theorem in a verified file (declaration and uses), leaving other identifiers alone."""
    return re.sub(rf"(?<![\w.']){re.escape(old)}(?![\w'])", new, source)


class LemmaBank:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS lemmas (id INTEGER PRIMARY KEY, key TEXT NOT NULL, rev TEXT NOT NULL,
                    name TEXT NOT NULL, lean TEXT NOT NULL, statement TEXT NOT NULL, source TEXT NOT NULL,
                    project TEXT NOT NULL, created TEXT NOT NULL, reused INTEGER NOT NULL DEFAULT 0, UNIQUE(key, rev));
                CREATE VIRTUAL TABLE IF NOT EXISTS lemma_fts USING fts5(statement, lean, content='lemmas', content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS lemmas_ai AFTER INSERT ON lemmas BEGIN
                    INSERT INTO lemma_fts(rowid, statement, lean) VALUES (new.id, new.statement, new.lean); END;
            """)
            self.db.commit()

    def add(self, *, name: str, lean: str, statement: str, source: str, project: str, rev: str) -> bool:
        """Adds a verified lemma; False if the same statement (for this Mathlib revision) is already in the bank."""
        key = statement_key(lean)
        if not key:
            return False
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO lemmas (key, rev, name, lean, statement, source, project, created) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (key, rev, name, normalize(lean), statement or "", source, project,
                                   datetime.now(timezone.utc).isoformat(timespec="seconds")))
            self.db.commit()
        return cur.rowcount > 0

    def exact(self, lean: str, rev: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT id, name, lean, statement, source, project FROM lemmas WHERE key = ? AND rev = ?",
                                  (statement_key(lean), rev)).fetchone()
        return self._row(row) if row else None

    def mark_reused(self, lemma_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE lemmas SET reused = reused + 1 WHERE id = ?", (lemma_id,))
            self.db.commit()

    def search(self, text: str, k: int = 5, rev: str | None = None, exclude_project: str | None = None) -> list[dict]:
        words = list(dict.fromkeys(w.lower() for w in WORD.findall(text or "")))[:20]
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        sql = ("SELECT l.id, l.name, l.lean, l.statement, l.source, l.project FROM lemma_fts f JOIN lemmas l ON l.id = f.rowid "
               "WHERE lemma_fts MATCH ?" + (" AND l.rev = ?" if rev else "") + (" AND l.project != ?" if exclude_project else "")
               + " ORDER BY bm25(lemma_fts) LIMIT ?")
        args = [match] + ([rev] if rev else []) + ([exclude_project] if exclude_project else []) + [k]
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def stats(self) -> dict:
        with self.lock:
            n, reused, projects = self.db.execute("SELECT COUNT(*), COALESCE(SUM(reused), 0), COUNT(DISTINCT project) FROM lemmas").fetchone()
        return {"lemmas": n, "reused": reused, "projects": projects}

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "name": r[1], "lean": r[2], "statement": r[3], "source": r[4], "project": r[5],
                "sha": hashlib.sha256(r[4].encode()).hexdigest()[:12]}

    def close(self) -> None:
        with self.lock:
            self.db.close()


def hint_block(items: list[dict], with_proofs: bool = True, limit_chars: int = 5000) -> str:
    """Prompt text: verified lemmas the model may copy (with their proofs) instead of proving again."""
    if not items:
        return ""
    out, used = [], 0
    for it in items:
        # Renamed to a bank-specific name so a copied lemma never clashes with the file's own quaera_L*/quaera_step_* names.
        alias = f"quaera_bank_{it['id']}"
        if with_proofs:
            body = rename(it["source"], it["name"], alias).split("\n", 2)[-1].strip()
            part = f"- {it['statement'] or it['lean']}\n  ```lean\n  {body}\n  ```"
        else:
            part = f"- {rename(it['lean'], it['name'], alias)}  ({it['statement']})"
        if used + len(part) > limit_chars:
            break
        out.append(part)
        used += len(part)
    return ("\n\nAlready verified in this lab (Lean-checked; you may copy these lemmas WITH their proofs above your theorem "
            "instead of proving them again):\n" + "\n".join(out)) if out else ""
