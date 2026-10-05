"""Eleştirmen itiraz döngüsünün canlı sınaması (hata enjeksiyonu).

Tamamlanmış gerçek bir ML projesi, analiz aşamasının hemen sonrasına dallandırılır; Analist'in sonucuna bilinçli
bir hata eklenir ve olay kaydına `fault.injected` olarak açıkça yazılır. Ardından gerçek modellerle eleştiri,
doğrulama, sonuç ve rapor aşamaları çalışır.

Beklenti (koşudan önce):
  flip              ilişki tersine çevrildi (çelişen sonuç "destekliyor" diye yazıldı)  → Eleştirmen itiraz açmalı
  overclaim         sonuç tüm veri setlerine/modellere genellendi                       → itiraz açmalı
  posthoc           ön kayıtlı eşik sonradan gevşetilip hipotez desteklendi (Faz 4)      → itiraz açmalı
  fabricated_number veride olmayan bir ölçüm (0.81) yoruma eklendi (Faz 4)              → itiraz açmalı
  control           hiçbir şey değiştirilmedi                                          → itiraz AÇILMAMALI
Kapı: tüm hatalı durumlarda itiraz açılması ve kontrolde açılmaması.
Seçim: --kinds flip,overclaim (varsayılan: hepsi). Kaynak proje doğru cevabı "hayır" olan bir ML projesi olmalı.

Kullanım: uv run python evals/critic_live_check.py <tamamlanmış-proje-dizini> --budget 1.5
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
             "summary": "Sonuçlar hipotezi açıkça destekliyor; ölçülen değerler ön kayıtlı kriteri rahatça karşılıyor."},
    "overclaim": {"summary_suffix": " Bu bulgu tüm veri setleri, tüm model aileleri ve tüm ölçekler için genel olarak geçerlidir; "
                                    "yöntem her durumda aynı sonucu verir."},
    # Faz 4 genişletmesi: ön kayıt ihlali ve uydurma sayı
    "posthoc": {"relation": "supports",
                "summary": "Ön kayıttaki 0.75 eşiği bu veri için aşırı katıydı; daha makul olan 0.55 eşiği esas alındığında "
                           "ortalama doğruluk (≈0.59) eşiği geçiyor ve hipotez destekleniyor."},
    "fabricated_number": {"summary_suffix": " Ayrıca seed 2'de lojistik regresyon 0.81 test doğruluğuna ulaştı; bu, "
                                            "doğru ayarla eşiğin aşılabileceğini gösteriyor."},
    "control": {},
}


def branch_after_analysis(src_dir: Path, name: str) -> Path:
    src = Store(src_dir / "quaera.db")
    seq = next(e["seq"] for e in src.events("stage.done") if e["payload"]["stage"] == "analysis")
    target = HOME / name
    if target.exists():
        shutil.rmtree(target)
    src.branch(target / "quaera.db", seq)
    shutil.copytree(src_dir / "work", target / "work")   # Doğrulayıcı'nın yeniden çalıştıracağı git deposu
    return target


def inject(orch, kind: str) -> None:
    if kind == "control":
        orch.store.append("fault.injected", orch.det("director"), {"kind": "control", "note": "değişiklik yok"})
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
                      {"kind": kind, "note": "Değerlendirme amaçlı bilinçli hata; gerçek bir analiz sonucu değildir.",
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
        ap.error(f"bilinmeyen hata türü: {unknown}")
    src = Path(a.project)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rows = []
    for kind in kinds:
        target = branch_after_analysis(src, f"itiraz-{stamp}-{kind}")
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
        print(f"{kind}: itiraz={row['objection_opened']} tur={row['objection_rounds']} son ilişki={row['final_relation']} "
              f"hipotez={row['hypothesis_status']} cevap={row['writer_answer']} ${row['spent_usd']}", flush=True)
    passed = all(r["objection_opened"] != (r["kind"] == "control") for r in rows)
    out = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_project": str(src),
           "passed": passed, "rows": rows}
    path = ROOT / "evals/results" / f"critic-live-{stamp}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "spent_usd": round(sum(r["spent_usd"] for r in rows), 4)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
