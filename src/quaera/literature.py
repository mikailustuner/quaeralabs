"""Literatür adımı: arama planı → arXiv, OpenAlex ve Mathlib araması → alaka süzme ve yenilik kararı → kaynak doğrulama.

Orkestratör ve değerlendirme seti (evals/literature_run.py) aynı fonksiyonu kullanır.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from . import prompts
from .gateway import ModelError, parse_json


def _json(llm, system: str, prompt: str, n: int) -> tuple[dict, dict]:
    text, actor = llm(system, prompt, n)
    try:
        return parse_json(text), actor
    except (ModelError, ValueError):
        text, actor = llm(system, prompt + "\n\nYour previous answer was not valid JSON. Return ONLY the JSON object.", n)
        return parse_json(text), actor

VERDICTS = {"novel", "partially_done", "already_done", "unknown"}
BASES = {"search", "model_knowledge", "mixed"}


@dataclass
class LiteratureResult:
    summary: str
    verdict: str
    basis: str
    verified: list[dict] = field(default_factory=list)   # [{"ref", "kind", "title"}]
    rejected: list[dict] = field(default_factory=list)   # [{"ref", "reason"}]
    mathlib: list[str] = field(default_factory=list)
    searched: int = 0
    actor: dict | None = None
    tool_errors: list = field(default_factory=list)


def _safe(tools, errors: list, *args, **kw):
    """Dış literatür servisleri (arXiv, OpenAlex) geçici olarak çökebilir: hata araştırmayı durdurmaz, kaydedilir."""
    try:
        return tools.call_json(*args, **kw)
    except Exception as exc:  # ağ/servis hataları: sonuç yokmuş gibi devam
        errors.append({"tool": args[1], "query": kw.get("query"), "error": str(exc)[:200]})
        return []


def run_literature(question: str, scope: str, domain: str, llm: Callable[[str, str, int], tuple[str, dict]],
                   tools) -> LiteratureResult:
    """`llm(system, prompt, max_tokens) -> (metin, aktör)`; `tools` rol izinleri kontrol edilen araç kaydıdır."""
    tool_errors: list = []
    plan, actor = _json(llm, prompts.LITERATURE_PLAN, f"Research question: {question}\nScope: {scope}\nDomain: {domain}", 1500)
    found: dict[str, dict] = {}
    for query in plan.get("arxiv_queries", [])[:3]:
        res = _safe(tools, tool_errors, "literature", "arxiv.search", query=query, max_results=6)
        for r in res if isinstance(res, list) else []:
            found.setdefault(r["id"], {"id": r["id"], "title": r["title"], "year": r["year"], "summary": r["summary"][:350]})
    for query in plan.get("openalex_queries", [])[:2]:
        res = _safe(tools, tool_errors, "literature", "openalex.search", query=query, max_results=6)
        for r in res if isinstance(res, list) else []:
            if r.get("doi"):
                found.setdefault(r["doi"], {"id": r["doi"], "title": r["title"], "year": r["year"]})
    mathlib = []
    if domain == "math":
        for query in plan.get("mathlib_queries", [])[:3]:
            res = _safe(tools, tool_errors, "literature", "mathlib.search", query=query, limit=12)
            mathlib += res if isinstance(res, list) else []
    raw = json.dumps({"papers": list(found.values())[:24], "mathlib": mathlib[:30],
                      "search_errors": [e["tool"] for e in tool_errors]}, ensure_ascii=False)
    out, actor = _json(llm, prompts.LITERATURE_SUMMARY, f"Research question: {question}\n\nSearch results:\n{raw}", 3000)

    verdict = out.get("verdict") if out.get("verdict") in VERDICTS else "unknown"
    basis = out.get("basis") if out.get("basis") in BASES else "search"
    result = LiteratureResult(out.get("summary", ""), verdict, basis, mathlib=out.get("relevant_mathlib", [])[:20],
                              searched=len(found), actor=actor)
    result.tool_errors = tool_errors
    for ref in out.get("cited", []):
        if ref not in found:
            result.rejected.append({"ref": ref, "reason": "not in the search results"})
            continue
        kind = "arxiv" if ref.startswith("arXiv:") else "doi"
        try:
            status = (tools.call("literature", "arxiv.lookup", arxiv_id=ref) if kind == "arxiv"
                      else tools.call("literature", "crossref.lookup", doi=ref))
        except Exception as exc:
            status = f"could not verify ({str(exc)[:80]})"
        if status != "real":
            result.rejected.append({"ref": ref, "reason": status})
        else:
            result.verified.append({"ref": ref, "kind": kind, "title": found[ref]["title"]})
    # Özet serbest metindir: model "cited" listesine koymadığı ya da doğrulamadan geçemeyen kaynakları da yazabilir.
    # Rapora yalnızca doğrulanmış kaynak girsin diye özetteki diğer arXiv kimlikleri ve DOI'ler çıkarılır
    # (dürüstlük denetiminde bulundu: evals/results/audit-*.json).
    result.summary, removed = scrub_unverified(result.summary, {v["ref"] for v in result.verified})
    for ref in removed:
        if ref not in {r["ref"] for r in result.rejected}:
            result.rejected.append({"ref": ref, "reason": "cited in the summary but not verified; removed from the summary"})
    # Doğrulanmış kaynak yoksa "search" dayanağı iddia edilemez.
    if not result.verified and result.basis == "search" and result.verdict in ("already_done", "partially_done", "novel"):
        result.basis = "model_knowledge"
    return result


ARXIV_TEXT = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?", re.I)
DOI_TEXT = re.compile(r"\b10\.\d{4,9}/(?:[^\s\[\]<>\"',;()]|\([^\s()]*\))+", re.I)


def norm_ref(ref: str) -> str:
    """Karşılaştırma için: küçük harf, arXiv önekleri ve arXiv'in DataCite DOI biçimi tek biçime."""
    r = ref.strip().rstrip(".").lower()
    r = re.sub(r"^10\.48550/arxiv\.", "", r)
    r = re.sub(r"^arxiv:\s*", "", r)
    return re.sub(r"v\d+$", "", r) if re.match(r"^\d{4}\.\d{4,5}v\d+$", r) else r


def scrub_unverified(text: str, verified: set[str]) -> tuple[str, list[str]]:
    keep = {norm_ref(v) for v in verified}
    removed: list[str] = []

    def repl(m):
        ref = m.group(0).rstrip(".")
        if norm_ref(ref) in keep:
            return m.group(0)
        removed.append(ref)
        return "[unverified source removed]"
    text = ARXIV_TEXT.sub(repl, text)
    text = DOI_TEXT.sub(repl, text)
    return text, removed
