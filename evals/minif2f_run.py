"""miniF2F (Lean 4) baseline.

The Engineer agent makes at most k attempts per problem; after every failed attempt it gets the compiler
feedback. A proof counts only if it compiles in the Lean REPL with the approved statement unchanged, without
sorry and without non-standard axioms. The budget cap is enforced by the gateway and is never exceeded.

Usage: uv run python evals/minif2f_run.py --split test --n 20 --attempts 2 --budget 6 --profile balanced
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

from quaera import prompts  # noqa: E402
from quaera.gateway import BudgetExceeded, ClaudeCLIProvider, Gateway  # noqa: E402
from quaera.lean import LeanChecker, normalize, statement_of  # noqa: E402
from quaera.orchestrator import extract_lean, feedback_text  # noqa: E402
from quaera.permissions import Permissions  # noqa: E402

HEADER = "import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\nopen BigOperators Real Nat Topology Rat\n\n"


def modernize(stmt: str) -> str:
    return re.sub(r"([∑∏]\s*\S+(?:\s*:\s*[^,]+?)?)\s+in\s+", r"\1 ∈ ", stmt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--profile", choices=["cheap", "balanced", "best"], default="balanced")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    rows = [json.loads(l) for l in (ROOT / "evals/data/minif2f-deepseek-v15.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r["split"] == a.split]
    random.Random(a.seed).shuffle(rows)
    rows = rows[: a.n]

    perms = Permissions.load()
    events: list[dict] = []
    gw = Gateway({"anthropic": ClaudeCLIProvider()}, a.budget, perms.agents, lambda k, p: events.append({"kind": k, **p}),
                 profile_overrides={"engineer": a.profile})
    lean = LeanChecker()
    out = []
    for i, r in enumerate(rows, 1):
        stmt = modernize(r["formal_statement"]).rstrip()
        name = r["name"]
        pilot_src = HEADER + stmt + " sorry\n"
        pilot = lean.check(pilot_src, name)
        row = {"name": name, "pilot_ok": pilot.compiled, "solved": False, "attempts": 0, "seconds": []}
        if not pilot.compiled:
            row["pilot_errors"] = pilot.errors[:2]
            out.append(row)
            print(f"[{i}/{len(rows)}] {name}: statement does not compile in current Mathlib, skipped", flush=True)
            continue
        approved = statement_of(pilot_src, name)
        prompt = (f"Prove this theorem. Keep the theorem name `{name}` and its statement exactly.\n"
                  f"```lean\n{HEADER}{stmt} sorry\n```").replace("quaera_main", name)
        system = prompts.PROVE.replace("`quaera_main`", f"`{name}`")
        try:
            for attempt in range(a.attempts):
                completion, _ = gw.call("engineer", system, prompt, 12000)
                src = extract_lean(completion.text)
                if "import Aesop" not in src:
                    src = src.replace("import Mathlib", "import Mathlib\nimport Aesop", 1)
                rep = lean.check(src, name, approved)
                row["attempts"] += 1
                row["seconds"].append(rep.seconds)
                if rep.verified:
                    row["solved"] = True
                    row["proof"] = src
                    break
                prompt += f"\n\nAttempt {attempt + 1} failed:\n```lean\n{src}\n```\n{feedback_text(rep.__dict__)}\nFix it."
        except BudgetExceeded as exc:
            row["budget_stop"] = str(exc)
            out.append(row)
            print(f"budget cap: {exc}", flush=True)
            break
        out.append(row)
        print(f"[{i}/{len(rows)}] {name}: {'SOLVED' if row['solved'] else 'not solved'} ({row['attempts']} attempts) · spent ${gw.spent_usd:.3f}", flush=True)
    # Every solved proof is re-verified independently of the REPL with a one-off compile in a clean environment.
    for row in out:
        if row["solved"]:
            pilot_src = HEADER + modernize(next(r for r in rows if r["name"] == row["name"])["formal_statement"]).rstrip() + " sorry\n"
            clean = lean.check(row["proof"], row["name"], statement_of(pilot_src, row["name"]), clean=True)
            row["clean_verified"] = clean.verified
            row["axioms"] = clean.axioms
            if not clean.verified:
                row["solved"] = False
                row["clean_problems"] = clean.problems + clean.errors[:2]
                print(f"{row['name']}: not verified in a clean environment → counted as unsolved", flush=True)
    lean.close()

    usable = [r for r in out if r["pilot_ok"]]
    attempted = [r for r in usable if r["attempts"] > 0]
    solved = [r for r in usable if r["solved"]]
    summary = {
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": a.split, "sampled": len(rows), "seed": a.seed, "attempts_per_problem": a.attempts,
        "profile": a.profile, "models": sorted({e["model"] for e in events if e["kind"] == "model.call"}),
        "statement_compile_ok": len(usable), "attempted": len(attempted), "solved": len(solved),
        f"pass@{a.attempts}": round(len(solved) / len(attempted), 3) if attempted else None,
        "clean_reverified": sum(1 for r in out if r.get("clean_verified")),
        "contamination_note": "miniF2F has been public since 2021; the problems and their solutions may be in model training data. Read this value as an optimistic ceiling, not a lower bound.",
        "spent_usd": round(gw.spent_usd, 4), "budget_usd": a.budget,
        "budget_never_exceeded": gw.spent_usd <= a.budget, "rows": out,
    }
    path = ROOT / "evals" / "results" / f"minif2f-{a.split}-{summary['createdAt'][:10]}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
