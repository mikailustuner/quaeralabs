"""Research memory (Phase 4): a local knowledge base built from the finished projects on this machine.

After the report stage every project is written as one row to a SQLite FTS5 index in `~/.quaera/memory.db`:
question, hypotheses and their status, results, verification, objections. In a new project the Hypothesis agent
sees similar past projects for **orientation** (e.g. so it does not propose an already refuted hypothesis again).

Memory is not evidence: the Writer may only cite the project's own objects (ml_loop/report), so a fact coming
from memory can never enter the report as a source. Eval projects with deliberately injected faults
(`fault.injected` event) are not indexed. The data never leaves the machine.

Hybrid recall: next to the FTS5 index, every project also gets a multilingual sentence embedding (fastembed,
local ONNX model; optional `memory` extra). bm25 and cosine rankings are merged with reciprocal rank fusion, so
a question asked in English also finds a project recorded in Turkish. Without fastembed (or with
QUAERA_MEMORY_EMBED=off) recall is bm25 only, exactly as before.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import sys
import threading
from array import array
from pathlib import Path
from typing import Callable

from .store import Store

WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
# Eval projects (synthetic tasks, critic tests, case replays, Putnam runs) are not lab knowledge.
EVAL_PREFIXES = ("synthetic-", "critic-test-", "case-", "putnam-", "sentetik-", "itiraz-", "vaka-")   # last three: names used by older evaluation runs

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"   # 384-d, ~220 MB, 50+ languages
# Vector-only hits below this cosine similarity are dropped, so an unrelated question recalls nothing. Measured on
# Turkish/English pairs (24 real lab projects): matching questions score 0.62-0.82, unrelated ones up to 0.52.
MIN_SIMILARITY = 0.55
RRF_K = 60

Embedder = Callable[[list[str]], list[list[float]]]   # texts -> unit vectors; carries a `name` attribute


class FastEmbedder:
    """Lazy fastembed wrapper; the model is downloaded once on first use (FASTEMBED_CACHE_PATH or ~/.cache)."""

    name = EMBED_MODEL

    def __init__(self):
        self.model, self.lock = None, threading.Lock()

    def __call__(self, texts: list[str]) -> list[list[float]]:
        import numpy as np
        with self.lock:
            if self.model is None:
                from fastembed import TextEmbedding
                cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(Path.home() / ".cache" / "quaeralabs" / "fastembed")
                self.model = TextEmbedding(EMBED_MODEL, cache_dir=cache)
            m = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return (m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)).tolist()


@functools.cache
def default_embedder() -> Embedder | None:
    """Shared embedder, or None when fastembed is not installed or QUAERA_MEMORY_EMBED=off."""
    if os.environ.get("QUAERA_MEMORY_EMBED", "").strip().lower() in ("0", "off", "false", "no"):
        return None
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return None
    return FastEmbedder()


class LabMemory:
    def __init__(self, path: Path, embedder: Embedder | None | bool = True):
        """`embedder`: True = default_embedder(), False/None = bm25 only, or any Embedder (tests)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.embedder = default_embedder() if embedder is True else (embedder or None)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5("
                        "project UNINDEXED, domain UNINDEXED, title, body, summary UNINDEXED, "
                        "tokenize='unicode61 remove_diacritics 2')")
        # Learnings: short, deterministic lessons drawn from every experiment result (even before the project ends).
        self.db.execute("CREATE TABLE IF NOT EXISTS learnings (project TEXT, domain TEXT, title TEXT, kind TEXT, "
                        "text TEXT, at TEXT)")
        # One embedding of `title + body` per project (float32 blob); recomputed when the model changes.
        self.db.execute("CREATE TABLE IF NOT EXISTS memory_vec (project TEXT PRIMARY KEY, model TEXT, vec BLOB)")
        self.db.commit()

    # --- indexing --------------------------------------------------------------------------
    def index_project(self, project: Path) -> bool:
        """(Re)writes the project to the index. Returns False if it was not indexed."""
        if not (project / "quaera.db").exists():
            return False
        store = Store(project / "quaera.db")
        try:
            entry = summarize(store, project.name)
        finally:
            store.close()
        with self.lock:
            self.db.execute("DELETE FROM memory WHERE project = ?", (project.name,))
            self.db.execute("DELETE FROM memory_vec WHERE project = ?", (project.name,))
            if entry:
                self.db.execute("INSERT INTO memory (project, domain, title, body, summary) VALUES (?, ?, ?, ?, ?)",
                                (project.name, entry["domain"], entry["title"], entry["text"], json.dumps(entry, ensure_ascii=False)))
            self.db.commit()
        if entry:
            self._embed_missing()
        return entry is not None

    def learn(self, project: Path) -> int:
        """(Re)writes the project's learnings. Does not wait for the report: called after every experiment result."""
        if not (project / "quaera.db").exists():
            return 0
        store = Store(project / "quaera.db")
        try:
            items = lessons(store)
            domain, title = store.meta("domain", "math"), store.meta("shortTitle") or store.meta("title") or project.name
        finally:
            store.close()
        with self.lock:
            self.db.execute("DELETE FROM learnings WHERE project = ?", (project.name,))
            self.db.executemany("INSERT INTO learnings (project, domain, title, kind, text, at) VALUES (?, ?, ?, ?, ?, ?)",
                                [(project.name, domain, title, i["kind"], i["text"], i["at"]) for i in items])
            self.db.commit()
        return len(items)

    def learnings(self, limit: int = 50, project: str | None = None, q: str | None = None,
                  include_evals: bool = False) -> list[dict]:
        sql, args = "SELECT project, domain, title, kind, text, at FROM learnings WHERE 1=1", []
        if project:
            sql += " AND project = ?"
            args.append(project)
        if q:
            for w in WORD_RE.findall(q)[:8]:
                sql += " AND (text LIKE ? OR title LIKE ?)"
                args += [f"%{w}%", f"%{w}%"]
        if not include_evals:
            sql += "".join(f" AND project NOT LIKE '{p}%'" for p in EVAL_PREFIXES)
        sql += " ORDER BY at DESC, rowid ASC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [dict(zip(("project", "domain", "title", "kind", "text", "at"), r)) for r in rows]

    def rebuild(self, home: Path) -> int:
        with self.lock:
            self.db.execute("DELETE FROM memory")
            self.db.execute("DELETE FROM memory_vec")
            self.db.execute("DELETE FROM learnings")
            self.db.commit()
        projects = [p for p in sorted(home.iterdir()) if p.is_dir()]
        for p in projects:
            self.learn(p)
        return sum(self.index_project(p) for p in projects)

    # --- search ----------------------------------------------------------------------------
    def recall(self, text: str, k: int = 5, exclude: str | None = None, domain: str | None = None) -> list[dict]:
        """bm25 and (when an embedder is available) cosine rankings merged by reciprocal rank fusion."""
        where, args = "", []
        if exclude:
            where += " AND m.project != ?"
            args.append(exclude)
        if domain:
            where += " AND m.domain = ?"
            args.append(domain)
        rankings = [self._bm25(text, k * 4, where, args), self._nearest(text, k * 4, where, args)]
        score: dict[str, float] = {}
        summaries: dict[str, str] = {}
        for ranking in rankings:
            for rank, (project, summary) in enumerate(ranking):
                score[project] = score.get(project, 0.0) + 1.0 / (RRF_K + rank + 1)
                summaries[project] = summary
        out, titles = [], set()
        for project in sorted(score, key=lambda p: -score[p]):   # repeated runs of the same question come back as one entry
            e = json.loads(summaries[project])
            if e["title"] not in titles:
                titles.add(e["title"])
                out.append(e)
        return out[:k]

    def _bm25(self, text: str, limit: int, where: str, args: list) -> list[tuple[str, str]]:
        words = sorted({w.lower() for w in WORD_RE.findall(text)})
        if not words:
            return []
        # Turkish is agglutinative: "eşiği"/"eşik", "doğruluk"/"doğrusal" do not match as whole words. As a rough stem
        # we prefix-query the first 5 letters of long words (FTS5 `"stem"*`).
        stems = sorted({w[:5] if len(w) > 5 else w for w in words})
        query = " OR ".join(f'"{w}"*' for w in stems[:40])
        # Column weights (project, domain, title, body, summary): the question title weighs 4x the body.
        sql = (f"SELECT m.project, m.summary FROM memory m WHERE memory MATCH ?{where} "
               "ORDER BY bm25(memory, 0, 0, 4.0, 1.0, 0) LIMIT ?")
        with self.lock:
            return self.db.execute(sql, [query, *args, limit]).fetchall()

    def _nearest(self, text: str, limit: int, where: str, args: list) -> list[tuple[str, str]]:
        if not self.embedder or not text.strip() or not self._embed_missing():
            return []
        try:
            q = self.embedder([text])[0]
        except Exception as exc:          # model download/load failed: recall still works with bm25
            return self._disable(exc)
        sql = (f"SELECT m.project, m.summary, v.vec FROM memory m JOIN memory_vec v ON v.project = m.project "
               f"WHERE v.model = ?{where}")
        with self.lock:
            rows = self.db.execute(sql, [self.embedder.name, *args]).fetchall()
        hits = []
        for project, summary, blob in rows:
            sim = sum(a * b for a, b in zip(q, array("f", blob)))
            if sim >= MIN_SIMILARITY:
                hits.append((sim, project, summary))
        hits.sort(reverse=True)
        return [(p, s) for _, p, s in hits[:limit]]

    def _embed_missing(self) -> bool:
        """Embeds projects that have no vector for the current model. False if embeddings are unavailable."""
        if not self.embedder:
            return False
        with self.lock:
            rows = self.db.execute("SELECT m.project, m.title, m.body FROM memory m LEFT JOIN memory_vec v "
                                   "ON v.project = m.project AND v.model = ? WHERE v.project IS NULL",
                                   (self.embedder.name,)).fetchall()
        if not rows:
            return True
        try:
            vecs = self.embedder([f"{title}\n{body}" for _, title, body in rows])
        except Exception as exc:
            return self._disable(exc) or False
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO memory_vec (project, model, vec) VALUES (?, ?, ?)",
                                [(r[0], self.embedder.name, array("f", v).tobytes()) for r, v in zip(rows, vecs)])
            self.db.commit()
        return True

    def _disable(self, exc: Exception) -> list:
        print(f"quaera: memory embeddings disabled, using bm25 only ({type(exc).__name__}: {exc})"[:300], file=sys.stderr)
        self.embedder = None
        return []

    def close(self) -> None:
        self.db.close()


