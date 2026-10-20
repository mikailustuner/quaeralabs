"""Literature step: search plan → arXiv, OpenAlex, Semantic Scholar and Mathlib search → relevance filtering and novelty
verdict → citation check. If nothing relevant was found, the queries are rewritten once (improvement plan B3).

The orchestrator and the evaluation set (evals/literature_run.py) use the same function.
"""

from __future__ import annotations

import json
import os
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
MAX_QUERIES = 8          # distinct paper-search queries per literature step, both passes together
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
    relevant: int = 0                                    # search results the model judged relevant
    reason: str = ""                                     # why the code changed the model's verdict ("" = kept)
    queries: list[str] = field(default_factory=list)     # paper-search queries run, in order
    replanned: bool = False                              # the first queries found nothing relevant and were rewritten


def _safe(tools, errors: list, *args, **kw):
    """External literature services (arXiv, OpenAlex) can go down temporarily: an error does not stop the research, it is recorded."""
    try:
        res = tools.call_json(*args, **kw)
    except Exception as exc:  # network/service errors: continue as if there were no results
        errors.append({"tool": args[1], "query": kw.get("query"), "error": str(exc)[:200]})
        return []
    if isinstance(res, dict) and "error" in res:     # the service answered with an error (HTTP status, rate limit)
        errors.append({"tool": args[1], "query": kw.get("query"), "error": str(res["error"])[:200]})
        return []
    return res


def _search(tools, errors: list, found: dict, arxiv: list[str], scholar: list[str]) -> None:
    """arXiv queries on arXiv; the other queries on OpenAlex and, with an S2_API_KEY, Semantic Scholar (without a key
    its shared rate limit refuses most requests)."""
    for query in arxiv:
        for r in _safe(tools, errors, "literature", "arxiv.search", query=query, max_results=6):
            found.setdefault(r["id"], {"id": r["id"], "title": r["title"], "year": r["year"], "summary": r["summary"][:350]})
    for query in scholar:
        for r in _safe(tools, errors, "literature", "openalex.search", query=query, max_results=6):
            if r.get("doi"):
                found.setdefault(r["doi"], {"id": r["doi"], "title": r["title"], "year": r["year"],
                                            **({"summary": r["summary"][:350]} if r.get("summary") else {})})
        if os.environ.get("S2_API_KEY"):
            for r in _safe(tools, errors, "literature", "s2.search", query=query, max_results=6):
                found.setdefault(r["id"], {"id": r["id"], "title": r["title"], "year": r["year"], "summary": r["summary"][:350]})


def _queries(plan: dict, key: str, n: int, used: list[str]) -> list[str]:
    out = [q for q in plan.get(key, []) if isinstance(q, str) and q.strip() and q not in used][:n]
    used += out
    return out


