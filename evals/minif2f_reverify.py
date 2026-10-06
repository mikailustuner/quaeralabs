"""Re-verifies saved miniF2F proofs in a clean environment, without any model calls.

Usage: uv run python evals/minif2f_reverify.py evals/results/minif2f-test-<date>.json
"""

import json
import sys
from pathlib import Path

from quaera.lean import LeanChecker, statement_of

sys.path.insert(0, str(Path(__file__).parent))
from minif2f_run import HEADER, modernize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    source = {json.loads(l)["name"]: json.loads(l) for l in (ROOT / "evals/data/minif2f-deepseek-v15.jsonl").read_text().splitlines()}
    lean = LeanChecker()
    for row in data["rows"]:
        if not row.get("proof"):
            continue
        approved = statement_of(HEADER + modernize(source[row["name"]]["formal_statement"]).rstrip() + " sorry\n", row["name"])
        rep = lean.check(row["proof"], row["name"], approved, clean=True)
        row["clean_verified"], row["axioms"], row["solved"] = rep.verified, rep.axioms, rep.verified
        row["clean_problems"] = rep.problems + rep.errors[:2]
        print(f"{row['name']}: {'verified' if rep.verified else 'REJECTED ' + str(row['clean_problems'])}", flush=True)
    lean.close()
    solved = sum(r["solved"] for r in data["rows"])
    attempted = sum(1 for r in data["rows"] if r["attempts"] > 0)
    key = next(k for k in data if k.startswith("pass@"))
    data.update({"solved": solved, key: round(solved / attempted, 3), "clean_reverified": solved})
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
