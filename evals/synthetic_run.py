"""Synthetic discovery set: the agent team (ML loop) solves every task with real models in the sandbox.

Gate criterion (v0.2, fixed before the run): in at least 8 of the 10 tasks (5 "yes", 5 "no") the Writer's answer MUST be
correct AND the result MUST be reproduced exactly by the Verifier with the same seed; in addition, at least 4/5 must be
correct in each answer class (a team biased toward one answer cannot pass the gate).

Usage: uv run python evals/synthetic_run.py --budget-per-task 1.5 [--only syn-01]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals" / "synthetic"))

from tasks import TASKS, materialize  # noqa: E402

from quaera.cli import HOME, build, finish  # noqa: E402

GATE_MIN_CORRECT = 8
GATE_MIN_PER_CLASS = 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-per-task", type=float, required=True)
    ap.add_argument("--only")
    a = ap.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = ROOT / "evals/results" / f"synthetic-{stamp}.json"
    rows = []

    def save(final: bool) -> dict:
        counted = sum(r["counts"] for r in rows)
        per_class = {c: sum(r["counts"] for r in rows if r["truth"] == c) for c in ("yes", "no")}
        summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "complete": final,
                   "tasks": len(rows), "correct": sum(r["correct"] for r in rows), "correct_and_reproduced": counted,
                   "per_class_correct_and_reproduced": per_class, "always_no_baseline": sum(r["truth"] == "no" for r in rows),
                   "gate_min": GATE_MIN_CORRECT, "gate_min_per_class": GATE_MIN_PER_CLASS,
                   "passed": (counted >= GATE_MIN_CORRECT and min(per_class.values()) >= GATE_MIN_PER_CLASS
                              and len(rows) == len(TASKS)),
                   "spent_usd": round(sum(r["spent_usd"] for r in rows), 4), "rows": rows}
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary
    for task in TASKS:
        if a.only and task.id != a.only:
            continue
        project = HOME / f"synthetic-{stamp}-{task.id}"
        seed = secrets.randbelow(2**31)   # hidden: not given to the team, only written to the result file
        data = materialize(task, project / "data", seed)
        orch = build(project, a.budget_per_task, None, autonomy="cap", domain="ml", memory=False)
        orch.store.set_meta("title", task.question)
        orch.store.set_meta("dataDir", str(data))
        orch.store.put({"type": "question", "createdBy": orch.human(), "title": task.question, "domain": "ml",
                        "scope": task.describe})
        try:
            finish(orch)
        except Exception as exc:  # one task crashing must not stop the others
            print(f"{task.id}: crashed: {exc}", flush=True)
        s = orch.store
        w = next((e["payload"] for e in reversed(s.events("writer.output"))), {"answer": "unclear"})
        vers = s.latest("verification")
        reproduced = bool(vers) and vers[-1]["reproduced"] == "yes"
        h = next((x for x in s.latest("hypothesis") if x["status"] not in ("draft", "rejected")), None)
        spent = sum(e["payload"]["costUsd"] for e in s.events("model.call"))
        row = {"id": task.id, "truth": task.truth, "answer": w["answer"], "correct": w["answer"] == task.truth,
               "reproduced": reproduced, "counts": w["answer"] == task.truth and reproduced,
               "hypothesis": h and {"id": h["id"], "status": h["status"], "statement": h["statement"]},
               "rule_violations": s.check_final(), "spent_usd": round(spent, 4), "data_seed": seed, "project": str(project)}
        rows.append(row)
        print(f"{task.id}: truth={task.truth} · team={w['answer']} · reproduced={reproduced} · ${spent:.3f}", flush=True)
        save(final=False)   # to disk after every task: no results lost on a crash
    summary = save(final=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
