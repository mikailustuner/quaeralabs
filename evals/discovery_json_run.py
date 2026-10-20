"""Discover JSON reliability (improvement plan D3): ideation and cross-review answers on the discovery targets.

Usage: uv run python -u evals/discovery_json_run.py --budget 5 [--lanes 2] [--n 5]

Only the model calls are run (no Lean): the landscape of each target, then IDEATE in `--lanes` lanes with the discovery
lenses, then one CROSS_REVIEW per strategy, with the same roles, profiles and output limits as discovery.py. Every answer
is parsed with the same structured-output layer. Recorded per call: valid JSON, the number of text blocks the answer came
in (thinking can split an answer; the Claude CLI's `result` field holds only the last block), and whether an invalid answer
looks cut at the end. `validIfLastBlockOnly` is what the old provider (reading `result`) would have produced: an answer in
more than one block counts as invalid there, because its last block alone is a fragment.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from quaera import prompts
from quaera.cli import make_providers
from quaera.gateway import BudgetExceeded, Gateway, ModelError
from quaera.permissions import Permissions
from quaera.structured import looks_cut, try_parse

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--lanes", type=int, default=2)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--family", default="anthropic")
    a = ap.parse_args()
    targets = yaml.safe_load((ROOT / "evals/sets/capacity-discovery.yaml").read_text(encoding="utf-8"))["targets"][: a.n]
    gw = Gateway({a.family: make_providers()[a.family]}, a.budget, Permissions.load().agents)
    rows: list[dict] = []

    def ask(role, system, prompt, n, purpose, target):
        c, _ = gw.call(role, system, prompt, n, json_mode=True)
        out, _err = try_parse(c.text, purpose)
        row = {"target": target, "purpose": purpose, "model": c.model, "ok": out is not None, "blocks": c.blocks,
               "validIfLastBlockOnly": out is not None and c.blocks == 1, "cut": out is None and looks_cut(c.text),
               "outputTokens": c.output_tokens, "chars": len(c.text), "costUsd": round(c.cost_usd, 4)}
        rows.append(row)
        print(json.dumps(row), flush=True)
        return out or {}

    try:
        for t in targets:
            q = t["question"]
            land = ask("literature", prompts.LANDSCAPE, f"Question: {q}\nScope: Formal proof in Lean 4 + Mathlib\n"
                       "Literature summary: —\nMathlib declarations found: —", 6000, "LANDSCAPE", t["id"])
            landscape = "\n".join(f"- {x.get('name')}: {x.get('idea') or x.get('body')}" for x in
                                  (land.get("approaches") or [])[:8] + (land.get("barriers") or [])[:6] if isinstance(x, dict))
            base = f"Question: {q}\nTarget claim: {q}\n\nKnown approaches and barriers:\n{landscape}"
            strategies = []
            for i in range(a.lanes):
                lens = prompts.DISCOVERY_LENSES[i % len(prompts.DISCOVERY_LENSES)]
                out = ask("hypothesis", prompts.IDEATE, base + f"\n\nYour lens ({lens['name']}): {lens['text']}", 5000, "IDEATE", t["id"])
                strategies += [s for s in (out.get("strategies") or [])[:2] if isinstance(s, dict) and s.get("title")]
            for s in strategies:
                ask("critic", prompts.CROSS_REVIEW, f"Target claim: {q}\n\n{landscape}\n\nStrategy:\n"
                    + json.dumps({k: s.get(k) for k in ("title", "idea", "keySteps", "barrierCheck", "killTest", "novelty", "direction")},
                                 ensure_ascii=False), 2500, "CROSS_REVIEW", t["id"])
    except (BudgetExceeded, ModelError) as exc:
        print(f"stopped: {exc}", flush=True)
    core = [r for r in rows if r["purpose"] in ("IDEATE", "CROSS_REVIEW")]
    summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "family": a.family, "lanes": a.lanes,
               "targets": len({r["target"] for r in rows}), "calls": len(core),
               "invalidJson": sum(not r["ok"] for r in core), "multiBlock": sum(r["blocks"] > 1 for r in core),
               "invalidIfLastBlockOnly": sum(not r["validIfLastBlockOnly"] for r in core),
               "cut": sum(r["cut"] for r in core), "spentUsd": round(gw.spent_usd, 4), "rows": rows}
    summary["invalidRate"] = round(summary["invalidJson"] / max(1, len(core)), 3)
    summary["passed"] = summary["invalidRate"] < 0.05
    out = ROOT / "evals/results" / f"discovery-json-{summary['createdAt'][:10]}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