def summarize(store: Store, project: str) -> dict | None:
    """Reduces a project to a memory entry. None for unfinished or fault-injected projects."""
    if store.events("fault.injected") or not store.events("report.written"):
        return None
    objs = store.latest()
    question = next((o for o in objs if o["type"] == "question"), None)
    if not question:
        return None
    hyps = [{"id": h["id"], "statement": h["statement"], "status": h["status"]}
            for h in objs if h["type"] == "hypothesis" and h["status"] not in ("draft", "rejected")]
    results = [{"id": r["id"], "summary": r["summary"][:400]} for r in objs if r["type"] == "result"]
    relation = next((l["relation"] for l in reversed(objs) if l["type"] == "evidence_link"), None)
    reproduced = next((v["reproduced"] for v in reversed(objs) if v["type"] == "verification"), None)
    open_crit = [c["body"][:300] for c in objs if c["type"] == "critique" and c["status"] == "open"]
    states = {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}
    answer = next((e["payload"].get("answer") for e in reversed(store.events("writer.output"))), None)
    entry = {"project": project, "domain": question.get("domain", "math"), "title": question["title"],
             "hypotheses": hyps, "results": results, "relation": relation, "reproduced": reproduced,
             "openCritiques": open_crit, "answer": answer, "stopped": states.get("stopped")}
    entry["text"] = " ".join([question["title"], question.get("scope", "")] + [h["statement"] for h in hyps] +
                             [r["summary"] for r in results] + open_crit)
    return entry


