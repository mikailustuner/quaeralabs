"""Dürüstlük denetimini tüm projelerde koşar ve sonucu evals/results/audit-<tarih>.json dosyasına yazar.

Kullanım: tools/limited.sh 10G 300% uv run python -u evals/audit_run.py [--lean] [--lean-timeout 1200] [--offline]
Lean yeniden derlemesi her ispat için ayrı ve temiz bir Lean sürecidir; zaman aşımı "sonuçsuz" sayılır, sahte değil.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from quaera import audit
from quaera.cli import HOME

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lean", action="store_true")
    ap.add_argument("--lean-timeout", type=int, default=1200)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    audits = []
    for p in sorted(HOME.iterdir()):
        if not (p / "quaera.db").exists():
            continue
        x = audit.audit_project(p, online=not a.offline, lean=a.lean, lean_timeout_s=a.lean_timeout)
        audits.append(x)
        if not x.skipped:
            print(f"[{'✓' if not x.findings else '✗'}] {p.name} {x.checked}", flush=True)
            for f in x.findings:
                print(f"     {f.kind}: {f.detail}", flush=True)
    summary = audit.summary(audits)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = ROOT / "evals/results" / f"audit-{stamp[:10]}.json"
    out.write_text(json.dumps({"createdAt": stamp, "lean": a.lean, "online": not a.offline, "summary": summary,
                               "projects": [asdict(x) for x in audits]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
