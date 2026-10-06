"""Literature MCP server: arXiv and OpenAlex search, arXiv/DOI verification."""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from mcp.server.mcpserver import MCPServer

from quaera import citations

mcp = MCPServer("literature")
ATOM = {"a": "http://www.w3.org/2005/Atom"}


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


@mcp.tool()
def openalex_search(query: str, max_results: int = 8) -> str:
    """Searches OpenAlex; returns the title, DOI and year."""
    params = urllib.parse.urlencode({"search": query, "per-page": min(max_results, 20)})
    status, body = citations._get(f"https://api.openalex.org/works?{params}")
    if status != 200:
        return json.dumps({"error": f"OpenAlex HTTP {status}"})
    out = [{"title": w.get("title"), "doi": (w.get("doi") or "").removeprefix("https://doi.org/") or None,
            "year": w.get("publication_year")} for w in json.loads(body).get("results", [])]
    return json.dumps(out, ensure_ascii=False)


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
