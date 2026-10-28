"""Literature MCP server: arXiv, OpenAlex, Semantic Scholar and Europe PMC search, arXiv/DOI verification, source text for reviews.

Search results are cached for 7 days in ~/.quaera/cache/literature.db (improvement plan B3), so eval repeats and resumed
runs do not query the services again; QUAERA_CACHE=off turns the cache off. Errors are never cached. Verification
(arxiv_lookup, crossref_lookup) is never cached: a source counts as real only on a fresh answer from arXiv, Crossref or
DataCite. A Semantic Scholar result is only a candidate; its arXiv ID or DOI is verified like any other.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET

from mcp.server.mcpserver import MCPServer

from quaera import cache, citations
from quaera.resources import quaera_home

mcp = MCPServer("literature")
ATOM = {"a": "http://www.w3.org/2005/Atom"}
CACHE_DAYS = 7


@functools.lru_cache(maxsize=1)
def _db() -> sqlite3.Connection:
    path = quaera_home() / "cache" / "literature.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS searches (key TEXT PRIMARY KEY, value TEXT NOT NULL, at REAL NOT NULL)")
    return db


def cached(fn):
    """Caches a search tool's JSON answer by (tool, arguments); answers with an "error" key are not stored."""
    @functools.wraps(fn)
    def wrapper(*args, **kw):
        if not cache.enabled():
            return fn(*args, **kw)
        key = json.dumps([fn.__name__, args, sorted(kw.items())], ensure_ascii=False)
        row = _db().execute("SELECT value FROM searches WHERE key = ? AND at > ?", (key, time.time() - CACHE_DAYS * 86400)).fetchone()
        if row:
            return row[0]
        out = fn(*args, **kw)
        if not (isinstance(json.loads(out), dict) and "error" in json.loads(out)):
            _db().execute("INSERT OR REPLACE INTO searches VALUES (?, ?, ?)", (key, out, time.time()))
            _db().commit()
        return out
    return wrapper


STOP = {"a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "is", "are", "does", "do", "with", "by", "every", "each", "all"}


def arxiv_query(query: str) -> str:
    """Joins keywords with AND; quoted phrases stay a single term.
    (The `all:multi word query` form broke relevance ranking and returned unrelated results.)"""
    phrases = re.findall(r'["\']([^"\']{3,})["\']', query)
    rest = re.sub(r'["\'][^"\']{3,}["\']', " ", query)
    words = [w for w in re.findall(r"[\w-]+", rest.lower()) if w not in STOP and len(w) > 1]
    terms = [f'abs:"{p}"' for p in phrases] + [f"all:{w}" for w in words[:6]]
    return " AND ".join(terms) or "all:" + query


@mcp.tool()
@cached
def arxiv_search(query: str, max_results: int = 8) -> str:
    """Searches arXiv (keywords joined with AND, sorted by relevance); returns the title, ID, year and the start of the abstract."""
    params = urllib.parse.urlencode({"search_query": arxiv_query(query), "max_results": min(max_results, 20),
                                     "sortBy": "relevance"})
    status, body = citations._get(f"https://export.arxiv.org/api/query?{params}")
    if status != 200:
        return json.dumps({"error": f"arXiv HTTP {status}"})
    out = []
    for e in ET.fromstring(body).findall("a:entry", ATOM):
        arxiv_id = re.sub(r"v\d+$", "", e.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1])
        out.append({
            "id": f"arXiv:{arxiv_id}", "title": " ".join(e.findtext("a:title", "", ATOM).split()),
            "year": e.findtext("a:published", "", ATOM)[:4],
            "summary": " ".join(e.findtext("a:summary", "", ATOM).split())[:500],
        })
    return json.dumps(out, ensure_ascii=False)