def lessons(store: Store) -> list[dict]:
    """Lessons from a project: fate of the hypotheses, experiment results, methods that worked or failed, caveats.

    No model is used; every item is derived from the project's own records. Empty list for fault-injected projects.
    """
    if store.events("fault.injected"):
        return []
    out: list[dict] = []

    def add(kind: str, text: str, at: str) -> None:
        text = " ".join(str(text).split())
        if text and all(o["text"] != text for o in out):
            out.append({"kind": kind, "text": text[:500], "at": at})

    objs = store.latest()
    verified = {v["resultId"]: v["reproduced"] for v in objs if v["type"] == "verification"}
    for h in objs:
        if h["type"] != "hypothesis" or h["status"] in ("draft", "rejected", "accepted", "testing"):
            continue
        st = h["status"]
        label = {"supported": "Supported", "refuted": "Refuted", "inconclusive": "Inconclusive",
                 "under_critique": "Contested"}.get(st, st.replace("_", " ").capitalize())
        add({"supported": "finding", "refuted": "refuted"}.get(st, "open"), f"{label}: {h['statement']}", h["createdAt"])
    for r in objs:
        if r["type"] != "result":
            continue
        lp = r.get("leanProof") or {}
        if lp.get("verified") and lp.get("theoremName") == "quaera_refute":
            add("refuted", "Lean proved the negation of the formal statement (counterexample exists).", r["createdAt"])
        elif lp.get("verified"):
            rep = verified.get(r["id"])
            add("finding", "Lean 4 proof compiled without sorry or non-standard axioms"
                + (", independently recompiled." if rep == "yes" else "."), r["createdAt"])
        elif r.get("negative") and "leanProof" in r:
            add("open", f"No proof found within budget ({len(r.get('runIds', []))} attempts); this does not refute the hypothesis.",
                r["createdAt"])
        else:
            add("result", r["summary"], r["createdAt"])
    for e in store.events("proof.search"):
        p, steps = e["payload"], e["payload"].get("steps") or []
        win = next((s for s in steps if s.get("verified") and s.get("kind") in ("automation", "whole", "assembled")), None)
        if win:
            how = {"automation": "tactic automation alone (no model call)", "whole": "a whole-proof candidate",
                   "assembled": "a sketch split into lemmas"}[win["kind"]]
            add("method", f"Proof found by {how} after {p.get('modelCalls', 0)} model call(s).", e["at"])
        elif p.get("stopped"):
            add("method", f"Proof search stopped: {p['stopped']}", e["at"])
        elif steps:
            lemmas = [s for s in steps if s.get("kind") == "lemma"]
            ok = sum(1 for s in lemmas if s.get("verified"))
            add("method", f"Proof search exhausted after {p.get('modelCalls', 0)} model call(s)"
                + (f"; {ok}/{len(lemmas)} lemma attempts verified." if lemmas else "."), e["at"])
    for e in store.events("fidelity.check"):
        if e["payload"].get("verified") and e["payload"].get("check") == "vacuity":
            add("caveat", "Formal statement was vacuous: its assumptions are contradictory.", e["at"])
    for e in store.events("exploration.done"):
        p = e["payload"]
        if p.get("counterexample"):
            add("refuted", f"Small-case search found a counterexample candidate: {p['counterexample']}", e["at"])
        for obs in (p.get("observations") or [])[:2]:
            add("observation", obs if isinstance(obs, str) else json.dumps(obs, ensure_ascii=False), e["at"])
    # Discovery mode: verified / refuted intermediate claims and abandoned strategies guide other projects.
    titles = {e["payload"]["id"]: e["payload"]["title"] for e in store.events("strategy.proposed")}
    stmts = {e["payload"]["id"]: e["payload"].get("lean") or e["payload"]["statement"] for e in store.events("program.lemma")}
    for e in store.events("lemma.status"):
        p = e["payload"]
        if p["status"] == "verified":
            add("finding", f"Lean-verified lemma ({titles.get(p.get('strategy'), p.get('strategy'))}): {stmts.get(p['id'], p['id'])}", e["at"])
        elif p["status"] == "refuted":
            add("refuted", f"Refuted intermediate claim ({titles.get(p.get('strategy'), p.get('strategy'))}): {stmts.get(p['id'], p['id'])}", e["at"])
    for e in store.events("strategy.dead"):
        add("method", f"Strategy abandoned — {titles.get(e['payload']['id'], e['payload']['id'])}: {e['payload']['reason']}", e["at"])
    for e in store.events("strategy.reviewed"):
        if e["payload"].get("fatalFlaw"):
            add("caveat", f"Cross-review flaw in “{titles.get(e['payload']['id'], e['payload']['id'])}”: {e['payload']['fatalFlaw']}", e["at"])
    for c in objs:
        if c["type"] == "critique" and c["status"] == "open" and c.get("severity") in ("high", "blocking"):
            add("caveat", f"Open critique: {c['body']}", c["createdAt"])
    for e in store.events("state"):
        if e["payload"]["key"] == "stopped" and e["payload"]["value"]:
            add("stopped", f"Research stopped early: {e['payload']['value']}", e["at"])
    return out


