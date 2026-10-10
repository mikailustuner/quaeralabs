"""Model capability profiles (capacity plan S2, with the scoreboard of M2): the system adapts to the model it has.

Per model the lab records, from its own runs (no extra calls): calls, valid / invalid JSON answers and Lean compile
outcomes of proof candidates. From these:

    json_reliability = valid / (valid + invalid)          compile_rate = compiled / (compiled + failed)

and the proof search adapts (all rules in `adapt`, logged per stage as `model.adapted`):
- a model whose candidates rarely compile proves step by step in the REPL first (K2) and writes more parallel
  candidates (K5) with shorter answers,
- a model that writes reliable Lean keeps whole-proof attempts first,
- below a few observations nothing changes (defaults).

The data stays in ~/.quaera/stats.db on this machine; `quaera models` prints the scoreboard.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIN_OBSERVATIONS = 5
DEFAULTS = {"samples": 2, "interactive_first": False, "max_tokens": 16000, "depth": 2}


class ModelStats:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS stats (model TEXT NOT NULL, purpose TEXT NOT NULL, metric TEXT NOT NULL, "
                            "n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (model, purpose, metric))")
            self.db.commit()

    def bump(self, model: str, purpose: str, metric: str, n: int = 1) -> None:
        with self.lock:
            self.db.execute("INSERT INTO stats VALUES (?, ?, ?, ?) ON CONFLICT(model, purpose, metric) DO UPDATE SET n = n + ?",
                            (model, purpose, metric, n, n))
            self.db.commit()

    def _sum(self, model: str, metric: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT COALESCE(SUM(n), 0) FROM stats WHERE model = ? AND metric = ?", (model, metric)).fetchone()
        return int(row[0])

    def profile(self, model: str) -> dict:
        valid, invalid = self._sum(model, "json_ok"), self._sum(model, "json_bad")
        ok, bad = self._sum(model, "compile_ok"), self._sum(model, "compile_bad")
        return {"model": model, "calls": self._sum(model, "call"),
                "json_reliability": round(valid / (valid + invalid), 3) if valid + invalid else None, "json_n": valid + invalid,
                "compile_rate": round(ok / (ok + bad), 3) if ok + bad else None, "compile_n": ok + bad,
                "verified": self._sum(model, "verified")}

    def board(self) -> list[dict]:
        with self.lock:
            models = [r[0] for r in self.db.execute("SELECT DISTINCT model FROM stats ORDER BY model")]
        return [self.profile(m) for m in models]

    def close(self) -> None:
        with self.lock:
            self.db.close()


def adapt(profile: dict | None) -> tuple[dict, list[str]]:
    """Search settings for a model profile and the reasons for every change from the defaults."""
    out, why = dict(DEFAULTS), []
    if not profile:
        return out, why
    rate, n = profile.get("compile_rate"), profile.get("compile_n", 0)
    if rate is not None and n >= MIN_OBSERVATIONS:
        if rate < 0.2:
            out.update(interactive_first=True, samples=4, max_tokens=8000, depth=3)
            why.append(f"compile rate {rate:.0%} over {n} candidates: step-by-step REPL first, 4 short candidates, deeper decomposition")
        elif rate < 0.45:
            out.update(samples=3)
            why.append(f"compile rate {rate:.0%}: 3 parallel candidates")
    rel, jn = profile.get("json_reliability"), profile.get("json_n", 0)
    if rel is not None and jn >= MIN_OBSERVATIONS and rel < 0.7:
        why.append(f"JSON reliability {rel:.0%}: structured answers go through the repair pass (R1)")
    return out, why
