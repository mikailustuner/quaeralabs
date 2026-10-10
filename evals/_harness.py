"""Evaluation harness (capacity plan M1): one loader, one task runner, one result schema and one failure classifier for
every proof benchmark, so every change is judged on the same sets with the same numbers.

Sets (`load_set`):
  putnam            PutnamBench sample (seed 7), far above small models — a ceiling probe
  minif2f-valid     miniF2F valid split without the easy mathd_* problems
  minif2f-test      miniF2F test split (public since 2021: read as an optimistic ceiling)
  synth-1/2/3       the generated QuaeraLabs-Synth-Math sets (template, hard, olympiad)
  capacity-synth    30 statements in three tiers (10 per synth set, fixed seed); `split` selects dev or hidden.
                    The hidden ids are listed in evals/sets/capacity-synth.yaml and must never reach a prompt or a rule.

Arms (`run_task`): `before` is the proof search as shipped before the capacity plan; `after` adds Phase 1–3
(heavy automation, REPL steps, Mathlib hints, candidates, decomposition, budget rounds).

Failure classes (`classify`): invalid_json, output_cut, model_error, compile_error, budget, timeout, unsolved.
Discovery (`score_discovery`): verified and refuted lemmas, dead strategies, rounds and synthesis of one project.
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "evals" / "data"
SETS_DIR = ROOT / "evals" / "sets"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

SYNTH = {"synth-1": "synth-math-2026.jsonl", "synth-2": "synth-math-hard-2027.jsonl", "synth-3": "synth-math-olympiad-2028.jsonl"}
SYNTH_HEADER = "import Mathlib\n\nset_option maxHeartbeats 400000\n\nopen BigOperators Real Nat\n\n"
SET_NAMES = ("putnam", "minif2f-valid", "minif2f-test", *SYNTH, "capacity-synth")


@dataclass
class Task:
    name: str
    statement_file: str
    tier: int | None = None


def _jsonl(name: str) -> list[dict]:
    return [json.loads(l) for l in (DATA / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def _synth_task(r: dict, tier: int | None = None) -> Task:
    return Task(r["name"], SYNTH_HEADER + r["formal_statement"].rstrip() + " sorry\n", tier)


def capacity_synth_split() -> dict:
    return yaml.safe_load((SETS_DIR / "capacity-synth.yaml").read_text(encoding="utf-8"))


def build_capacity_synth(seed: int = 2026, per_tier: int = 10, hidden: float = 0.4) -> dict:
    """Generates the frozen tiered set definition (ids only). Run once; the YAML is committed and not regenerated."""
    rng = random.Random(seed)
    tiers, ids = {}, []
    for tier, (key, file) in enumerate(SYNTH.items(), 1):
        names = [r["name"] for r in _jsonl(file)]
        pick = rng.sample(names, min(per_tier, len(names)))
        tiers[f"tier{tier}"] = {"source": file, "ids": pick}
        ids += pick
    rng.shuffle(ids)
    cut = int(len(ids) * hidden)
    return {"version": 1, "seed": seed, "tiers": tiers, "hidden": sorted(ids[:cut]), "dev": sorted(ids[cut:])}


def load_set(name: str, n: int | None = None, seed: int = 0, split: str = "dev") -> list[Task]:
    from minif2f_run import HEADER, modernize
    if name == "putnam":
        tasks = [Task(r["name"], r["statement_file"]) for r in _jsonl("putnam-sample-7.jsonl")]
    elif name.startswith("minif2f-"):
        want = name.split("-", 1)[1]
        rows = [r for r in _jsonl("minif2f-deepseek-v15.jsonl") if r["split"] == want and not (want == "valid" and r["name"].startswith("mathd"))]
        random.Random(seed).shuffle(rows)
        tasks = [Task(r["name"], HEADER + modernize(r["formal_statement"]).rstrip() + " sorry\n") for r in rows]
    elif name in SYNTH:
        tasks = [_synth_task(r) for r in _jsonl(SYNTH[name])]
    elif name == "capacity-synth":
        spec = capacity_synth_split()
        keep = set(spec[split]) if split in ("dev", "hidden") else set(spec["dev"]) | set(spec["hidden"])
        tasks = []
        for tier, info in enumerate(spec["tiers"].values(), 1):
            rows = {r["name"]: r for r in _jsonl(info["source"])}
            tasks += [_synth_task(rows[i], tier) for i in info["ids"] if i in keep and i in rows]
    else:
        raise ValueError(f"unknown set {name}; known: {', '.join(SET_NAMES)}")
    return tasks[:n] if n else tasks


class TaskBudget(Exception):
    """The arm's budget for this task is used up."""


