"""Runs the evaluation sets and writes the results under evals/results/.

Phase 0 has two runners:
  - citations: the citation verifier (tools/citations.py), live arXiv and Crossref APIs.
  - critic-majority: the majority-class baseline that objects to every report with
    the most frequent category. Not an LLM; it sets the LOWER BOUND the Critic agent must beat.

Baselines for LLM-based agents need an API key and spending approval;
they are added to this tool in Phase 1 once the model gateway is in place.

Usage: uv run python evals/run_evals.py [--only citations|critic-majority]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
from quaera import citations  # noqa: E402

SETS = ROOT / "evals" / "sets"
RESULTS = ROOT / "evals" / "results"

def run_critic_majority() -> dict:
    """Majority-class baseline: objects to every report with the set's most frequent category.

    A standard, case-independent lower bound. An LLM Critic must beat it
    on both catch rate and false-alarm rate.
    """
    cases = yaml.safe_load((SETS / "critic-test.yaml").read_text(encoding="utf-8"))["cases"]
    flawed = [c for c in cases if c["kind"] == "flawed"]
    controls = [c for c in cases if c["kind"] == "control"]
    counts: dict[str, int] = {}
    for c in flawed:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    majority = max(sorted(counts), key=counts.get)
    caught = counts[majority]
    return {
        "runner": "critic-majority (not an LLM; lower bound)",
        "majority_category": majority,
        "category_counts": counts,
        "flawed_cases": len(flawed),
        "caught": caught,
        "catch_rate": round(caught / len(flawed), 3),
        "control_cases": len(controls),
        "false_alarms": len(controls),
        "false_alarm_rate": 1.0,
    }


def run_citations() -> dict:
    cases = yaml.safe_load((SETS / "citations.yaml").read_text(encoding="utf-8"))["cases"]
    rows = [{"id": c["id"], "expected": c["expected"], "got": citations.check(c["kind"], c["ref"])} for c in cases]
    correct = sum(r["got"] == r["expected"] for r in rows)
    fake_accepted = sum(r["expected"] == "fake" and r["got"] == "real" for r in rows)
    return {
        "runner": "tools/citations.py (live arXiv + Crossref)",
        "cases": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 3),
        "fake_accepted": fake_accepted,
        "unknown": sum(r["got"] == "unknown" for r in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["citations", "critic-majority"])
    args = parser.parse_args()

    runners = {"critic-majority": run_critic_majority, "citations": run_citations}
    if args.only:
        runners = {args.only: runners[args.only]}

    now = datetime.now(timezone.utc)
    out = {"createdAt": now.isoformat(timespec="seconds"), "results": {k: fn() for k, fn in runners.items()}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"baseline-{now:%Y-%m-%d}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for name, res in out["results"].items():
        summary = {k: v for k, v in res.items() if k not in ("rows",)}
        print(f"{name}: {json.dumps(summary, ensure_ascii=False)}")
    print(f"Written: {path.relative_to(ROOT)}")

    # Zero-tolerance gate: fail if a fabricated source is accepted.
    cit = out["results"].get("citations")
    return 1 if cit and cit["fake_accepted"] else 0


if __name__ == "__main__":
    sys.exit(main())