def abstract_of(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; this puts the words back in order."""
    words = sorted((pos, w) for w, positions in (inverted or {}).items() for pos in positions)
    return " ".join(w for _pos, w in words)


@mcp.tool()
@cached
def openalex_search(query: str, max_results: int = 8) -> str:
    """Searches OpenAlex; returns the title, DOI, year, citation count and the start of the abstract."""
    params = urllib.parse.urlencode({"search": query, "per-page": min(max_results, 20)})
    status, body = citations._get(f"https://api.openalex.org/works?{params}")
    if status != 200:
        return json.dumps({"error": f"OpenAlex HTTP {status}"})
    out = [{"title": w.get("title"), "doi": (w.get("doi") or "").removeprefix("https://doi.org/") or None,
            "year": w.get("publication_year"), "citations": w.get("cited_by_count"),
            "summary": abstract_of(w.get("abstract_inverted_index"))[:500]} for w in json.loads(body).get("results", [])]
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
@cached
def s2_search(query: str, max_results: int = 8) -> str:
    """Searches Semantic Scholar; returns the arXiv ID or DOI, title, year, citation count and the start of the abstract.
    Papers with neither identifier are left out (they could not be verified). S2_API_KEY (optional) raises the rate limit."""
    params = urllib.parse.urlencode({"query": query, "limit": min(max_results, 20),
                                     "fields": "title,year,abstract,citationCount,externalIds"})
    key = os.environ.get("S2_API_KEY")
    status, body = citations._get(f"https://api.semanticscholar.org/graph/v1/paper/search?{params}",
                                  {"x-api-key": key} if key else None)
    if status != 200:
        return json.dumps({"error": f"Semantic Scholar HTTP {status}" + (" (rate limit; set S2_API_KEY)" if status == 429 else "")})
    out = []
    for p in json.loads(body).get("data") or []:
        ids = p.get("externalIds") or {}
        ref = f"arXiv:{ids['ArXiv']}" if ids.get("ArXiv") else ids.get("DOI")
        if ref:
            out.append({"id": ref, "title": p.get("title"), "year": p.get("year"), "citations": p.get("citationCount"),
                        "summary": " ".join((p.get("abstract") or "").split())[:500]})
    return json.dumps(out, ensure_ascii=False)


EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _europepmc(query: str, size: int) -> tuple[int, list[dict]]:
    params = urllib.parse.urlencode({"query": query, "format": "json", "resultType": "core", "pageSize": size})
    status, body = citations._get(f"{EUROPEPMC}/search?{params}")
    return status, (json.loads(body).get("resultList", {}).get("result", []) if status == 200 else [])


@mcp.tool()
@cached
def europepmc_search(query: str, max_results: int = 8) -> str:
    """Searches Europe PMC (PubMed, PMC and preprints: medicine, life and social sciences); returns the title, DOI, year,
    whether open-access full text exists and the start of the abstract. Records without a DOI are left out (not verifiable)."""
    status, results = _europepmc(query, min(max_results, 25))
    if status != 200:
        return json.dumps({"error": f"Europe PMC HTTP {status}"})
    out = [{"title": r.get("title"), "doi": r["doi"], "year": r.get("pubYear"), "openAccess": r.get("isOpenAccess") == "Y",
            "summary": re.sub(r"<[^>]+>", "", r.get("abstractText") or "")[:500]} for r in results if r.get("doi")]
    return json.dumps(out, ensure_ascii=False)


def _plain(xml_text: str) -> str:
    """The readable text of a JATS article, each element on its own line, in reading priority: the abstract, the results
    sections, every table, then the rest of the body. The extracting model sees only the start of a long article, and in
    article order the results were often cut off."""
    root = ET.fromstring(xml_text)
    body = root.find(".//body")
    results = [sec for sec in (body.findall("sec") if body is not None else [])
               if re.search(r"result|finding", f"{sec.get('sec-type', '')} {sec.findtext('title') or ''}", re.I)]
    parts = [root.find(".//abstract"), *results, *root.iter("table-wrap"), body]
    lines = []
    for part in (p for p in parts if p is not None):
        for el in part.iter():
            if el.tag in ("p", "title", "tr", "caption", "label"):
                text = " ".join(" ".join(el.itertext()).split())
                if text:
                    lines.append(text)
    return "\n".join(dict.fromkeys(lines))


@mcp.tool()
def source_text(ref: str) -> str:
    """The text a review extracts data from, for a DOI: the open-access full text from Europe PMC, else the abstract (Europe PMC,
    then OpenAlex). Never cached: the Verifier reads the source again. Returns {"text", "from"} or {"error"}."""
    doi = ref.strip().lower().removeprefix("https://doi.org/")
    status, results = _europepmc(f'DOI:"{doi}"', 1)
    rec = results[0] if results and (results[0].get("doi") or "").lower() == doi else None
    if rec and rec.get("pmcid") and rec.get("isOpenAccess") == "Y":
        code, body = citations._get(f"{EUROPEPMC}/{rec['pmcid']}/fullTextXML")
        if code == 200:
            try:
                text = _plain(body)
            except ET.ParseError:
                text = ""
            if len(text) > 500:
                return json.dumps({"text": text, "from": f"europepmc_fulltext:{rec['pmcid']}"}, ensure_ascii=False)
    if rec and rec.get("abstractText"):
        text = " ".join(re.sub(r"<[^>]+>", " ", rec["abstractText"]).split())
        return json.dumps({"text": f"{rec.get('title', '')}\n{text}", "from": "europepmc_abstract"}, ensure_ascii=False)
    code, body = citations._get(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}")
    if code == 200:
        work = json.loads(body)
        text = abstract_of(work.get("abstract_inverted_index"))
        if text:
            return json.dumps({"text": f"{work.get('title', '')}\n{text}", "from": "openalex_abstract"}, ensure_ascii=False)
    if status != 200 and code != 200:
        return json.dumps({"error": f"Europe PMC HTTP {status}, OpenAlex HTTP {code}"})
    return json.dumps({"error": "no abstract or open-access full text found"})


@mcp.tool()
def arxiv_lookup(arxiv_id: str) -> str:
    """Checks whether an arXiv ID really exists: real / fake / unknown."""
    return citations.check("arxiv", arxiv_id.removeprefix("arXiv:"))


@mcp.tool()
def crossref_lookup(doi: str) -> str:
    """Checks whether a DOI is registered (Crossref, falling back to DataCite): real / fake / unknown."""
    return citations.check("doi", doi)


if __name__ == "__main__":
    mcp.run()
