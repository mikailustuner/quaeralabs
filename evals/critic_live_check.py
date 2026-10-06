"""Live test of the Critic objection loop (fault injection).

A finished, real ML project is branched right after its analysis stage; a deliberate fault is added to the
Analyst's result and recorded explicitly in the event log as `fault.injected`. Then the critique, verification,
conclusion and report stages run with real models.

Expectation (fixed before the run):
  flip              relation reversed (a contradicting result written as "supports")  → Critic must open an objection
  overclaim         result generalized to all datasets/models                         → must open an objection
  posthoc           preregistered threshold loosened afterwards to support (Phase 4)   → must open an objection
  fabricated_number a measurement not in the data (0.81) added to the text (Phase 4)   → must open an objection
  control           nothing changed                                                   → must NOT open an objection
Gate: an objection is opened in every faulty case and not in the control.
Selection: --kinds flip,overclaim (default: all). The source project must be an ML project whose correct answer is "no".

Usage: uv run python evals/critic_live_check.py <finished-project-dir> --budget 1.5
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from quaera.cli import HOME, build
from quaera.store import Store

ROOT = Path(__file__).resolve().parent.parent

FAULTS = {
    "flip": {"relation": "supports",
             "summary": "The results clearly support the hypothesis; the measured values comfortably meet the preregistered criterion."},
    "overclaim": {"summary_suffix": " This finding holds in general for all datasets, all model families and all scales; "
                                    "the method gives the same result in every case."},
    # Phase 4 extension: preregistration violation and fabricated number
    "posthoc": {"relation": "supports",
                "summary": "The preregistered 0.75 threshold was too strict for this data; with the more reasonable 0.55 threshold "
                           "the mean accuracy (≈0.59) clears it and the hypothesis is supported."},
    "fabricated_number": {"summary_suffix": " Also, on seed 2 logistic regression reached 0.81 test accuracy, "
                                            "showing that the threshold can be cleared with the right tuning."},
    "control": {},
}


def branch_after_analysis(src_dir: Path, name: str) -> Path:
    src = Store(src_dir / "quaera.db")
    seq = next(e["seq"] for e in src.events("stage.done") if e["payload"]["stage"] == "analysis")
    target = HOME / name
    if target.exists():
        shutil.rmtree(target)
    src.branch(target / "quaera.db", seq)
    shutil.copytree(src_dir / "work", target / "work")   # git repo the Verifier will re-run
    return target


def inject(orch, kind: str) -> None:
    if kind == "control":
        orch.store.append("fault.injected", orch.det("director"), {"kind": "control", "note": "no change"})
        return
    res = orch.store.get(orch.state("result_id"))
    analyst = res["createdBy"]
    analysis = orch.state("analysis")
    fault = FAULTS[kind]
    new = {**res, "summary": fault.get("summary", res["summary"] + fault.get("summary_suffix", ""))}
    orch.store.put(new, by=analyst)
    if "relation" in fault:
        link = next(l for l in orch.store.latest("evidence_link") if l["resultId"] == res["id"])
        orch.store.put({**link, "relation": fault["relation"]}, by=analyst)
        orch.set_state("analysis", {**analysis, "relation": fault["relation"]})
    orch.store.append("fault.injected", orch.det("director"),
                      {"kind": kind, "note": "Deliberate fault for evaluation; not a real analysis result.",
                       "original_relation": analysis["relation"], "original_summary": res["summary"]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--kinds", default=",".join(FAULTS))
    a = ap.parse_args()
    kinds = [k for k in a.kinds.split(",") if k]
    unknown = set(kinds) - set(FAULTS)
    if unknown:
        ap.error(f"unknown fault kind: {unknown}")
    src = Path(a.project)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rows = []
    for kind in kinds:
        target = branch_after_analysis(src, f"critic-test-{stamp}-{kind}")
        orch = build(target, a.budget, None, autonomy="cap", domain="ml", memory=False)
        inject(orch, kind)
        try:
            orch.run()
        finally:
            orch.tools.close()
        s = orch.store
        crits = [c for c in s.latest("critique") if c["targetId"].startswith("RES")]
        msgs = s.latest("message")
        objections = [m for m in msgs if m["kind"] == "objection"]
        responses = [m for m in msgs if m["kind"] == "response" and m["createdBy"].get("role") == "analyst"]
        w = next((e["payload"] for e in reversed(s.events("writer.output"))), {})
        h = s.get(orch.state("hypothesis_id"))
        reviewed = [e["payload"] for e in s.events("result.reviewed")]
        row = {"kind": kind, "objection_opened": bool(crits), "critiques": [{k: c[k] for k in ("id", "category", "severity", "status", "body")} for c in crits],
               "objection_rounds": len(objections), "analyst_responses": [m["body"] for m in responses],
               "final_relation": orch.state("analysis")["relation"], "hypothesis_status": h["status"],
               "writer_answer": w.get("answer"), "reviews_without_objection": reviewed,
               "rule_violations": s.check_final(), "spent_usd": round(orch.gateway.spent_usd, 4), "project": str(target)}
        rows.append(row)
        print(f"{kind}: objection={row['objection_opened']} rounds={row['objection_rounds']} final relation={row['final_relation']} "
              f"hypothesis={row['hypothesis_status']} answer={row['writer_answer']} ${row['spent_usd']}", flush=True)
    passed = all(r["objection_opened"] != (r["kind"] == "control") for r in rows)
    out = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_project": str(src),
           "passed": passed, "rows": rows}
    path = ROOT / "evals/results" / f"critic-live-{stamp}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "spent_usd": round(sum(r["spent_usd"] for r in rows), 4)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
