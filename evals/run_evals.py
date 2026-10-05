"""Değerlendirme setlerini çalıştırır ve sonuçları evals/results/ altına yazar.

Faz 0'da iki çalıştırıcı vardır:
  - citations: kaynak doğrulayıcı (tools/citations.py), canlı arXiv ve Crossref API'leri.
  - critic-majority: her rapora en sık kategoriyle itiraz eden çoğunluk sınıfı
    temel çizgisi. LLM değildir; Eleştirmen ajanının geçmesi gereken ALT SINIRI belirler.

LLM tabanlı ajanların başlangıç değerleri bir API anahtarı ve harcama onayı gerektirir;
bunlar Faz 1'de model gateway kurulduğunda bu araca eklenir.

Kullanım: uv run python evals/run_evals.py [--only citations|critic-majority]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
from quaera import citations  # noqa: E402

SETS = ROOT / "evals" / "sets"
RESULTS = ROOT / "evals" / "results"

def run_critic_majority() -> dict:
    """Çoğunluk sınıfı temel çizgisi: her rapora setteki en sık kategoriyle itiraz eder.

    Vakalardan bağımsız, standart bir alt sınırdır. Bir LLM Eleştirmen bunu
    hem yakalama oranında hem yanlış alarm oranında geçmelidir.
    """
    cases = yaml.safe_load((SETS / "critic-test.yaml").read_text(encoding="utf-8"))["cases"]
    flawed = [c for c in cases if c["kind"] == "flawed"]
    controls = [c for c in cases if c["kind"] == "control"]
    counts: dict[str, int] = {}
    for c in flawed:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    majority = max(sorted(counts), key=counts.get)
    caught = counts[majority]
    return {
        "runner": "critic-majority (LLM değil; alt sınır)",
        "majority_category": majority,
        "category_counts": counts,
        "flawed_cases": len(flawed),
        "caught": caught,
        "catch_rate": round(caught / len(flawed), 3),
        "control_cases": len(controls),
        "false_alarms": len(controls),
        "false_alarm_rate": 1.0,
    }


def run_citations() -> dict:
    cases = yaml.safe_load((SETS / "citations.yaml").read_text(encoding="utf-8"))["cases"]
    rows = [{"id": c["id"], "expected": c["expected"], "got": citations.check(c["kind"], c["ref"])} for c in cases]
    correct = sum(r["got"] == r["expected"] for r in rows)
    fake_accepted = sum(r["expected"] == "fake" and r["got"] == "real" for r in rows)
    return {
        "runner": "tools/citations.py (canlı arXiv + Crossref)",
        "cases": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 3),
        "fake_accepted": fake_accepted,
        "unknown": sum(r["got"] == "unknown" for r in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["citations", "critic-majority"])
    args = parser.parse_args()

    runners = {"critic-majority": run_critic_majority, "citations": run_citations}
    if args.only:
        runners = {args.only: runners[args.only]}

    now = datetime.now(timezone.utc)
    out = {"createdAt": now.isoformat(timespec="seconds"), "results": {k: fn() for k, fn in runners.items()}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"baseline-{now:%Y-%m-%d}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for name, res in out["results"].items():
        summary = {k: v for k, v in res.items() if k not in ("rows",)}
        print(f"{name}: {json.dumps(summary, ensure_ascii=False)}")
    print(f"Yazıldı: {path.relative_to(ROOT)}")

    # Sıfır tolerans kapısı: uydurma bir kaynak kabul edilirse başarısız.
    cit = out["results"].get("citations")
    return 1 if cit and cit["fake_accepted"] else 0


if __name__ == "__main__":
    sys.exit(main())
