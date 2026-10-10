"""Completion cache (capacity plan C1): an identical model call is never paid twice.

Key = sha256(route, model, effort, system, prompt, output limit, sample index). Branches re-run the same literature and
landscape calls, resumes repeat the interrupted call, evals rerun tasks: all of these hit the cache. A hit is recorded
as a `model.call` with `cached: true` and zero cost; it never reserves budget.

Best-of-N samples (K5) carry their sample index in the key, so N samples stay N different answers. Evals and
measurements that need the true cost turn the cache off with QUAERA_CACHE=off. The data never leaves the machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path


def enabled() -> bool:
    return os.environ.get("QUAERA_CACHE", "on").strip().lower() not in ("off", "0", "false", "no")


class CompletionCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS completions (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                            "hits INTEGER NOT NULL DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP)")
            self.db.commit()

    @staticmethod
    def key(route: str, model: str, effort: str | None, system: str, prompt: str, max_tokens: int,
            sample: int | None = None) -> str:
        raw = json.dumps([route, model, effort, system, prompt, max_tokens, sample], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM completions WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE completions SET hits = hits + 1 WHERE key = ?", (key,))
            self.db.commit()
        return json.loads(row[0])

    def put(self, key: str, completion) -> None:
        value = {k: v for k, v in asdict(completion).items() if k != "cached"}
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO completions (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
            self.db.commit()

    def stats(self) -> dict:
        with self.lock:
            n, hits = self.db.execute("SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM completions").fetchone()
        return {"entries": n, "hits": hits}

    def clear(self) -> int:
        with self.lock:
            n = self.db.execute("DELETE FROM completions").rowcount
            self.db.commit()
        return n

    def close(self) -> None:
        with self.lock:
            self.db.close()
