"""Araştırma hafızası (Faz 4): bu makinedeki tamamlanmış projelerden yerel bir bilgi tabanı.

Her proje rapor aşamasından sonra `~/.quaera/memory.db` içindeki bir SQLite FTS5 dizinine tek satır olarak yazılır:
soru, hipotezler ve durumları, sonuçlar, doğrulama, itirazlar. Yeni bir araştırmada Hipotez ajanı benzer
geçmiş projeleri **yönlendirme** için görür (ör. daha önce çürütülmüş bir hipotezi tekrar önermemek için).

Hafıza kanıt değildir: Yazar yalnızca projenin kendi nesnelerine atıf yapabilir (ml_loop/report), bu yüzden
hafızadan gelen bir bilgi rapora kaynak olarak giremez. Bilinçli hata enjekte edilmiş değerlendirme projeleri
(`fault.injected` olayı) dizine alınmaz. Veri makineden çıkmaz.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

from .store import Store

WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
# Değerlendirme projeleri (sentetik görevler, itiraz testleri, vaka tekrarları, Putnam ölçümü) laboratuvar bilgisi değildir.
EVAL_PREFIXES = ("sentetik-", "itiraz-", "vaka-", "putnam-")


class LabMemory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5("
                        "project UNINDEXED, domain UNINDEXED, title, body, summary UNINDEXED, "
                        "tokenize='unicode61 remove_diacritics 2')")
        # Öğrenilenler: her deney sonucundan (proje bitmeden de) çıkarılan kısa, deterministik dersler.
        self.db.execute("CREATE TABLE IF NOT EXISTS learnings (project TEXT, domain TEXT, title TEXT, kind TEXT, "
                        "text TEXT, at TEXT)")
        self.db.commit()

    # --- dizinleme -------------------------------------------------------------------------
    def index_project(self, project: Path) -> bool:
        """Projeyi (yeniden) dizine yazar. Dizine alınmadıysa False döner."""
        if not (project / "quaera.db").exists():
            return False
        store = Store(project / "quaera.db")
        try:
            entry = summarize(store, project.name)
        finally:
            store.close()
        with self.lock:
            self.db.execute("DELETE FROM memory WHERE project = ?", (project.name,))
            if entry:
                self.db.execute("INSERT INTO memory (project, domain, title, body, summary) VALUES (?, ?, ?, ?, ?)",
                                (project.name, entry["domain"], entry["title"], entry["text"], json.dumps(entry, ensure_ascii=False)))
            self.db.commit()
        return entry is not None

    def learn(self, project: Path) -> int:
        """Projenin öğrenilenlerini (yeniden) yazar. Rapor beklenmez: her deney sonucundan sonra çağrılır."""
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
            self.db.execute("DELETE FROM learnings")
            self.db.commit()
        projects = [p for p in sorted(home.iterdir()) if p.is_dir()]
        for p in projects:
            self.learn(p)
        return sum(self.index_project(p) for p in projects)

    # --- arama -----------------------------------------------------------------------------
    def recall(self, text: str, k: int = 5, exclude: str | None = None, domain: str | None = None) -> list[dict]:
        words = sorted({w.lower() for w in WORD_RE.findall(text)})
        if not words:
            return []
        # Türkçe eklemeli bir dil: "eşiği"/"eşik", "doğruluk"/"doğrusal" tam kelimeyle eşleşmez. Kaba kök olarak
        # uzun kelimelerin ilk 5 harfini önek sorgusu yapıyoruz (FTS5 `"kök"*`).
        stems = sorted({w[:5] if len(w) > 5 else w for w in words})
        query = " OR ".join(f'"{w}"*' for w in stems[:40])
        sql = "SELECT summary FROM memory WHERE memory MATCH ?"
        args: list = [query]
        if exclude:
            sql += " AND project != ?"
            args.append(exclude)
        if domain:
            sql += " AND domain = ?"
            args.append(domain)
        # Sütun ağırlıkları (project, domain, title, body, summary): soru başlığı gövdeden 4 kat önemli.
        sql += " ORDER BY bm25(memory, 0, 0, 4.0, 1.0, 0) LIMIT ?"
        args.append(k * 4)
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        out, titles = [], set()
        for r in rows:                       # aynı sorunun tekrar koşuları tek kayıt olarak döner
            e = json.loads(r[0])
            if e["title"] not in titles:
                titles.add(e["title"])
                out.append(e)
        return out[:k]

    def close(self) -> None:
        self.db.close()


def summarize(store: Store, project: str) -> dict | None:
    """Bir projeyi hafıza kaydına indirger. Bitmemiş ya da hata enjekte edilmiş projeler için None."""
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
    """Bir projeden öğrenilenler: hipotezlerin akıbeti, deney sonuçları, işe yarayan/yaramayan yöntem, uyarılar.

    Model kullanılmaz; her madde projenin kendi kayıtlarından türetilir. Hata enjekte edilmiş projeler için boş liste.
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
    # Keşif kipi: doğrulanan / çürütülen ara iddialar ve bırakılan stratejiler başka projelere yol gösterir.
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
    """Hafıza kaydının tek satırlık özeti (ajana ve arayüze)."""
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