def outcome(e: dict) -> str:
    """One-line summary of a memory entry (for agents and the UI)."""
    parts = []
    for h in e["hypotheses"]:
        parts.append(f"hypothesis “{h['statement'][:160]}” → {h['status']}")
    if e.get("reproduced"):
        parts.append(f"independent re-run reproduced: {e['reproduced']}")
    if e.get("openCritiques"):
        parts.append(f"{len(e['openCritiques'])} open critique(s): {e['openCritiques'][0][:160]}")
    if e.get("stopped"):
        parts.append(f"stopped early: {str(e['stopped'])[:120]}")
    return "; ".join(parts) or "no accepted hypothesis"


def context_block(items: list[dict], learned: dict[str, list[dict]] | None = None) -> str:
    if not items:
        return ""
    lines = []
    for e in items:
        lines.append(f"- [{e['project']}] Q: {e['title'][:200]} — {outcome(e)}")
        lines += [f"    · {x['kind']}: {x['text'][:220]}" for x in (learned or {}).get(e["project"], [])[:4]]
    return ("\n\nLab memory — earlier QuaeraLabs projects on this machine. Use only for orientation (avoid repeating a refuted "
            "hypothesis, build on what was verified). It is NOT evidence for this project and must not be cited:\n" + "\n".join(lines))
