"""Capacity comparison (docs/capacity-plan.md): the previous proof search vs the Phase 1–3 search, same model, same tasks,
same per-task budget cap. The question it answers: with a cheap model, do the new mechanisms turn the same budget into
more Lean-verified proofs?

  before  prove_search as shipped before the capacity plan: 4 whole-proof attempts (2 parallel in the first round),
          2 sketches, one round, fast automation only
  after   the same entry point with the capacity features: heavy automation (K3), step-by-step proving in the Lean
          REPL (K2), ranked Mathlib hints (K4), 2 candidates (K5), recursive decomposition (K6) and further rounds while
          the task's budget share remains (R2)

Both arms run on the same Claude profile, interleaved task by task; a hard total cap is enforced by the gateway, and each
arm gets the same cap per task (checked before every call). Every solution is recompiled in a clean Lean process.

  tools/limited.sh 10G 300% uv run python -u evals/capacity_compare.py --n 8 --budget 3 --profile cheap
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class TaskBudget(Exception):
    """This arm's budget for the task is used up."""


def main() -> int:
    from quaera.gateway import BudgetExceeded, ClaudeCLIProvider, Gateway, ModelError
    from quaera.lean import LeanChecker, statement_of
    from quaera.mathlib_index import MathlibIndex
    from quaera.permissions import Permissions
    from quaera.prover import Interactive, prove_search

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "evals/data/putnam-sample-7.jsonl"))
    ap.add_argument("--n", type=int, default=8, help="tasks whose statement compiles")
    ap.add_argument("--budget", type=float, default=3.0, help="hard total USD cap for both arms together")
    ap.add_argument("--profile", default="cheap")
    a = ap.parse_args()

    rows = [json.loads(l) for l in Path(a.data).read_text(encoding="utf-8").splitlines()]
    events: list[dict] = []
    gw = Gateway({"anthropic": ClaudeCLIProvider()}, a.budget, Permissions.load().agents, lambda k, p: events.append({"kind": k, **p}),
                 profile_overrides={"engineer": a.profile}, effort_overrides={"engineer": "default"})
    lean = LeanChecker()
    index = MathlibIndex.default()
    tasks = []
    for r in rows:
        name, stmt = r["name"], r.get("statement_file") or (r["formal_statement"].rstrip() + " sorry\n")
        if lean.check(stmt, name).compiled:
            tasks.append((name, stmt))
        if len(tasks) >= a.n:
            break
    per_task = a.budget / (2 * len(tasks))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = ROOT / "evals/results" / f"capacity-compare-{stamp}.json"
    results = {"before": [], "after": []}

    def save(final: bool) -> dict:
        summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "complete": final, "data": Path(a.data).name,
                   "profile": a.profile, "models": sorted({e["model"] for e in events if e["kind"] == "model.call"}),
                   "tasks": len(tasks), "perTaskUsd": round(per_task, 4), "budgetUsd": a.budget, "spentUsd": round(gw.spent_usd, 4),
                   "arms": {arm: {"solved": sum(r["solved"] for r in rs), "attempted": len(rs),
                                  "modelCalls": sum(r["calls"] for r in rs), "spentUsd": round(sum(r["usd"] for r in rs), 4),
                                  "secondsTotal": round(sum(r["seconds"] for r in rs), 1)} for arm, rs in results.items()},
                   "rows": results}
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary

    for i, (name, stmt) in enumerate(tasks, 1):
        approved = statement_of(stmt, name)
        check = lambda src, n=None, ap_=None: lean.check(src, n or name, ap_ if n else approved).__dict__  # noqa: E731
        for arm in ("before", "after"):
            start, t0, calls = gw.spent_usd, time.monotonic(), []

            def ask(system, prompt, n, lane=None, _start=start, _calls=calls):
                if gw.spent_usd - _start >= per_task:
                    raise TaskBudget(f"task budget ${per_task:.3f} used")
                _calls.append(1)
                return gw.call("engineer", system, prompt, n)[0].text
            row = {"name": name, "solved": False}
            try:
                if arm == "before":
                    res = prove_search(stmt, name, ask, check, attempts=4, parallel=2, sketches=2)
                else:
                    hints = "Mathlib declarations that may help:\n" + "\n".join(
                        f"- {h['text']}" for h in index.search(approved or stmt, 8))
                    res = prove_search(stmt, name, ask, check, attempts=2, samples=2, sketches=1, heavy=True, depth=2, hints=hints,
                                       rounds=3, more=lambda _s=start: gw.spent_usd - _s < per_task * 0.9,
                                       interactive=Interactive(goals=lean.goals, tactic=lean.tactic))
                if res.solved:
                    row["solved"] = lean.check(res.source, name, approved, clean=True).verified
                row["kinds"] = [e["kind"] for e in res.log]
            except TaskBudget as exc:
                row["stopped"] = str(exc)
            except BudgetExceeded as exc:
                row["stopped"] = f"total cap: {exc}"
            except ModelError as exc:
                row["stopped"] = f"model error: {str(exc)[:200]}"
            row.update(calls=len(calls), usd=round(gw.spent_usd - start, 4), seconds=round(time.monotonic() - t0, 1))
            results[arm].append(row)
            print(f"[{i}/{len(tasks)}] {arm:<6} {name}: {'SOLVED' if row['solved'] else 'no'} · {row['calls']} calls · ${row['usd']:.3f}"
                  + (f" · {row.get('stopped')}" if row.get("stopped") else ""), flush=True)
            save(False)
    s = save(True)
    print(json.dumps({k: v for k, v in s.items() if k != "rows"}, ensure_ascii=False, indent=2))
    lean.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
