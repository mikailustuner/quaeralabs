"""Discriminative proof benchmark: a fixed-seed sample of PutnamBench (Lean 4, Apache-2.0) problems.

Why: our own template sets hit the ceiling (51/51), so we could not tell whether an improvement made the team stronger.
Putnam problems are beyond olympiad difficulty and their formal proofs are largely unpublished. Natural-language
solutions are widespread, so contamination is not zero; read the results with that caveat.

  uv run python evals/putnam_run.py prepare --src <PutnamBench>/lean4/src --n 20 --seed 7
  tools/limited.sh 8G 300% uv run python -u evals/putnam_run.py run --seed 7 --strategy baseline --attempts 2 --budget 6

Only problems that need no answer guess (no `_solution` definition) are taken. Statements that do not compile
with our Mathlib version are excluded from the measurement and counted separately.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "evals" / "data"


def prepare(src: Path, n: int, seed: int) -> Path:
    files = sorted(p for p in src.glob("putnam_*.lean") if "_solution" not in p.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    pick = rng.sample(files, min(n, len(files)))
    rows = []
    for p in sorted(pick):
        text = p.read_text(encoding="utf-8").strip()
        name = re.search(r"theorem\s+(putnam_\w+)", text).group(1)
        rows.append({"name": name, "year": int(name.split("_")[1]), "statement_file": text + "\n",
                     "source": "PutnamBench (trishullab/PutnamBench, Apache-2.0)"})
    out = DATA / f"putnam-sample-{seed}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return out


class ProblemBudget(Exception):
    """The budget for this problem ran out; move on to the next problem."""


def run(seed: int, strategy: str, attempts: int, budget: float, profile: str, effort: str | None,
        continue_from: Path | None = None, per_problem: float | None = None) -> int:
    from quaera.gateway import BudgetExceeded, ClaudeCLIProvider, Gateway, ModelError
    from quaera.lean import LeanChecker, statement_of
    from quaera.permissions import Permissions
    from quaera.prover import STRATEGIES

    rows = [json.loads(l) for l in (DATA / f"putnam-sample-{seed}.jsonl").read_text(encoding="utf-8").splitlines()]
    perms = Permissions.load()
    events: list[dict] = []
    gw = Gateway({"anthropic": ClaudeCLIProvider()}, budget, perms.agents, lambda k, p: events.append({"kind": k, **p}),
                 profile_overrides={"engineer": profile}, effort_overrides={"engineer": effort} if effort else None)
    lean = LeanChecker()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = ROOT / "evals/results" / f"putnam-{seed}-{strategy}-{stamp}.json"
    out, prior_spent = [], 0.0
    if continue_from:
        # Continue a run that ran out of budget: finished problems are kept as-is, the rest run with the new budget.
        prev = json.loads(continue_from.read_text(encoding="utf-8"))
        out = [r for r in prev["rows"] if "budget_stop" not in r]
        prior_spent = prev["spent_usd"] + prev.get("prior_spent_usd", 0.0)
        path = continue_from
    done_names = {r["name"] for r in out}

    def save(final: bool):
        attempted = [r for r in out if r.get("pilot_ok") and r["attempts"] > 0]
        summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "complete": final, "seed": seed,
                   "strategy": strategy, "attempts": attempts, "profile": profile, "effort": effort, "per_problem_usd": per_problem,
                   "problems": len(rows), "statement_compile_ok": sum(1 for r in out if r.get("pilot_ok")),
                   "attempted": len(attempted), "solved": sum(r["solved"] for r in attempted),
                   # Denominator: problems whose statement compiles. A problem with no attempt due to a model error (e.g. output limit) also counts as a failure.
                   "solve_rate": round(sum(r["solved"] for r in out if r.get("pilot_ok")) / max(1, sum(1 for r in out if r.get("pilot_ok"))), 3),
                   "model_calls": sum(r["attempts"] for r in out),
                   "models": sorted({e["model"] for e in events if e["kind"] == "model.call"}),
                   "spent_usd": round(gw.spent_usd, 4), "prior_spent_usd": round(prior_spent, 4),
                   "total_spent_usd": round(gw.spent_usd + prior_spent, 4), "budget_usd": budget, "rows": out}
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary

    for i, r in enumerate(rows, 1):
        name, stmt = r["name"], r["statement_file"]
        if name in done_names:
            continue
        pilot = lean.check(stmt, name)
        row = {"name": name, "pilot_ok": pilot.compiled, "solved": False, "attempts": 0}
        if not pilot.compiled:
            row["pilot_errors"] = pilot.errors[:2]
            out.append(row)
            print(f"[{i}/{len(rows)}] {name}: statement does not compile with this Mathlib version (excluded)", flush=True)
            save(False)
            continue
        approved = statement_of(stmt, name)
        start, calls = gw.spent_usd, []

        def ask(system, prompt, n, _start=start, _calls=calls):
            # Per-problem cap: checked BEFORE every call (overshoot of at most one call is possible; it shows in the file).
            if per_problem is not None and gw.spent_usd - _start >= per_problem:
                raise ProblemBudget(f"problem budget (${per_problem}) used up")
            _calls.append(1)
            return gw.call("engineer", system, prompt, n)[0].text
        check = lambda src, n=None, a=None: lean.check(src, n or name, a if n else approved).__dict__  # noqa: E731
        try:
            res = STRATEGIES[strategy](stmt, name, ask, check, attempts=attempts)
            row["attempts"] = res.attempts
            if res.solved:
                clean = lean.check(res.source, name, approved, clean=True)   # independent recompilation
                row["solved"], row["proof"] = clean.verified, res.source
            row["log"] = [{k: v for k, v in e.items() if k != "source"} for e in res.log]
        except BudgetExceeded as exc:
            row["budget_stop"] = str(exc)
            out.append(row)
            print(f"[{i}/{len(rows)}] budget exhausted: {exc}", flush=True)
            break
        except ModelError as exc:
            row["model_error"] = str(exc)[:200]
        except ProblemBudget as exc:
            row["problem_budget_stop"] = str(exc)
        row["spent_usd"] = round(gw.spent_usd - start, 4)
        row["attempts"] = max(row["attempts"], len(calls))      # real call count even if cut short by budget or model error
        out.append(row)
        print(f"[{i}/{len(rows)}] {name}: {'SOLVED' if row['solved'] else 'not solved'} ({row['attempts']} calls) · ${gw.spent_usd:.3f}", flush=True)
        save(False)
    lean.close()
    summary = save(True)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--src", type=Path, required=True)
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--seed", type=int, required=True)
    r = sub.add_parser("run")
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--strategy", default="baseline")
    r.add_argument("--attempts", type=int, default=2)
    r.add_argument("--budget", type=float, required=True)
    r.add_argument("--profile", choices=["cheap", "balanced", "best"], default="balanced")
    r.add_argument("--per-problem", type=float, help="per-problem budget cap (USD); for equal-budget comparisons")
    r.add_argument("--continue-from", type=Path, help="continue a result file that ran out of budget with the remaining problems")
    r.add_argument("--effort", choices=["default", "low", "medium", "high", "xhigh", "max"],
                   help="Engineer effort; if omitted, the value in agents/engineer.yaml. Phase 4 baseline: default")
    a = ap.parse_args()
    if a.cmd == "prepare":
        print(prepare(a.src, a.n, a.seed))
        return 0
    return run(a.seed, a.strategy, a.attempts, a.budget, a.profile, a.effort, a.continue_from, a.per_problem)


if __name__ == "__main__":
    sys.exit(main())
