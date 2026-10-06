"""Critic test v0.2: measured with the QuaeraLabs Critic agent (CRITIC_EXPERIMENT prompt).

Usage: uv run python evals/critic_v2_run.py --budget 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from quaera import prompts
from quaera.gateway import ClaudeCLIProvider, Gateway, ModelError, parse_json
from quaera.permissions import Permissions

ROOT = Path(__file__).resolve().parent.parent


def rate(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def metrics(rows, cases):
    flawed = [r for r in rows if cases[r["id"]]["kind"] == "flawed"]
    pers = [r for r in rows if cases[r["id"]]["kind"] == "persuasion"]
    ctrl = [r for r in rows if cases[r["id"]]["kind"] == "control"]
    match = lambda r: bool(set(r["categories"]) & set(cases[r["id"]]["accept"]))  # noqa: E731
    return {
        "flawed": len(flawed), "persuasion": len(pers), "control": len(ctrl),
        "detection": rate([r["flawed"] for r in flawed]),
        "category_match": rate([r["flawed"] and match(r) for r in flawed + pers]),
        "false_alarm": rate([r["flawed"] for r in ctrl]),
        "persuasion_resistance": rate([r["flawed"] for r in pers]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True)
    a = ap.parse_args()
    spec = yaml.safe_load((ROOT / "evals/sets/critic-test-v0.2.yaml").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in spec["cases"]}
    order = list(cases)
    random.Random(11).shuffle(order)
    perms = Permissions.load()
    gw = Gateway({"anthropic": ClaudeCLIProvider()}, a.budget, perms.agents)
    rows = []
    for cid in order:
        try:
            c, _ = gw.call("critic", prompts.CRITIC_EXPERIMENT, f"Experiment report:\n{cases[cid]['report']}", 1500)
            out = parse_json(c.text)
        except ModelError as exc:
            out = {"flawed": None, "categories": [], "summary": f"error: {exc}"}
        rows.append({"id": cid, "split": cases[cid]["split"], "kind": cases[cid]["kind"], "flawed": bool(out.get("flawed")),
                     "categories": out.get("categories") or [], "severity": out.get("severity"),
                     "evidence": out.get("evidence", ""), "summary": out.get("summary", "")})
        print(f"{cid} {cases[cid]['kind']:10} → flawed={rows[-1]['flawed']} {rows[-1]['categories']}", flush=True)
    test = [r for r in rows if r["split"] == "test"]
    m_test, m_dev = metrics(test, cases), metrics([r for r in rows if r["split"] == "dev"], cases)
    th = spec["thresholds"]
    passed = (m_test["detection"] >= th["detection_min"] and m_test["category_match"] >= th["category_match_min"]
              and m_test["false_alarm"] <= th["false_alarm_max"] and m_test["persuasion_resistance"] >= th["persuasion_resistance_min"])
    # Majority-class baseline ("flawed" + the most frequently accepted category for every report)
    cnt = Counter(cat for c in cases.values() if c["split"] == "test" and c["kind"] != "control" for cat in c["accept"])
    maj = cnt.most_common(1)[0][0]
    baseline = metrics([{"id": r["id"], "flawed": True, "categories": [maj]} for r in test], cases)
    summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "promptSha256": hashlib.sha256(prompts.CRITIC_EXPERIMENT.encode()).hexdigest(),
               "test": m_test, "dev": m_dev, "thresholds": th,
               "passed": passed, "majority_baseline_test": {"category": maj, **baseline},
               "spent_usd": round(gw.spent_usd, 4), "rows": rows}
    out = ROOT / "evals/results" / f"critic-v0.2-{summary['createdAt'][:10]}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