def run_literature(question: str, scope: str, domain: str, llm: Callable[[str, str, int], tuple[str, dict]],
                   tools) -> LiteratureResult:
    """`llm(system, prompt, max_tokens) -> (text, actor)`; `tools` is the tool registry that enforces role permissions."""
    tool_errors: list = []
    plan, actor = _json(llm, prompts.LITERATURE_PLAN, f"Research question: {question}\nScope: {scope}\nDomain: {domain}", 1500)
    found: dict[str, dict] = {}
    used: list[str] = []
    _search(tools, tool_errors, found, _queries(plan, "arxiv_queries", 3, used), _queries(plan, "openalex_queries", 2, used))
    mathlib = []
    if domain == "math":
        for query in plan.get("mathlib_queries", [])[:3]:
            res = _safe(tools, tool_errors, "literature", "mathlib.search", query=query, limit=12)
            mathlib += res if isinstance(res, list) else []

    def summarize():
        raw = json.dumps({"papers": list(found.values())[:30], "mathlib": mathlib[:30],
                          "search_errors": sorted({e["tool"] for e in tool_errors})}, ensure_ascii=False)
        return _json(llm, prompts.LITERATURE_SUMMARY, f"Research question: {question}\n\nSearch results:\n{raw}", 3000)

    out, actor = summarize()
    first_relevant = {r for r in [*out.get("relevant", []), *out.get("cited", [])] if isinstance(r, str) and r in found}
    replanned = False
    if not first_relevant and not (tool_errors and not found) and len(used) < MAX_QUERIES:
        # Second pass: nothing relevant (or nothing at all). The model sees what the first queries returned and rewrites them.
        seen = "; ".join(p["title"] for p in list(found.values())[:12]) or "nothing"
        again, _a = _json(llm, prompts.LITERATURE_REPLAN, f"Research question: {question}\nScope: {scope}\nDomain: {domain}\n"
                          f"Queries used: {json.dumps(used, ensure_ascii=False)}\nThey returned (titles): {seen}", 1000)
        room = MAX_QUERIES - len(used)
        arxiv = _queries(again, "arxiv_queries", min(2, room), used)
        scholar = _queries(again, "openalex_queries", min(1, MAX_QUERIES - len(used)), used)
        if arxiv or scholar:
            before = len(found)
            _search(tools, tool_errors, found, arxiv, scholar)
            replanned = True
            if len(found) > before:
                out, actor = summarize()

    verdict = out.get("verdict") if out.get("verdict") in VERDICTS else "unknown"
    basis = out.get("basis") if out.get("basis") in BASES else "search"
    result = LiteratureResult(out.get("summary", ""), verdict, basis, mathlib=out.get("relevant_mathlib", [])[:20],
                              searched=len(found), actor=actor, queries=used, replanned=replanned)
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
    # The summary is free text: the model may also mention sources it left out of "cited" or that failed verification.
    # So that only verified sources reach the report, other arXiv IDs and DOIs in the summary are removed
    # (found in the honesty audit: evals/results/audit-*.json).
    result.summary, removed = scrub_unverified(result.summary, {v["ref"] for v in result.verified})
    for ref in removed:
        if ref not in {r["ref"] for r in result.rejected}:
            result.rejected.append({"ref": ref, "reason": "cited in the summary but not verified; removed from the summary"})
    result.relevant = len({r for r in out.get("relevant", []) if isinstance(r, str) and r in found})
    result.verdict, result.basis, result.reason = decide(result.verdict, result.basis, len(result.verified),
                                                         searched=len(found), service_down=bool(tool_errors) and not found)
    return result


def decide(verdict: str, basis: str, verified: int, *, searched: int, service_down: bool) -> tuple[str, str, str]:
    """The model proposes a verdict; the code decides what may be claimed (improvement plan B1).

    In the 2026-10-04 run on "is n³ − n divisible by 6" the search returned only unrelated papers and the model answered
    `novel`, although the literature eval accepted only `already_done` for the same question: the prompt rule alone was
    not enough. Claiming that something is new, or partly done, needs a verified relevant source; a textbook fact the
    model is sure about stays `already_done`, marked as unverified model knowledge."""
    if verdict in ("novel", "partially_done") and verified == 0:
        why = ("the literature services could not be reached" if service_down else
               f"none of the {searched} search results was a verified relevant source" if searched else "the search found nothing")
        return "unknown", "search", f"`{verdict}` needs a verified source; {why}"
    if verified == 0 and verdict == "already_done" and basis in ("search", "mixed"):
        return verdict, "model_knowledge", ""      # without a verified source a "search" basis cannot be claimed
    return verdict, basis, ""


ARXIV_TEXT = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?", re.I)
DOI_TEXT = re.compile(r"\b10\.\d{4,9}/(?:[^\s\[\]<>\"',;()]|\([^\s()]*\))+", re.I)


def norm_ref(ref: str) -> str:
    """For comparison: lowercase, and arXiv prefixes and arXiv's DataCite DOI form normalized to one form."""
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
