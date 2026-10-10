"""Job scheduler (capacity plan S4): one place for every parallel job of the lab, with a global concurrency bound.

Parallel work (two Hypothesis lanes, cross-reviews, concurrent lemma attacks, parallel branches of a tree search) is
submitted as jobs. Top-level jobs take one of QUAERA_MAX_JOBS slots, shared by all projects on this machine, so
several running projects queue instead of overloading the machine; a job started from inside another job runs
without taking a slot (no deadlock when parallel work nests). Per-provider limits stay in the gateway (semaphores
from the registry's `limits.concurrent`), the budget cap stays in the gateway's reservations.

`active()` lists the running and queued jobs (the web UI shows them on the lab screen).
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


class Scheduler:
    def __init__(self, max_jobs: int):
        self.max_jobs = max(1, max_jobs)
        self.slots = threading.BoundedSemaphore(self.max_jobs)
        self.lock = threading.Lock()
        self.jobs: dict[int, dict] = {}
        self.ids = itertools.count(1)
        self.local = threading.local()

    def _set(self, jid: int, **kw) -> None:
        with self.lock:
            if jid in self.jobs:
                self.jobs[jid].update(kw)

    def run_all(self, fns: list[Callable], *, project: str = "", label: str = "job") -> list:
        """Runs the callables concurrently and returns their results in order (exceptions propagate like before)."""
        nested = bool(getattr(self.local, "depth", 0))

        def wrap(fn: Callable, i: int):
            with self.lock:
                jid = next(self.ids)
                self.jobs[jid] = {"id": jid, "project": project, "label": f"{label} {i + 1}/{len(fns)}" if len(fns) > 1 else label,
                                  "state": "queued", "queuedAt": time.time(), "nested": nested}

            def run():
                if not nested:
                    self.slots.acquire()
                self.local.depth = getattr(self.local, "depth", 0) + 1
                self._set(jid, state="running", startedAt=time.time())
                try:
                    return fn()
                finally:
                    self.local.depth -= 1
                    with self.lock:
                        self.jobs.pop(jid, None)
                    if not nested:
                        self.slots.release()
            return run

        if not fns:
            return []
        runs = [wrap(fn, i) for i, fn in enumerate(fns)]
        with ThreadPoolExecutor(max_workers=len(runs)) as pool:
            futures = [pool.submit(r) for r in runs]
            return [f.result() for f in futures]

    def active(self, project: str | None = None) -> list[dict]:
        with self.lock:
            jobs = [dict(j) for j in self.jobs.values() if project is None or j["project"] == project]
        return sorted(jobs, key=lambda j: j["id"])


SCHEDULER = Scheduler(int(os.environ.get("QUAERA_MAX_JOBS", "8")))
