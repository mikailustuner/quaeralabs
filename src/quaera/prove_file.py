"""Lean project mode (improvement plan C3): close the `sorry` of one declaration in the user's own Lean project.

    quaera prove Path/To/File.lean --decl my_lemma --budget 2 [--write]

The proof search is the same as in a research run (prover.prove_search); what differs is where it happens:
- Lean runs against the user's project (its toolchain, its dependencies, its compiled modules), read-only, in the sandbox.
- Everything before the declaration is the user's and stays byte for byte as it is. From a model's answer only two things
  are taken: new helper lemmas placed directly above the declaration, and the declaration itself with its proof. The guards
  that stop a model from faking a verdict (no new axiom, no output-producing or syntax-changing command) apply to that part.
- The statement must stay exactly as written; the axiom check covers the declaration and everything it depends on.
- A found proof is checked once more as a one-shot build of the whole new file (helpers + proof + the rest of the file).
  Only then is it shown as a diff; the file is changed only with --write and the user's confirmation.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .lean import ALLOWED_IMPORT_ROOTS, DEFAULT_WORKSPACE, LeanChecker, statement_of
from .prover import DECL_START, Interactive, decl_span

LAKEFILES = ("lakefile.toml", "lakefile.lean")
VIEW_LIMIT = 12000                  # characters of the file before the declaration shown to the model in full
VIEW_TAIL_LINES = 150               # for a longer file: imports, scope commands and the last lines before the declaration
LEAD = re.compile(r"(?:/--(?:(?!-/).)*-/\s*)?(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable)\s+)*$", re.S)
BLOCK_NAME = re.compile(r"(?:/--(?:(?!-/).)*-/\s*)?(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable)\s+)*"
                        r"(?:theorem|lemma)\s+([^\s(:{\[]+)", re.S)
DEFINED = re.compile(r"\b(?:theorem|lemma|def|abbrev|instance|structure|inductive|class)\s+([^\s(:{\[]+)")
SCOPE = re.compile(r"^\s*(?:import|open|namespace|section|end|variable|universe|set_option|local notation|notation)\b")


class ProveError(Exception):
    """The request cannot be carried out (no such declaration, already proved, no Lean project …)."""


@dataclass
class Target:
    path: Path
    project: Path
    text: str
    name: str
    prefix: str            # everything before the declaration (with its doc comment and attributes): the user's, fixed
    decl: str              # the declaration as written, with `sorry`
    suffix: str            # everything after it
    statement: str         # normalized `theorem NAME … :` header; must not change


@dataclass
class Outcome:
    solved: bool
    new_text: str | None
    diff: str
    model_calls: int
    spent_usd: float
    stopped: str = ""
    log: list[dict] = field(default_factory=list)


def find_project(path: Path) -> Path:
    for d in [path.parent, *path.parent.parents]:
        if any((d / f).exists() for f in LAKEFILES):
            return d
    raise ProveError(f"{path} is not inside a Lean project (no {' or '.join(LAKEFILES)} above it)")


def load_target(path: Path, name: str, project: Path | None = None) -> Target:
    path = path.resolve()
    if not path.exists():
        raise ProveError(f"no such file: {path}")
    text = path.read_text(encoding="utf-8")
    span = decl_span(text, name)
    if not span:
        raise ProveError(f"no `theorem {name}` or `lemma {name}` in {path.name}")
    start, head_end, end = span
    start = LEAD.search(text[:start]).start()          # keep its doc comment and attributes with the declaration
    if not re.search(r"\bsorry\b", text[head_end:end]):
        raise ProveError(f"`{name}` has no `sorry` to close")
    return Target(path, (project or find_project(path)).resolve(), text, name, text[:start], text[start:end], text[end:],
                  statement_of(text, name))


def namespace_of(prefix: str) -> list[str]:
    """The namespaces open at the end of `prefix` (`namespace A.B` … `end`; sections count as unnamed scopes)."""
    stack: list[str | None] = []
    for line in prefix.splitlines():
        m = re.match(r"^\s*(namespace|section|end)\b\s*([^\s-]*)", line)
        if not m:
            continue
        if m.group(1) == "namespace":
            stack.append(m.group(2))
        elif m.group(1) == "section":
            stack.append(None)
        elif stack:
            stack.pop()
    return [x for x in stack if x]


def qualify(name: str, prefix: str) -> str:
    return name[len("_root_."):] if name.startswith("_root_.") else ".".join(namespace_of(prefix) + [name])


def checker_for(target: Target, timeout_s: int = 300) -> LeanChecker:
    """Lean for the user's project. Our REPL binary is reused when the project uses the same Lean version; otherwise
    every check is a one-shot build (slower, same verdict)."""
    imports = [l.strip() for l in target.prefix.splitlines() if re.match(r"^\s*import\s", l)]
    ours = DEFAULT_WORKSPACE / ".lake" / "packages" / "REPL" / ".lake" / "build" / "bin" / "repl"
    same_lean = ((DEFAULT_WORKSPACE / "lean-toolchain").exists() and (target.project / "lean-toolchain").exists()
                 and (DEFAULT_WORKSPACE / "lean-toolchain").read_text().strip() == (target.project / "lean-toolchain").read_text().strip())
    repl = ours if imports and same_lean and ours.exists() else None
    c = LeanChecker(target.project, timeout_s=timeout_s, header="\n".join(imports), require_mathlib=False, repl_bin=repl)
    roots = set(ALLOWED_IMPORT_ROOTS)
    for d in map(Path, c.lean_path):              # the modules this project can see (its own, its packages, Lean's)
        roots |= {p.stem for p in d.iterdir() if p.suffix == ".olean" or p.is_dir()} if d.is_dir() else set()
    c.import_roots = tuple(sorted(roots))
    return c


def view_of(target: Target) -> tuple[str, bool]:
    """What the model sees: the file up to the declaration, shortened for long files (imports and scope commands kept)."""
    if len(target.prefix) <= VIEW_LIMIT:
        return target.prefix + target.decl, False
    lines = target.prefix.splitlines()
    head, tail = lines[:-VIEW_TAIL_LINES], lines[-VIEW_TAIL_LINES:]
    scope = [l for l in head if SCOPE.match(l)]
    note = f"-- ({len(head) - len(scope)} earlier lines of {target.path.name} omitted here; they are part of the file)"
    return "\n".join(scope + [note] + tail) + "\n" + target.decl, True


def rebuild(source: str, target: Target) -> str | None:
    """The user's prefix + the model's new helper lemmas + the model's version of the declaration. Anything else the model
    wrote (its copy of earlier declarations, imports, later text) is dropped."""
    span = decl_span(source, target.name)
    if not span:
        return None
    start, _head_end, end = span
    start = LEAD.search(source[:start]).start()
    main = source[start:end].strip()
    known = set(DEFINED.findall(target.prefix + target.suffix))     # the model's copies of existing declarations
    before = "\n" + source[:start]
    cuts = [m.start() for m in DECL_START.finditer(before)] + [len(before)]
    helpers = []
    for a, b in zip(cuts, cuts[1:]):
        block = before[a:b].strip()
        m = BLOCK_NAME.match(block)
        if m and m.group(1) not in known and m.group(1) != target.name:
            helpers.append(block)
    sep = "" if target.prefix.endswith("\n\n") or not target.prefix else ("\n" if target.prefix.endswith("\n") else "\n\n")
    return target.prefix + sep + "".join(h + "\n\n" for h in helpers) + main + "\n"


def splice(proved: str, target: Target) -> str:
    """The new file: the proved part followed by the rest of the original file."""
    suffix = target.suffix if target.suffix.startswith("\n") or not target.suffix else "\n" + target.suffix
    return proved.rstrip("\n") + "\n" + suffix


def prove_file(target: Target, checker: LeanChecker, ask: Callable[..., str], *, hints: str = "", rounds: int = 3,
               more: Callable[[], bool] | None = None, record: Callable[[str, dict], None] = lambda k, p: None,
               progress: Callable | None = None) -> Outcome:
    from .prover import prove_search

    statement_file, shortened = view_of(target)
    oneshot = checker.repl_bin is None

    def check(source: str, name: str | None = None, approved: str | None = None) -> dict:
        name = name or target.name
        full = rebuild(source, target)
        if full is None:
            rep = {"compiled": False, "verified": False, "errors": [f"the answer does not contain `{target.name}`"],
                   "problems": [], "warnings": [], "output": ""}
        else:
            rep = asdict(checker.check(full, name, target.statement if name == target.name else approved,
                                       clean=oneshot, trusted_prefix=target.prefix, qualified=qualify(name, target.prefix)))
        record("lean.output", {"theorem": name, "verified": rep["verified"], "errors": rep["errors"][:3],
                               "problems": rep["problems"][:3], "sha256": hashlib.sha256((full or "").encode()).hexdigest()})
        return rep

    interactive = None
    if not oneshot and not shortened:
        interactive = Interactive(goals=checker.goals, tactic=checker.tactic)
    context = (f"This file belongs to the user's Lean project {target.project.name}. Prove only `{target.name}`: keep its "
               f"statement exactly, do not change or restate earlier declarations, and put any helper lemmas directly above "
               f"`{target.name}`." + (" Earlier parts of the file are omitted in the view; they exist and can be used." if shortened else ""))
    res = prove_search(statement_file, target.name, ask, check, attempts=2, hints=f"{context} {hints}".strip(),
                       progress=progress, rounds=rounds, more=more, heavy=True, interactive=interactive)
    log = [{k: v for k, v in e.items() if k != "source"} for e in res.log]
    if not res.solved:
        return Outcome(False, None, "", res.attempts, 0.0, "no proof found within the budget", log)
    proved = rebuild(res.source, target)
    # Final check: one-shot build of the proved part (guards on the model's text), then of the whole new file.
    full_name = qualify(target.name, target.prefix)
    final = checker.check(proved, target.name, target.statement, clean=True, trusted_prefix=target.prefix, qualified=full_name)
    new_text = splice(proved, target)
    whole = checker.check(new_text, target.name, target.statement, clean=True, trusted_prefix=new_text, qualified=full_name)
    if not (final.verified and whole.verified):
        why = (final.problems + final.errors + whole.problems + whole.errors)[:3]
        record("prove.final_check_failed", {"errors": why})
        return Outcome(False, None, "", res.attempts, 0.0, f"the final clean build failed: {'; '.join(why)}", log)
    rel = target.path.relative_to(target.project) if target.project in target.path.parents else Path(target.path.name)
    diff = "".join(difflib.unified_diff(target.text.splitlines(True), new_text.splitlines(True), f"a/{rel}", f"b/{rel}"))
    return Outcome(True, new_text, diff, res.attempts, 0.0, "", log)


def run_dir(home: Path, target: Target) -> Path:
    """Where the event log of a `quaera prove` run lives (not under projects/: it is not a research project)."""
    key = hashlib.sha256(f"{target.path}:{target.name}".encode()).hexdigest()[:8]
    d = home / "prove" / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', target.name)[:40]}-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d
