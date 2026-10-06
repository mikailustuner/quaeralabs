"""Literature / novelty evaluation.

Usage: uv run python evals/literature_run.py --budget 1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from quaera.gateway import ClaudeCLIProvider, Gateway
from quaera.literature import run_literature
from quaera.permissions import Permissions
from quaera.tools import ToolRegistry

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True)
    a = ap.parse_args()
    spec = yaml.safe_load((ROOT / "evals/sets/literature.yaml").read_text(encoding="utf-8"))
    perms = Permissions.load()
    gw = Gateway({"anthropic": ClaudeCLIProvider()}, a.budget, perms.agents)
    tools = ToolRegistry(perms)

    def llm(system, prompt, n):
        c, _ = gw.call("literature", system, prompt, n)
        return c.text, {"kind": "agent", "role": "literature", "model": c.model, "modelFamily": c.family}

    rows = []
    try:
        for case in spec["cases"]:
            lit = run_literature(case["question"], "", case["domain"], llm, tools)
            ok = lit.verdict in case["accept"]
            rows.append({"id": case["id"], "kind": case["kind"], "verdict": lit.verdict, "basis": lit.basis, "ok": ok,
                         "searched": lit.searched, "verified": [v["ref"] for v in lit.verified],
                         "rejected": lit.rejected, "summary": lit.summary})
            print(f"{case['id']} {case['kind']:9} → {lit.verdict:14} ({lit.basis}) {'✓' if ok else '✗'} · verified sources {len(lit.verified)}", flush=True)
    finally:
        tools.close()
    acc = sum(r["ok"] for r in rows) / len(rows)
    open_done = sum(1 for r in rows if r["kind"] == "open" and r["verdict"] == "already_done"
                    and r["id"] != "lit-12")
    th = spec["thresholds"]
    summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cases": len(rows),
               "accuracy": round(acc, 3), "open_marked_done": open_done,
               "with_verified_citation": sum(1 for r in rows if r["verified"]),
               "passed": acc >= th["accuracy_min"] and open_done <= th["open_marked_done_max"],
               "thresholds": th, "spent_usd": round(gw.spent_usd, 4), "rows": rows}
    out = ROOT / "evals/results" / f"literature-{summary['createdAt'][:10]}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
