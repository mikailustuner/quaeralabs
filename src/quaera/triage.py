"""Başarısız araştırmaların incelenmesi ve eval setine aktarılması (Faz 4).

  quaera triage scan                      tüm projelerdeki hata işaretleri (haftalık "ajan hata incelemesi" girdisi)
  quaera triage add <proje> --expect …    bir vakayı beklenen davranışla yerel olarak kaydeder (~/.quaera/triage/)
  quaera triage export <dosya.zip>        vakaları kişisel bilgilerden arındırıp paketler (katılımcı rızasıyla gönderilir)

Ekip, gelen vakaları inceleyip `evals/sets/failures.yaml` dosyasına ekler; `evals/failures_run.py` bu vakaları
güncel kodla yeniden koşar ve beklentinin tuttuğunu kontrol eder.
"""

from __future__ import annotations

import getpass
import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .store import Store

EXPECT_KEYS = {
    "answer": {"yes", "no", "unclear"},                  # Yazar'ın son cevabı bu olmalı
    "notStatus": {"supported", "refuted", "inconclusive", "under_critique"},   # seçilen hipotez bu durumda OLMAMALI
    "mustObject": {True, False},                         # Eleştirmen sonuca itiraz açmalı mı
    "mustStop": {True, False},                           # araştırma kontrollü durmalı mı
    "notAnswered": {True},                               # açık bir soru tam cevaplanmış gibi raporlanmamalı
}


def signals(store: Store) -> list[dict]:
    """Bir projedeki hata işaretleri. Boş liste = belirgin bir sorun yok (yine de yanlış olabilir)."""
    out = []
    st = {e["payload"]["key"]: e["payload"]["value"] for e in store.events("state")}
    if st.get("stopped"):
        out.append({"kind": "stopped", "detail": str(st["stopped"])[:300]})
    for e in store.events("run.crashed"):
        out.append({"kind": "crashed", "detail": e["payload"].get("error", "")[:300]})
    objs = store.latest()
    for v in (o for o in objs if o["type"] == "verification" and o.get("reproduced") != "yes"):
        out.append({"kind": "not_reproduced", "detail": f"{v['id']}: {v.get('reproduced')}"})
    for c in (o for o in objs if o["type"] == "critique" and o["status"] == "open"):
        out.append({"kind": "open_critique", "detail": f"{c['id']} [{c['severity']}] {c['body'][:200]}"})
    rejected = store.events("citation.rejected")
    if rejected:
        out.append({"kind": "citation_rejected", "detail": f"{len(rejected)} sources could not be verified and were dropped"})
    dropped = sum(len(e["payload"].get("dropped", [])) for e in store.events("writer.output"))
    if dropped:
        out.append({"kind": "uncited_sentences", "detail": f"{dropped} uncited sentences from the Writer were dropped"})
    for kind in ("budget.blocked", "model.invalid_json", "tool.error"):
        n = len(store.events(kind))
        if n:
            out.append({"kind": kind.replace(".", "_"), "detail": f"{n} times"})
    if not store.events("report.written"):
        out.append({"kind": "incomplete", "detail": "no report written (interrupted or still running)"})
    answer = next((e["payload"].get("answer") for e in reversed(store.events("writer.output"))), None)
    if answer == "unclear":
        out.append({"kind": "unclear_answer", "detail": "the Writer could not give a clear answer to the question"})
    return out


def scan(home: Path) -> list[dict]:
    rows = []
    for p in sorted(home.iterdir()):
        if not (p / "quaera.db").exists():
            continue
        s = Store(p / "quaera.db")
        try:
            if s.events("fault.injected"):
                continue                                     # bilinçli hata enjeksiyonu: değerlendirme, gerçek vaka değil
            sig = signals(s)
            q = next((o for o in s.latest("question")), {})
            rows.append({"project": p.name, "title": q.get("title", ""), "domain": q.get("domain", ""), "signals": sig})
        finally:
            s.close()
    return rows


