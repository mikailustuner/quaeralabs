"""Citation checker: queries the official APIs for arXiv IDs and DOIs.

Network calls have timeouts; an unreachable source returns 'unknown' and is
never counted as 'real'.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "QuaeraLabs-citation-check/0.1 (+https://github.com/quaeralabs)"
TIMEOUT = 15
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def check_arxiv(ref: str) -> str:
    if not ARXIV_ID.match(ref):
        return "fake"
    status, body = _get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ref}))
    if status != 200:
        return "unknown"
    # For a nonexistent ID, arXiv returns an error entry.
    if "<entry>" not in body or "<title>Error</title>" in body or "/api/errors" in body:
        return "fake"
    return "real"


def check_doi(ref: str) -> str:
    # arXiv's own DOIs (10.48550/arXiv.XXXX) are registered with DataCite, not Crossref: ask arXiv.
    m = re.match(r"^10\.48550/arxiv\.(\d{4}\.\d{4,5}(v\d+)?)$", ref, re.I)
    if m:
        return check_arxiv(m.group(1))
    status, _ = _get("https://api.crossref.org/works/" + urllib.parse.quote(ref, safe="/"))
    if status == 200:
        return "real"
    if status != 404:
        return "unknown"
    # If Crossref doesn't have it, ask DataCite too (datasets, preprints); missing from both counts as fake.
    status, _ = _get("https://api.datacite.org/dois/" + urllib.parse.quote(ref, safe="/"))
    if status == 200:
        return "real"
    return "fake" if status == 404 else "unknown"


def check(kind: str, ref: str) -> str:
    try:
        return check_arxiv(ref) if kind == "arxiv" else check_doi(ref)
    except (urllib.error.URLError, TimeoutError):
        return "unknown"
