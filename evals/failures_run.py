"""Failure case set: re-runs past failures with the current code and checks that the expectation holds.

Usage: tools/limited.sh 8G 300% uv run python -u evals/failures_run.py [--only fail-goldbach-scope]
Math cases need Lean. ML cases run either on a `syntheticTask` (data regenerated with a hidden seed) or on data
shared with consent under `evals/data/failures/<id>/`; without data the case is skipped.
Memory is off (memory=False): a case must not pass by remembering an earlier run of the same question.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals" / "synthetic"))

from tasks import TASKS, materialize  # noqa: E402

from quaera.cli import HOME, build, finish  # noqa: E402
from quaera.triage import check  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    a = ap.parse_args()
    cases = yaml.safe_load((ROOT / "evals/sets/failures.yaml").read_text(encoding="utf-8"))["cases"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = ROOT / "evals/results" / f"failures-{stamp}.json"
    rows = []
    for case in cases:
        if a.only and case["id"] != a.only:
            continue
        project = HOME / f"case-{stamp}-{case['id']}"
        question, scope, data = case.get("question"), case.get("scope", ""), None
        if case["domain"] == "ml":
            if case.get("syntheticTask"):
                task = next(t for t in TASKS if t.id == case["syntheticTask"])
                question, scope = task.question, task.describe
                data = materialize(task, project / "data", secrets.randbelow(2**31))
            elif (ROOT / "evals/data/failures" / case["id"]).exists():
                data = ROOT / "evals/data/failures" / case["id"]
            else:
                rows.append({"id": case["id"], "skipped": "no data"})
                continue
        orch = build(project, case.get("budget", 1.5), None, autonomy="cap", domain=case["domain"], memory=False)
        orch.store.set_meta("title", question)
        if data:
            orch.store.set_meta("dataDir", str(data))
        orch.store.put({"type": "question", "createdBy": orch.human(), "title": question, "domain": case["domain"], "scope": scope})
        try:
            finish(orch)
        except Exception as exc:  # one case crashing must not stop the others
            print(f"{case['id']}: crashed: {exc}", flush=True)
        problems = check(case, orch.store)
        spent = sum(e["payload"]["costUsd"] for e in orch.store.events("model.call"))
        rows.append({"id": case["id"], "passed": not problems, "problems": problems, "spent_usd": round(spent, 4),
                     "project": str(project)})
        print(f"{case['id']}: {'PASSED' if not problems else 'FAILED: ' + '; '.join(problems)} · ${spent:.3f}", flush=True)
        out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ran = [r for r in rows if "passed" in r]
    summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cases": len(rows), "ran": len(ran),
               "passed": sum(r["passed"] for r in ran), "all_passed": bool(ran) and all(r["passed"] for r in ran),
               "spent_usd": round(sum(r.get("spent_usd", 0) for r in rows), 4), "rows": rows}
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
