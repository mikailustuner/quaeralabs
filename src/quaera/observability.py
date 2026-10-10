"""Observability (capacity plan O1): where a research run spent its budget and what went wrong, per stage and provider.

Computed from the event log only (no extra bookkeeping): for every stage the cost, paid and cached calls, tokens,
escalations, repairs and failure classes; for every provider its calls, errors and error rate; the alerts raised
while it ran (`provider.alert`, budget blocks). The same JSON is shown on the report tab and exported, signed, in the
evidence package as `observability.json`.
"""

from __future__ import annotations

from collections import defaultdict

from .store import Store

FAILURES = {
    "model.error": "model_error", "model.invalid_json": "invalid_json", "model.degraded": "degraded", "budget.blocked": "budget_block",
    "tool.error": "tool_error", "run.crashed": "crash",
}


def metrics(store: Store) -> dict:
    stages_done = [(e["seq"], e["payload"]["stage"]) for e in store.events("stage.done")]
    at = (store.meta("branch") or {}).get("atSeq") or 0

    def stage_of(seq: int) -> str:
        for s, name in stages_done:
            if seq <= s:
                return name
        return "running" if not store.events("report.written") else "after report"

    per_stage: dict[str, dict] = {}
    per_provider: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0, "usd": 0.0, "tokens": 0})
    order = [name for _, name in stages_done]
    totals = {"usd": 0.0, "calls": 0, "cached": 0, "tokens": 0, "escalations": 0, "repairs": 0}

    def bucket(seq: int) -> dict:
        name = stage_of(seq)
        if name not in per_stage:
            per_stage[name] = {"stage": name, "usd": 0.0, "calls": 0, "cached": 0, "tokens": 0, "escalations": 0, "repairs": 0, "failures": {}}
            if name not in order:
                order.append(name)
        return per_stage[name]

    for e in store.events():
        if e["seq"] <= at:                 # a branch's copied history belongs to its parent
            continue
        k, p = e["kind"], e["payload"]
        if k == "model.call":
            b = bucket(e["seq"])
            key = "cached" if p.get("cached") else "calls"
            b[key] += 1
            totals[key] += 1
            usd, tok = float(p.get("costUsd") or 0), int(p.get("inputTokens") or 0) + int(p.get("outputTokens") or 0)
            b["usd"] += usd
            b["tokens"] += tok
            totals["usd"] += usd
            totals["tokens"] += tok
            prov = per_provider[p.get("provider") or p.get("family") or "?"]
            prov["calls"] += 1
            prov["usd"] += usd
            prov["tokens"] += tok
        elif k == "model.escalated":
            bucket(e["seq"])["escalations"] += 1
            totals["escalations"] += 1
        elif k == "model.repaired":
            bucket(e["seq"])["repairs"] += 1
            totals["repairs"] += 1
        if k in FAILURES:
            b = bucket(e["seq"])
            cls = FAILURES[k]
            b["failures"][cls] = b["failures"].get(cls, 0) + 1
            if k == "model.error":
                per_provider[p.get("provider") or "?"]["errors"] += 1
    for b in per_stage.values():
        b["usd"] = round(b["usd"], 6)
    providers = []
    for key, v in per_provider.items():
        n = v["calls"] + v["errors"]
        providers.append({"provider": key, **v, "usd": round(v["usd"], 6), "errorRate": round(v["errors"] / n, 3) if n else None})
    alerts = [{"at": e["at"], **e["payload"]} for e in store.events("provider.alert")]
    alerts += [{"at": e["at"], "kind": "budget", "message": e["payload"].get("dimension") or "a call was blocked by the budget cap"}
               for e in store.events("budget.blocked")][-3:]
    return {"totals": {**totals, "usd": round(totals["usd"], 6)}, "stages": [per_stage[n] for n in order if n in per_stage],
            "providers": sorted(providers, key=lambda x: -x["calls"]), "alerts": alerts}
