"""Mathlib declaration index (capacity plan K4): ranked search by words, name parts and notation.

The old `mathlib.search` was a grep that needed every query word on one source line. This index is built once per
Mathlib revision by scanning the sources (no Lean process, a few minutes, ~100 MB) into SQLite FTS5:
- the declaration name split into parts (`Finset.sum_range_succ` → finset sum range succ),
- the signature up to `:=` with notation translated to words (∑ → sum, ≤ → le, ∣ → dvd, …), so a goal type such as
  `∑ i ∈ range n, f i ≤ …` finds lemmas about sums and inequalities,
- the docstring above it.
Ranking is bm25. The index lives in ~/.quaera/mathlib-<rev>.db and is rebuilt only when the revision changes.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

from .lean import DEFAULT_WORKSPACE, mathlib_rev

SYMBOLS = {
    "∑": "sum", "∏": "prod", "≤": "le", "≥": "ge", "<": "lt", ">": "gt", "≠": "ne", "∣": "dvd", "∈": "mem", "∉": "notmem",
    "⊆": "subset", "∪": "union", "∩": "inter", "∀": "forall", "∃": "exists", "¬": "not", "∧": "and", "∨": "or",
    "→": "imp", "↔": "iff", "√": "sqrt", "⁻¹": "inv", "∫": "integral", "‖": "norm", "|": "abs", "⌊": "floor", "⌈": "ceil",
    "ℕ": "nat", "ℤ": "int", "ℚ": "rat", "ℝ": "real", "ℂ": "complex", "^": "pow", "*": "mul", "+": "add", "-": "sub",
    "/": "div", "%": "mod", "•": "smul", "∘": "comp", "π": "pi", "∞": "top", "⊤": "top", "⊥": "bot", "!": "factorial",
}
DECL_RE = re.compile(r"(?:/--(?P<doc>(?:(?!-/).)*?)-/\s*)?(?:@\[[^\]]*\]\s*)?(?:(?:private|protected|noncomputable|nonrec)\s+)*"
                     r"(?P<kind>theorem|lemma|def|abbrev|instance)\s+(?P<name>[^\s:({\[]+)(?P<sig>[^:=]*(?::(?!=)[^:=]*)*)", re.S)
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def name_words(name: str) -> str:
    parts = re.split(r"[._']", name)
    out = []
    for p in parts:
        out += re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", p) or [p]
    return " ".join(x.lower() for x in out if x)


def notation_words(text: str) -> str:
    words = [SYMBOLS[c] for c in SYMBOLS if c in text]
    return " ".join(words)


def query_terms(query: str) -> list[str]:
    terms = [w.lower() for w in WORD.findall(query)]
    terms += name_words(" ".join(t for t in re.findall(r"[\w.']+", query) if "." in t or "_" in t)).split()
    terms += notation_words(query).split()
    stop = {"the", "and", "for", "of", "a", "an", "is", "to", "in", "by", "with", "that", "all", "every", "fun", "theorem", "lemma"}
    return list(dict.fromkeys(t for t in terms if len(t) > 1 and t not in stop))[:16]


class MathlibIndex:
    _default: "MathlibIndex | None" = None
    _lock = threading.Lock()

    def __init__(self, db_path: Path, source_root: Path):
        self.path, self.root = db_path, source_root
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS decls USING fts5(name, words, sig, doc, file UNINDEXED, "
                            "line UNINDEXED, kind UNINDEXED, tokenize='unicode61')")
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            self.db.commit()

    @classmethod
    def default(cls) -> "MathlibIndex":
        with cls._lock:
            if cls._default is None:
                home = Path(os.environ.get("QUAERA_HOME", Path.home() / ".quaera"))
                rev = mathlib_rev()
                cls._default = cls(home / f"mathlib-{rev[:12]}.db", DEFAULT_WORKSPACE / ".lake" / "packages" / "mathlib" / "Mathlib")
            return cls._default

    def built(self) -> bool:
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k = 'built'").fetchone()
        return bool(row)

    def build(self) -> int:
        """Scans the sources once; returns the number of declarations indexed."""
        if not self.root.exists():
            raise FileNotFoundError(f"Mathlib sources not found under {self.root}")
        n, rows = 0, []
        for path in sorted(self.root.rglob("*.lean")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(path.relative_to(self.root.parent))
            for m in DECL_RE.finditer(text):
                name, sig = m.group("name"), " ".join((m.group("sig") or "").split())[:600]
                doc = " ".join((m.group("doc") or "").split())[:400]
                line = text.count("\n", 0, m.start("kind")) + 1
                rows.append((name, name_words(name) + " " + notation_words(sig), sig, doc, rel, line, m.group("kind")))
                n += 1
            if len(rows) > 5000:
                self._insert(rows)
                rows = []
        self._insert(rows)
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (str(n),))
            self.db.commit()
        return n

    def _insert(self, rows: list[tuple]) -> None:
        if rows:
            with self.lock:
                self.db.executemany("INSERT INTO decls VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                self.db.commit()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        if not self.built():
            self.build()
        terms = query_terms(query)
        if not terms:
            return []
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        with self.lock:
            rows = self.db.execute(
                "SELECT name, sig, doc, file, line, kind FROM decls WHERE decls MATCH ? "
                "ORDER BY bm25(decls, 4.0, 3.0, 1.0, 0.5) LIMIT ?", (match, int(limit))).fetchall()
        return [{"name": r[0], "file": r[3], "line": r[4], "kind": r[5],
                 "text": f"{r[5]} {r[0]}{(' ' + r[1]) if r[1] else ''}"[:240], **({"doc": r[2][:200]} if r[2] else {})} for r in rows]
