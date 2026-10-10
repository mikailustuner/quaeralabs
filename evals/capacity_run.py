"""One command for the capacity measurements (capacity plan M1).

  # proof sets: both arms, interleaved, same per-task cap, clean recompilation of every solution
  tools/limited.sh 10G 300% uv run python -u evals/capacity_run.py --set capacity-synth --profile cheap --budget 4
  uv run python -u evals/capacity_run.py --set minif2f-valid --n 12 --profile balanced --provider openrouter --budget 6
  uv run python -u evals/capacity_run.py --set putnam --arms after --max-calls 8 --provider local     # $0 provider: bound by calls

  # discovery mini-set: one discovery project per target, scored by lemma progress (not by a yes/no answer)
  uv run python -u evals/capacity_run.py --set discovery --n 2 --budget 4

Providers: `claude` (Claude Code CLI), any id from the provider registry (`quaera providers list`), or `scripted`
(no model, no cost: regression of the harness itself). Results: evals/results/capacity-<set>-<profile>-<stamp>.json.
Memory, lemma bank and completion cache are off: every task starts from zero, and the cost is the true cost.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, SET_NAMES, SETS_DIR, load_set, run_set, score_discovery  # noqa: E402


def provider(name: str):
    from quaera.gateway import ClaudeCLIProvider, ScriptedProvider
    if name == "claude":
        return {"anthropic": ClaudeCLIProvider()}
    if name == "scripted":   # a fixed, usually wrong answer: exercises the whole loop without a model
        return {"scripted": ScriptedProvider(lambda s, p: "```lean\nimport Mathlib\n\ntheorem x : True := by\n  trivial\n```")}
    from quaera import registry
    registry.load_secrets()
    entry = next((e for e in registry.load()["providers"] if e["id"] == name), None)
    if entry is None:
        raise SystemExit(f"unknown provider {name}: claude, scripted, or a registry id")
    return {name: registry.build(entry)}


def run_proofs(a) -> dict:
    from quaera.gateway import Gateway
    from quaera.lean import LeanChecker, statement_of
    from quaera.permissions import Permissions
    from quaera.prover import Interactive
    events: list[dict] = []
    gw = Gateway(provider(a.provider), a.budget, Permissions.load().agents, lambda k, p: events.append({"kind": k, **p}),
                 profile_overrides={"engineer": a.profile}, effort_overrides={"engineer": "default"})
    lean = LeanChecker()
    tasks = load_set(a.set, n=a.n, seed=a.seed, split=a.split)
    arms = a.arms.split(",")
    per_task = None if a.max_calls and not a.per_task else (a.per_task or a.budget * 0.92 / (len(arms) * len(tasks)))
    hint_fn = None
    if not a.no_hints:
        from quaera.mathlib_index import MathlibIndex
        index = MathlibIndex.default()
        hint_fn = lambda text: "Mathlib declarations that may help:\n" + "\n".join(f"- {h['text']}" for h in index.search(text, 8))  # noqa: E731

    def check_factory(task):
        if not lean.check(task.statement_file, task.name).compiled:
            return None, None
        approved = statement_of(task.statement_file, task.name)
        return (lambda src, n=None, ap=None: lean.check(src, n or task.name, ap if n else approved).__dict__), approved

    out = run_set(tasks, arms, gw, check_factory, per_task_usd=per_task, per_task_calls=a.max_calls,
                  interactive=Interactive(goals=lean.goals, tactic=lean.tactic), hint_fn=hint_fn,
                  clean_check=lambda src, task, ap: lean.check(src, task.name, ap, clean=True).verified, events=events,
                  log=lambda m: print(m, flush=True))
    lean.close()
    return {**out, "perTaskUsd": per_task and round(per_task, 4), "perTaskCalls": a.max_calls, "spentUsd": round(gw.spent_usd, 4),
            "models": sorted({e["model"] for e in events if e["kind"] == "model.call"})}


def run_discovery(a) -> dict:
    import yaml
    import quaera.cli as cli
    from quaera.store import Store, now
    targets = yaml.safe_load((SETS_DIR / "capacity-discovery.yaml").read_text(encoding="utf-8"))["targets"][: a.n]
    home = Path(os.environ.get("QUAERA_HOME", ROOT / "evals" / "tmp-discovery")) / "projects"
    cli.HOME = home
    rows = []
    for t in targets:
        pid = f"capacity-discovery-{t['id']}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
        pre = Store(home / pid / "quaera.db")
        pre.set_meta("mode", "discover")
        pre.close()
        orch = cli.build(home / pid, a.budget / max(1, len(targets)), None, providers=provider(a.provider), autonomy="cap",
                         domain="math", memory=False)
        orch.store.set_meta("title", t["question"])
        orch.store.put({"type": "question", "createdBy": orch.human(), "title": t["question"], "domain": "math",
                        "scope": "Formal proof in Lean 4 + Mathlib"})
        try:
            orch.run()
        finally:
            orch.tools.close()
        score = score_discovery(orch.store)
        rows.append({"id": t["id"], "project": pid, **score, "spentUsd": round(orch.gateway.spent_usd, 4)})
        print(f"{t['id']}: {json.dumps(score)}", flush=True)
        orch.store.close()
    return {"rows": {"discovery": rows}, "arms": {"discovery": {"targets": len(rows), "verifiedLemmas": sum(r["verifiedLemmas"] for r in rows),
                                                                "refutedLemmas": sum(r["refutedLemmas"] for r in rows),
                                                                "mainSolved": sum(r["mainSolved"] for r in rows),
                                                                "spentUsd": round(sum(r["spentUsd"] for r in rows), 4)}}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, choices=[*SET_NAMES, "discovery"])
    ap.add_argument("--split", default="dev", choices=["dev", "hidden", "all"], help="capacity-synth only")
    ap.add_argument("--arms", default="before,after")
    ap.add_argument("--profile", default="cheap", choices=["cheap", "balanced", "best"])
    ap.add_argument("--provider", default="claude")
    ap.add_argument("--budget", type=float, required=True, help="hard total USD cap (all arms together)")
    ap.add_argument("--per-task", type=float, help="USD per task and arm (default: the budget split evenly, 8%% margin)")
    ap.add_argument("--max-calls", type=int, help="model calls per task and arm (for $0 providers)")
    ap.add_argument("--n", type=int)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--no-hints", action="store_true", help="after-arm without Mathlib index hints")
    ap.add_argument("--out", help="result file (default under evals/results/)")
    a = ap.parse_args(argv)
    os.environ["QUAERA_CACHE"] = "off"
    res = run_discovery(a) if a.set == "discovery" else run_proofs(a)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = Path(a.out) if a.out else ROOT / "evals" / "results" / f"capacity-{a.set}-{a.profile}-{stamp}.json"
    summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "set": a.set, "split": a.split, "profile": a.profile,
               "provider": a.provider, "budgetUsd": a.budget, "seed": a.seed, **res}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))
    print(f"→ {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