def run_task(task: Task, arm: str, ask: Callable, check: Callable, *, interactive=None, hints: str = "",
             more: Callable[[], bool] | None = None) -> dict:
    """One task in one arm. `ask(system, prompt, n, lane=None)` raises TaskBudget when the task's share is used;
    `check(src, name=None, approved=None) -> dict`. Returns the row (without timing and cost: the caller adds them)."""
    from quaera.gateway import BudgetExceeded, ModelError
    from quaera.prover import prove_search
    row = {"name": task.name, "tier": task.tier, "solved": False, "source": None, "kinds": [], "stopped": None}
    try:
        if arm == "before":
            res = prove_search(task.statement_file, task.name, ask, check, attempts=4, parallel=2, sketches=2)
        elif arm == "after":
            res = prove_search(task.statement_file, task.name, ask, check, attempts=2, samples=2, sketches=1, heavy=True, depth=2,
                               hints=hints, rounds=3, more=more, interactive=interactive)
        else:
            raise ValueError(f"unknown arm {arm}")
        row["solved"], row["source"] = res.solved, res.source
        row["kinds"] = [e["kind"] for e in res.log]
        row["errors"] = [x for e in res.log for x in (e.get("errors") or [])][:12]
    except TaskBudget as exc:
        row["stopped"] = f"task budget: {exc}"
    except BudgetExceeded as exc:
        row["stopped"] = f"total cap: {exc}"
    except ModelError as exc:
        row["stopped"] = f"model error: {str(exc)[:200]}"
    return row


def classify(row: dict, events: list[dict]) -> str:
    """The single failure class of an unsolved row (events: the gateway events recorded while it ran)."""
    if row["solved"]:
        return "solved"
    stop = row.get("stopped") or ""
    if "budget" in stop or "cap" in stop:
        return "budget"
    if any(e["kind"] == "model.invalid_json" for e in events):
        return "invalid_json"
    errs = [str(e.get("error", "")) for e in events if e["kind"] == "model.error"] + ([stop] if stop.startswith("model error") else [])
    if any(re.search(r"cut off|output limit|exceeded the \d+ output", x) for x in errs):
        return "output_cut"
    if errs:
        return "model_error"
    compile_errs = row.get("errors") or []
    if any("timed out" in x for x in compile_errs):
        return "timeout"
    if compile_errs:
        return "compile_error"
    return "unsolved"


def summarize(rows: list[dict]) -> dict:
    out = {"tasks": len(rows), "solved": sum(r["solved"] for r in rows), "verifiedClean": sum(bool(r.get("verifiedClean")) for r in rows),
           "modelCalls": sum(r.get("calls", 0) for r in rows), "tokens": sum(r.get("tokens", 0) for r in rows),
           "spentUsd": round(sum(r.get("usd", 0) for r in rows), 4), "seconds": round(sum(r.get("seconds", 0) for r in rows), 1),
           "failures": {}}
    for r in rows:
        out["failures"][r["failure"]] = out["failures"].get(r["failure"], 0) + 1
    tiers = sorted({r.get("tier") for r in rows if r.get("tier")})
    if tiers:
        out["byTier"] = {f"tier{t}": {"tasks": sum(1 for r in rows if r.get("tier") == t),
                                      "solved": sum(r["solved"] for r in rows if r.get("tier") == t)} for t in tiers}
    return out


