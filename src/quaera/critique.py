"""`quaera critique DIR`: the Critic on an experiment that was not run by QuaeraLabs (improvement plan E1).

The directory's text files (code, results, a pre-registration, a README or report) are put together into one experiment
report, each line numbered under its file name, so an objection can point at `file:line`. The Critic reviews it with the
same prompt that the critic test measures (CRITIC_EXPERIMENT; evals/critic_v2_run.py calls `review` too, so the eval and
the product take the same path). A `blocking` objection is put to a second model family; if that family does not
confirm it, it stays an objection but becomes `high` (one model cannot block alone, as in a research run).

Nothing in the directory is executed. Large files are cut, binary files and environments are skipped, and the report
lists what was left out.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import prompts
from .gateway import ModelError, parse_json

TEXT = {".py", ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".r", ".jl", ".sh", ".tex", ".log"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".ipynb_checkpoints", "wandb", "mlruns", "data", "checkpoints"}
FILE_CHARS = 12000          # per file
TABLE_LINES = 60            # first lines of a CSV/TSV/log
TOTAL_CHARS = 80000         # the whole report
MAX_OUTPUT = 1500           # as in the critic test
CATEGORIES = ("leakage", "statistics", "confound", "methodology", "implementation", "overclaim", "preregistration_violation", "citation")


@dataclass
class Critique:
    flawed: bool | None
    categories: list[str]
    severity: str | None
    evidence: str
    summary: str
    files: list[str] = field(default_factory=list)       # what the Critic read
    skipped: list[str] = field(default_factory=list)     # what it did not read, and why
    models: list[str] = field(default_factory=list)
    note: str = ""


def _priority(p: Path, prereg: Path | None, results: Path | None) -> tuple:
    # pre-registration and results first (the prompt reads numbers and plans before interpretations), then code, then the rest
    if p == prereg:
        return (0, str(p))
    if p == results:
        return (1, str(p))
    return ({".json": 2, ".csv": 2, ".tsv": 2, ".py": 3}.get(p.suffix.lower(), 4), str(p))


def collect(root: Path, prereg: Path | None = None, results: Path | None = None) -> tuple[str, list[str], list[str]]:
    """The experiment report built from a directory: (text, files read, files skipped with the reason)."""
    root = root.resolve()
    prereg, results = (prereg.resolve() if prereg else None), (results.resolve() if results else None)
    paths = [p for p in root.rglob("*") if p.is_file() and not any(part in SKIP_DIRS or part.startswith(".")
                                                                     for part in p.relative_to(root).parts[:-1])]
    paths += [p for p in (prereg, results) if p and p not in paths]
    parts, read, skipped, total = [], [], [], 0
    for p in sorted(paths, key=lambda p: _priority(p, prereg, results)):
        rel = str(p.relative_to(root)) if root in p.parents else p.name
        if p.suffix.lower() not in TEXT or p.name.startswith("."):
            skipped.append(f"{rel} (not a text file)")
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            skipped.append(f"{rel} (not readable as UTF-8)")
            continue
        cut = ""
        if p.suffix.lower() in (".csv", ".tsv", ".log") and len(lines) > TABLE_LINES:
            cut, lines = f"[first {TABLE_LINES} of {len(lines)} lines]", lines[:TABLE_LINES]
        body = "\n".join(f"{i:>4}| {l}" for i, l in enumerate(lines, 1))
        if len(body) > FILE_CHARS:
            body, cut = body[:FILE_CHARS], cut or f"[cut at {FILE_CHARS} characters]"
        role = " (pre-registration)" if p == prereg else " (results)" if p == results else ""
        block = f"=== {rel}{role} {cut}".rstrip() + f"\n{body}\n"
        if total + len(block) > TOTAL_CHARS:
            skipped.append(f"{rel} (the report reached {TOTAL_CHARS} characters)")
            continue
        parts.append(block)
        read.append(rel)
        total += len(block)
    return "\n".join(parts), read, skipped


def review(report: str, ask: Callable[..., tuple[str, str]], second: Callable[..., tuple[str, str]] | None = None) -> dict:
    """One Critic verdict on an experiment report. `ask(system, prompt, max_tokens) -> (text, model)`; `second` is the
    same call on another model family, used only to confirm a `blocking` severity."""
    prompt = f"Experiment report:\n{report}"
    try:
        text, model = ask(prompts.CRITIC_EXPERIMENT, prompt, MAX_OUTPUT)
        out = parse_json(text)
    except (ModelError, ValueError) as exc:
        return {"flawed": None, "categories": [], "severity": None, "evidence": "", "summary": f"error: {exc}", "models": []}
    out = {"flawed": bool(out.get("flawed")), "categories": [c for c in out.get("categories") or [] if c in CATEGORIES],
           "severity": out.get("severity"), "evidence": out.get("evidence") or "", "summary": out.get("summary") or "",
           "models": [model]}
    if out["severity"] == "blocking" and second is not None:
        try:
            text2, model2 = second(prompts.CRITIC_EXPERIMENT, prompt, MAX_OUTPUT)
            confirmed = parse_json(text2).get("severity") == "blocking"
        except (ModelError, ValueError):
            return out
        out["models"].append(model2)
        if not confirmed:
            out["severity"] = "high"
            out["note"] = f"rated blocking by {model}; {model2} did not confirm that severity"
    return out


def critique(root: Path, ask, second=None, prereg: Path | None = None, results: Path | None = None) -> Critique:
    report, read, skipped = collect(root, prereg, results)
    if not read:
        return Critique(None, [], None, "", "no readable text files in the directory", read, skipped)
    out = review(report, ask, second)
    return Critique(out["flawed"], out["categories"], out["severity"], out["evidence"], out["summary"], read, skipped,
                    out.get("models", []), out.get("note", ""))


def markdown(c: Critique, root: Path) -> str:
    verdict = ("no verdict (the model call failed)" if c.flawed is None else
               f"**objection** · severity `{c.severity}` · {', '.join(c.categories) or 'uncategorized'}" if c.flawed else
               "**no objection**")
    lines = [f"# Critique of `{root.name}`", "", f"Verdict: {verdict}", "", c.summary]
    if c.evidence:
        lines += ["", "## Evidence", "", c.evidence]
    if c.note:
        lines += ["", f"_{c.note}_"]
    lines += ["", "## What was read", "", *(f"- {f}" for f in c.files)]
    if c.skipped:
        lines += ["", "Not read:", *(f"- {s}" for s in c.skipped)]
    lines += ["", f"_Critic: {', '.join(c.models) or '—'} · prompt measured by evals/critic_v2_run.py · nothing was executed._"]
    return "\n".join(lines) + "\n"


def as_dict(c: Critique) -> dict:
    return asdict(c)