def triage_dir(home: Path) -> Path:
    d = home.parent / "triage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_case(home: Path, project: str, note: str, expect: dict) -> Path:
    for k, v in expect.items():
        if k not in EXPECT_KEYS or v not in EXPECT_KEYS[k]:
            raise ValueError(f"invalid expectation: {k}={v!r}; allowed: {EXPECT_KEYS}")
    if not expect:
        raise ValueError("at least one expectation is required (e.g. --expect-answer no)")
    if len(note.strip()) < 10:
        raise ValueError("what went wrong? a note of at least one sentence is required")
    s = Store(home / project / "quaera.db")
    try:
        q = s.latest("question")[0]
        case = {"id": f"fail-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}", "sourceProject": project,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "domain": q.get("domain", "math"),
                "question": q["title"], "scope": q.get("scope", ""), "note": note.strip(), "expect": expect,
                "signals": signals(s), "status": "needs_review",
                "dataRequired": q.get("domain") == "ml"}
    finally:
        s.close()
    path = triage_dir(home) / f"{case['id']}.yaml"
    path.write_text(yaml.safe_dump(case, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def redact(text: str) -> str:
    """Kullanıcı adını ve ev dizini yollarını gizler (soru metni katılımcının rızasıyla paylaşılır)."""
    user = getpass.getuser()
    text = re.sub(r"/(home|Users)/[^/\s\"']+", "/~", text)
    return re.sub(rf"\b{re.escape(user)}\b", "<user>", text) if len(user) > 2 else text


def export(home: Path) -> bytes:
    buf = io.BytesIO()
    cases = sorted(triage_dir(home).glob("*.yaml"))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for c in cases:
            z.writestr(c.name, redact(c.read_text(encoding="utf-8")))
        z.writestr("README.txt", "QuaeraLabs failure cases. User name and file paths are redacted. Data files are not included in the package.\n")
    return buf.getvalue()


def check(case: dict, store: Store) -> list[str]:
    """Bir vakanın beklentisini yeniden koşulmuş projeye karşı denetler. Boş liste = beklenti tuttu."""
    problems = []
    exp = case["expect"]
    answer = next((e["payload"].get("answer") for e in reversed(store.events("writer.output"))), None)
    hid = next((e["payload"]["value"] for e in reversed(store.events("state")) if e["payload"]["key"] == "hypothesis_id"), None)
    status = store.get(hid)["status"] if hid else None
    stopped = any(e["payload"]["key"] == "stopped" and e["payload"]["value"] for e in store.events("state"))
    if "answer" in exp and answer != exp["answer"]:
        problems.append(f"answer {answer!r}, expected {exp['answer']!r}")
    if "notStatus" in exp and status == exp["notStatus"]:
        problems.append(f"hypothesis status should not have been {status!r}")
    if "mustObject" in exp:
        objected = any(c["targetId"].startswith("RES") for c in store.latest("critique"))
        if objected != exp["mustObject"]:
            problems.append(f"Critic objection: {objected}, expected {exp['mustObject']}")
    if "mustStop" in exp and stopped != exp["mustStop"]:
        problems.append(f"stopped: {stopped}, expected {exp['mustStop']}")
    if exp.get("notAnswered"):
        h = store.get(hid) if hid else {}
        if status == "supported" and (h.get("scopeRelation") or {}).get("relation", "full") == "full":
            problems.append("open question was reported as fully 'supported'")
        if answer in ("yes", "no"):
            problems.append(f"a definite answer was given to an open question: {answer!r}")
    if not store.events("report.written"):
        problems.append("no report was written")
    return problems


def format_scan(rows: list[dict]) -> str:
    lines = []
    flagged = [r for r in rows if r["signals"]]
    lines.append(f"{len(rows)} projects scanned, {len(flagged)} with failure signals.\n")
    for r in flagged:
        lines.append(f"■ {r['project']} ({r['domain']})\n  {r['title'][:120]}")
        for s in r["signals"]:
            lines.append(f"  - {s['kind']}: {s['detail']}")
    return "\n".join(lines)


