"""Lean MCP server: the lean_compile and mathlib_search tools."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict

from mcp.server.mcpserver import MCPServer

from quaera.lean import DEFAULT_WORKSPACE, LeanChecker

mcp = MCPServer("lean")
_checker: LeanChecker | None = None


def checker() -> LeanChecker:
    global _checker
    if _checker is None:
        _checker = LeanChecker()
    return _checker


@mcp.tool()
def lean_compile(source: str, theorem: str | None = None, approved_statement: str | None = None, clean: bool = False) -> str:
    """Checks Lean 4 source inside the sandbox (clean=True: one-shot compilation from scratch); returns a report on compilation, axioms and the statement."""
    return json.dumps(asdict(checker().check(source, theorem, approved_statement, clean)), ensure_ascii=False)


@mcp.tool()
def mathlib_search(query: str, limit: int = 20) -> str:
    """Searches the Mathlib sources for theorem/lemma/def lines that contain every word of the query."""
    root = DEFAULT_WORKSPACE / ".lake" / "packages" / "mathlib" / "Mathlib"
    words = [w for w in re.split(r"\s+", query.strip()) if w][:6]
    if not words or not root.exists():
        return json.dumps([])
    proc = subprocess.run(
        ["grep", "-rnE", r"^(theorem|lemma|def|abbrev) [^ ]*" + re.escape(words[0]), str(root), "--include=*.lean"],
        capture_output=True, text=True, timeout=60,
    )
    hits = []
    for line in proc.stdout.splitlines():
        if all(w.lower() in line.lower() for w in words[1:]):
            path, lineno, text = line.split(":", 2)
            hits.append({"file": path.split("/Mathlib/", 1)[-1], "line": int(lineno), "text": text.strip()[:240]})
            if len(hits) >= limit:
                break
    return json.dumps(hits, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