def run_set(tasks: list[Task], arms: list[str], gateway, check_factory: Callable, *, per_task_usd: float | None,
            per_task_calls: int | None = None, interactive=None, hint_fn: Callable[[str], str] | None = None,
            clean_check: Callable | None = None, log: Callable = print, events: list | None = None) -> dict:
    """Runs every task in every arm (interleaved task by task, so time-dependent effects hit both arms alike).
    `check_factory(task) -> (check, approved)`; `clean_check(source, task, approved) -> bool` recompiles a solution
    in a clean process. Per-task caps (USD and/or calls) are checked before every model call."""
    events = events if events is not None else []
    results: dict[str, list[dict]] = {a: [] for a in arms}
    for i, task in enumerate(tasks, 1):
        check, approved = check_factory(task)
        if check is None:
            log(f"[{i}/{len(tasks)}] {task.name}: statement does not compile (excluded)")
            continue
        for arm in arms:
            start_usd, start_tokens, t0, mark = gateway.spent_usd, gateway.tokens, time.monotonic(), len(events)
            calls: list[int] = []

            def ask(system, prompt, n, lane=None, _s=start_usd, _c=calls):
                if per_task_usd is not None and gateway.spent_usd - _s >= per_task_usd:
                    raise TaskBudget(f"${per_task_usd:.3f} used")
                if per_task_calls is not None and len(_c) >= per_task_calls:
                    raise TaskBudget(f"{per_task_calls} calls used")
                _c.append(1)
                return gateway.call("engineer", system, prompt, n, sample=(ord(lane) - 64) if lane and lane != "A" else None)[0].text

            more = (lambda _s=start_usd: gateway.spent_usd - _s < per_task_usd * 0.9) if per_task_usd is not None else \
                (lambda _c=calls: per_task_calls is None or len(_c) < per_task_calls)
            row = run_task(task, arm, ask, check, interactive=interactive, hints=hint_fn(approved or task.statement_file) if hint_fn and arm == "after" else "",
                           more=more)
            if row["solved"] and clean_check is not None:
                row["verifiedClean"] = bool(clean_check(row["source"], task, approved))
                row["solved"] = row["verifiedClean"]
            row.update(calls=len(calls), usd=round(gateway.spent_usd - start_usd, 4), tokens=gateway.tokens - start_tokens,
                       seconds=round(time.monotonic() - t0, 1))
            row["failure"] = classify(row, events[mark:])
            row.pop("source", None)
            results[arm].append(row)
            log(f"[{i}/{len(tasks)}] {arm:<6} {task.name}: {'SOLVED' if row['solved'] else row['failure']} · {row['calls']} calls · ${row['usd']:.3f}")
    return {"arms": {a: summarize(rs) for a, rs in results.items()}, "rows": results}


def score_discovery(store) -> dict:
    """Progress of one discovery project (the discovery mini-set scores this, not a yes/no answer)."""
    status = {}
    for e in store.events("lemma.status"):
        if e["payload"]["status"] in ("verified", "refuted"):
            status[e["payload"]["id"]] = e["payload"]["status"]
    syn = store.events("synthesis.done")
    return {"verifiedLemmas": sum(1 for s in status.values() if s == "verified"),
            "refutedLemmas": sum(1 for s in status.values() if s == "refuted"),
            "lemmas": len(store.events("program.lemma")),
            "deadStrategies": len({e["payload"]["id"] for e in store.events("strategy.dead")}),
            "strategies": len(store.events("strategy.proposed")),
            "rounds": len(store.events("attack.round")),
            "mainSolved": bool(syn and syn[-1]["payload"].get("solved")),
            "bankReused": len(store.events("bank.reused"))}
