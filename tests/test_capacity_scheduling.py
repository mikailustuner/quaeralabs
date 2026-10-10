"""Capacity plan S3 (committee on blocking objections) and S4 (scheduler, per-provider limits)."""

import json
import threading
import time

from quaera import prompts
from quaera.gateway import Gateway, ScriptedProvider
from quaera.permissions import Permissions
from quaera.scheduler import Scheduler

from test_orchestrator import Script, make

AGENTS = Permissions.load().agents


class Family(ScriptedProvider):
    model_for = lambda self, profile: "scripted"  # noqa: E731


def blocking_result(second_says_blocking: bool):
    def critic(family):
        def answer(system, prompt):
            if system == prompts.CRITIC_RESULT:
                sev = "blocking" if family == "openai" or second_says_blocking else "low"
                return json.dumps({"concerns": [{"severity": sev, "body": f"The proof is suspicious ({family})."}]})
            return SCRIPT(system, prompt)
        return answer
    return critic


SCRIPT = Script()


def run_with_committee(tmp_path, confirm: bool, name: str):
    global SCRIPT
    SCRIPT = Script()
    orch = make(tmp_path, SCRIPT, name=name)
    crit = blocking_result(confirm)
    orch.gateway.providers = {"anthropic": Family(crit("anthropic"), "anthropic"), "openai": Family(crit("openai"), "openai"),
                              "google": Family(crit("google"), "google")}
    orch.gateway.__post_init__()
    orch.run()
    return orch


def test_blocking_objection_needs_a_second_family(tmp_path):
    orch = run_with_committee(tmp_path, confirm=False, name="unconfirmed")
    crit = [c for c in orch.store.latest("critique") if c.get("category") == "overclaim"]
    assert crit and crit[0]["severity"] == "high" and crit[0]["status"] == "open"     # downgraded, still open
    assert "did not confirm" in crit[0]["body"] and orch.store.events("critique.unconfirmed")
    h = orch.store.get(orch.state("hypothesis_id"))
    assert h["status"] != "supported"                                                   # the honesty gate still holds

    confirmed = run_with_committee(tmp_path, confirm=True, name="confirmed")
    crit = [c for c in confirmed.store.latest("critique") if c.get("category") == "overclaim"]
    assert crit[0]["severity"] == "blocking" and confirmed.store.events("critique.confirmed")


def test_scheduler_bounds_jobs_and_nested_work_does_not_deadlock():
    sched = Scheduler(2)
    now, peak, lock = [0], [0], threading.Lock()

    def job(i):
        def run():
            with lock:
                now[0] += 1
                peak[0] = max(peak[0], now[0])
            time.sleep(0.05)
            inner = sched.run_all([lambda: i * 10, lambda: i * 10 + 1], project="p", label="inner")   # nested: no slot
            with lock:
                now[0] -= 1
            return inner
        return run
    seen = []
    t = threading.Thread(target=lambda: seen.append(sched.run_all([job(i) for i in range(5)], project="p", label="outer")))
    t.start()
    time.sleep(0.02)
    active = sched.active("p")
    t.join(5)
    assert seen and seen[0] == [[0, 1], [10, 11], [20, 21], [30, 31], [40, 41]]
    assert peak[0] <= 2 and any(j["state"] == "queued" for j in active) and sched.active() == []


def test_provider_concurrency_limit_holds_under_parallel_calls():
    now, peak, lock = [0], [0], threading.Lock()

    def slow(system, prompt):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.05)
        with lock:
            now[0] -= 1
        return "ok"
    prov = ScriptedProvider(slow, "local")
    prov.concurrency = 1
    gw = Gateway({"local": prov}, 5.0, AGENTS)
    Scheduler(8).run_all([lambda: gw.call("writer", "s", "p", 10) for _ in range(4)])
    assert peak[0] == 1 and len(prov.calls) == 4
