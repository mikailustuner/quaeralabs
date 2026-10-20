"""Lean 4 + Mathlib toolchain.

The "verified" verdict is given only here, from the compiler output:
- the file must compile without errors,
- the theorem must depend only on the standard axioms (propext, Classical.choice, Quot.sound);
  `sorry` (sorryAx), `native_decide` or new `axiom` declarations are rejected,
- the approved theorem statement must not have been changed during the proof.

Two modes:
- REPL (default): Mathlib is loaded once, every later check takes seconds. For the Engineer's loop.
- One-shot compilation (clean=True): `lean` runs from scratch each time, in a fresh sandbox.
  The Verifier uses this; it is slow but fully independent of the REPL's state.

Both modes run inside the sandbox, without network, with only the toolchain and Mathlib visible read-only.

Where Lean comes from (improvement plan A2): the host workspace (lean/, built by install.sh --with-lean) or the prebuilt
Lean image (quaera.images), which runs in the container sandbox and needs nothing on the host but Docker or Podman.
QUAERA_LEAN=host|image|auto; auto prefers the host workspace on Linux and otherwise uses the image if it has been pulled.
"""

from __future__ import annotations

import json
import os
import collections
import functools
import re
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import images
from .resources import lean_workspace, quaera_home
from .sandbox import ContainerSandbox, fresh_workdir, make_sandbox

DEFAULT_WORKSPACE = lean_workspace()
ELAN_HOME = Path(os.environ.get("ELAN_HOME", Path.home() / ".elan"))
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
ALLOWED_IMPORT_ROOTS = ("Mathlib", "Batteries", "Aesop", "Qq", "Plausible", "ProofWidgets", "ImportGraph", "LeanSearchClient")

MSG_RE = re.compile(r"^[^:\n]*:(\d+):(\d+): (error|warning|info): (.*?)(?=^[^:\n]*:\d+:\d+: |\Z)", re.M | re.S)
AXIOMS_RE = re.compile(r"'([^']+)' depends on axioms: \[(.*?)\]", re.S)
NO_AXIOMS_RE = re.compile(r"'([^']+)' does not depend on any axioms")
# Commands that produce output or change the environment/syntax are forbidden in a proof file,
# so the check cannot be fooled by printing a fake "depends on axioms" line.
FORBIDDEN_CMD_RE = re.compile(
    r"^\s*(#eval|#print|#exit|#check_failure|run_cmd|run_elab|run_meta|elab|elab_rules|macro|macro_rules|syntax|notation|initialize|builtin_initialize|unsafe)\b",
    re.M,
)
# One-shot builds load Mathlib from scratch (several GB each): at most QUAERA_LEAN_SLOTS of them at a time in a process.
LEAN_SLOTS = threading.BoundedSemaphore(max(1, int(os.environ.get("QUAERA_LEAN_SLOTS", "1"))))
SLOW_LOAD_S = 300            # a Mathlib load slower than this makes Lean impractical here (quaera doctor warns)
THEOREM_RE = re.compile(r"^\s*(?:theorem|lemma)\s+([^\s(:{\[]+)", re.M)


class LeanUnavailable(Exception):
    pass


@dataclass
class LeanReport:
    compiled: bool
    verified: bool
    theorem: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    axioms: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    output: str = ""
    timed_out: bool = False
    mode: str = "repl"
    seconds: float = 0.0


def normalize(stmt: str) -> str:
    return " ".join(stmt.replace(":= by", "").replace(":=", "").split())


def _toolchain_dir(workspace: Path) -> Path:
    toolchain = (workspace / "lean-toolchain").read_text().strip()
    return ELAN_HOME / "toolchains" / toolchain.replace("/", "--").replace(":", "---")


def host_ready(workspace: Path = DEFAULT_WORKSPACE) -> bool:
    """The host workspace has a toolchain and the Mathlib build files."""
    return ((workspace / "lean-toolchain").exists() and (_toolchain_dir(workspace) / "bin" / "lean").exists()
            and (workspace / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean").exists())


@functools.lru_cache(maxsize=1)
def configured_image() -> str | None:
    """The Lean image to use for our Mathlib workspace, or None for the host workspace (QUAERA_LEAN)."""
    m = images.mode("lean")
    if m == "image" or (m == "auto" and not host_ready() and images.present(images.lean_image())):
        return images.lean_image()
    return None


def mathlib_rev(workspace: Path | None = None) -> str:
    """The pinned Mathlib revision (lemma bank and Mathlib index are keyed by it); the image's when Lean runs from one."""
    if workspace is None:
        try:
            if configured_image():
                return images.layout(configured_image())["mathlibRev"]
        except (images.ImageUnavailable, KeyError):
            return "unknown"
        workspace = DEFAULT_WORKSPACE
    try:
        m = re.search(r'rev = "([^"]+)"', (workspace / "lakefile.toml").read_text())
    except OSError:
        return "unknown"
    return m.group(1) if m else "unknown"


def lean_versions() -> tuple[str, str]:
    """(toolchain, Mathlib revision) of the configured Lean, for the preregistration."""
    img = configured_image()
    if img:
        lay = images.layout(img)
        return lay["toolchainName"], lay["mathlibRev"]
    return (DEFAULT_WORKSPACE / "lean-toolchain").read_text().strip(), mathlib_rev(DEFAULT_WORKSPACE)


def mathlib_sources() -> Path:
    """The Mathlib source tree (for the search index): the host workspace's, or copied out of the image once."""
    img = configured_image()
    if not img:
        return DEFAULT_WORKSPACE / ".lake" / "packages" / "mathlib" / "Mathlib"
    root = images.layout(img)["workspace"]
    return images.copy_out(img, f"{root}/.lake/packages/mathlib/Mathlib",
                           quaera_home() / "cache" / f"mathlib-src-{mathlib_rev()}" / "Mathlib")


def statement_of(source: str, theorem: str) -> str | None:
    """Returns the `theorem NAME ... :=` header with whitespace normalized."""
    m = re.search(rf"(?:theorem|lemma)\s+{re.escape(theorem)}\b(.*?):=", source, re.S)
    return normalize(f"theorem {theorem}{m.group(1)}") if m else None


def record_load(seconds: float, ok: bool) -> None:
    """The last Mathlib load (time, success), for `quaera doctor` (improvement plan A4)."""
    try:
        path = quaera_home() / "lean-load.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seconds": round(seconds, 1), "ok": ok, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    except OSError:
        pass


def last_load() -> dict | None:
    try:
        return json.loads((quaera_home() / "lean-load.json").read_text())
    except (OSError, ValueError):
        return None


class _Repl:
    """A persistent Lean REPL process running inside the sandbox; the `import Mathlib` environment is kept ready.

    Output is read line by line on a separate thread and put on a queue. (select() + readline()
    cannot be combined: once readline pulls the answer into Python's internal buffer, select sees nothing.)
    """

    def __init__(self, argv: list[str], load_timeout_s: int, stop: list[str] | None = None, header: str = "import Mathlib"):
        self.stop = stop          # container backend: removes the container (killing its client would leave it running)
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stderr: collections.deque[str] = collections.deque(maxlen=20)
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=lambda: self.stderr.extend(self.proc.stderr), daemon=True).start()
        started = time.monotonic()
        resp = self.send({"cmd": header}, load_timeout_s)
        errors = [m.get("data", "") for m in (resp or {}).get("messages", []) if m.get("severity") == "error"]
        ok = resp is not None and "env" in resp and not errors
        if header == "import Mathlib":
            record_load(time.monotonic() - started, ok)
        if not ok:
            exited = self.proc.poll()
            self.close()
            if resp is None and exited is not None:
                # the process died (a sandbox or environment problem, not a slow load): say so, with its own words
                time.sleep(0.2)
                raise LeanUnavailable(f"the Lean REPL exited with code {exited} before loading `{header}`: "
                                      + ("".join(self.stderr).strip()[-500:] or "no error output"))
            if resp is None:
                raise LeanUnavailable(f"Lean could not load `{header}` within {load_timeout_s} s. On a slow (rotating) "
                                      "disk Mathlib may not load at all; see docs/installation.md#lean-on-a-slow-disk")
            raise LeanUnavailable(f"REPL could not load `{header}`: " + ("; ".join(errors)[:400] if errors else str(resp)))
        self.base_env = resp["env"]

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)  # process exited

    def send(self, payload: dict, timeout_s: int) -> dict | None:
        self.proc.stdin.write(json.dumps(payload) + "\n\n")
        self.proc.stdin.flush()
        buf, deadline = "", time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None  # timed out
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                return None  # process exited
            if line.strip() == "" and buf.strip():
                return json.loads(buf)
            buf += line

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        if self.alive():
            self.proc.kill()
            self.proc.wait(10)
        if self.stop:
            subprocess.run(self.stop, capture_output=True, timeout=60)
            self.stop = None


class LeanChecker:
    """Without `workspace`: our pinned Mathlib workspace, on the host or from the Lean image (`configured_image`).
    For a user's Lean project (improvement plan C3,
    `quaera prove`) it is that project: `header` is the file's import block (what the REPL loads once), `require_mathlib`
    is off, `repl_bin` is ours when the toolchains match (otherwise `repl_bin=None`: every check is a one-shot build),
    and `import_roots` are the modules the project can see."""

    def __init__(self, workspace: Path | None = None, timeout_s: int = 300, load_timeout_s: int = 1800, *,
                 header: str = "import Mathlib", require_mathlib: bool = True, repl_bin: Path | None | str = "default",
                 import_roots: tuple[str, ...] = ALLOWED_IMPORT_ROOTS):
        try:
            self.image = configured_image() if workspace is None else None
        except images.ImageUnavailable as exc:
            raise LeanUnavailable(str(exc)) from None
        if self.image:
            try:
                lay = images.layout(self.image)
            except images.ImageUnavailable as exc:
                raise LeanUnavailable(str(exc)) from None
            self.workspace, self.toolchain_dir, self.lean_bin = Path(lay["workspace"]), Path(lay["toolchain"]), Path(lay["lean"])
            self.lean_path = list(lay["leanPath"])
            default_repl = Path(lay["repl"])
            self.binds: list = []                  # everything is inside the image
        else:
            self._host(Path(workspace or DEFAULT_WORKSPACE).resolve(), require_mathlib)
            default_repl = self.workspace / ".lake" / "packages" / "REPL" / ".lake" / "build" / "bin" / "repl"
        self.repl_bin = default_repl if repl_bin == "default" else (Path(repl_bin) if repl_bin else None)
        self.header = header
        self.import_roots = tuple(import_roots)
        self.timeout_s = timeout_s
        self.load_timeout_s = load_timeout_s
        # Lean with Mathlib loaded holds ~5-6 GB (mostly reclaimable file cache); the limit can be changed with QUAERA_LEAN_MEM.
        self.mem = os.environ.get("QUAERA_LEAN_MEM", "7G")
        limits = dict(cpu_seconds=max(timeout_s, load_timeout_s) * 2, mem=self.mem, cpu=os.environ.get("QUAERA_LEAN_CPU", "200%"))
        self.sandbox = (ContainerSandbox(**limits, image=self.image, host_binds=False) if self.image else make_sandbox(**limits))
        self._repl: _Repl | None = None

    def _host(self, workspace: Path, require_mathlib: bool) -> None:
        if sys.platform != "linux":
            raise LeanUnavailable("host Lean runs only on Linux; use the Lean image (QUAERA_LEAN=image)")
        self.workspace = workspace
        if not (workspace / "lean-toolchain").exists():
            raise LeanUnavailable(f"no Lean workspace at {workspace}")
        self.toolchain_dir = _toolchain_dir(workspace)
        self.lean_bin = self.toolchain_dir / "bin" / "lean"
        if not self.lean_bin.exists():
            raise LeanUnavailable(f"Lean toolchain not found: {self.toolchain_dir}")
        # Resolved: packages may live outside the project (a symlinked or relocated packages directory), and the sandbox
        # only sees what is bound, so every library directory outside the workspace is bound as well.
        libs = sorted(p.resolve() for p in (workspace / ".lake" / "packages").glob("*/.lake/build/lib/lean"))
        if require_mathlib and not any((p / "Mathlib.olean").exists() for p in libs):
            raise LeanUnavailable("Mathlib build files are missing; run `lake exe cache get`")
        own = workspace / ".lake" / "build" / "lib" / "lean"      # a project's own compiled modules
        self.lean_path = [str(p) for p in libs] + ([str(own)] if own.is_dir() else []) + [str(self.toolchain_dir / "lib" / "lean")]
        self.binds = [self.toolchain_dir, workspace] + [p for p in libs if workspace not in p.parents]

    def has_repl(self) -> bool:
        return self.repl_bin is not None and (self.image is not None or self.repl_bin.exists())

    # --- shared -----------------------------------------------------------------
    def _prepare(self, source: str) -> tuple[str, list[str]]:
        """Rejects disallowed imports; replaces import lines with blank lines (line numbers are preserved)."""
        problems, lines = [], []
        for line in source.splitlines():
            m = re.match(r"^\s*import\s+(\S+)", line)
            if m:
                if not m.group(1).startswith(self.import_roots):
                    problems.append(f"Import not allowed: {m.group(1)}")
                lines.append("")
            else:
                lines.append(line)
        return "\n".join(lines), problems

    def _finish(self, mode, theorem, messages, problems, output, timed_out, started) -> LeanReport:
        errors = [f"line {ln}: {txt.strip()}" for sev, ln, txt in messages if sev == "error"]
        warnings = [f"line {ln}: {txt.strip()}" for sev, ln, txt in messages if sev == "warning"]
        infos = "\n".join(txt for sev, _ln, txt in messages if sev == "info")
        axioms: list[str] = []
        if theorem:
            # Only the LAST line for this theorem: the output of the #print axioms we appended to the file.
            ours = [m for m in AXIOMS_RE.finditer(infos) if m.group(1) == theorem]
            none = [m for m in NO_AXIOMS_RE.finditer(infos) if m.group(1) == theorem]
            if ours and (not none or ours[-1].start() > none[-1].start()):
                axioms = [a.strip() for a in ours[-1].group(2).split(",") if a.strip()]
            elif not none and not errors and not timed_out:
                problems.append("Could not read the axiom list.")
        else:
            problems.append("No theorem found in the file.")
        bad = set(axioms) - ALLOWED_AXIOMS
        if "sorryAx" in bad:
            problems.append("Proof contains `sorry`.")
        if bad - {"sorryAx"}:
            problems.append(f"Axioms not allowed: {', '.join(sorted(bad - {'sorryAx'}))}")
        if timed_out:
            errors.append(f"timed out ({self.timeout_s} s)")
        compiled = not errors and not timed_out
        return LeanReport(compiled, compiled and not problems, theorem, errors, warnings, axioms, problems,
                          output[-4000:], timed_out, mode, round(time.monotonic() - started, 1))

    def check(self, source: str, theorem: str | None = None, approved_statement: str | None = None,
              clean: bool = False, trusted_prefix: str | None = None, qualified: str | None = None) -> LeanReport:
        """`trusted_prefix`: the start of the file written by the user (project mode). The guards against a model faking
        the verdict (no new axiom, no output-producing or syntax-changing command) apply to the rest of the file.
        `qualified`: the theorem's full name when it is declared inside a namespace (the axiom check needs it)."""
        started = time.monotonic()
        theorem = theorem or (THEOREM_RE.findall(source) or [None])[-1]
        body, problems = self._prepare(source)
        guarded = source
        if trusted_prefix is not None:
            if not source.startswith(trusted_prefix):
                problems.append("The fixed part of the file (everything before the declaration) was changed.")
            else:
                guarded = source[len(trusted_prefix):]
        if re.search(r"^\s*axiom\s", guarded, re.M):
            problems.append("The file declares a new `axiom`; this is not accepted.")
        for m in FORBIDDEN_CMD_RE.finditer(guarded):
            problems.append(f"The `{m.group(1)}` command is not allowed in a proof file.")
        if approved_statement and theorem and statement_of(source, theorem) != normalize(approved_statement):
            problems.append("The approved theorem statement was changed; it must be kept exactly.")
        if theorem and qualified:
            theorem = qualified
        if theorem:
            body = body.rstrip() + f"\n\n#print axioms {theorem}\n"
        if clean or self.repl_bin is None:
            return self._oneshot(source, theorem, problems, started)
        return self._via_repl(body, theorem, problems, started)

    # --- REPL -------------------------------------------------------------------
    def _repl_argv(self) -> tuple[list[str], list[str] | None]:
        """The long-lived REPL: no CPU-time limit (it lives across many checks); memory and CPU share are limited."""
        work = fresh_workdir("quaera-repl-")
        extra = [] if self.image or self.workspace in self.repl_bin.parents else [self.repl_bin.parent]
        return self.sandbox.argv([str(self.repl_bin)], work, ro_paths=[*self.binds, *extra],
                                 env={"PATH": f"{self.toolchain_dir / 'bin'}:/usr/bin:/bin", "LEAN_PATH": ":".join(self.lean_path)},
                                 limits=False)

    def _via_repl(self, body: str, theorem, problems, started) -> LeanReport:
        if not self.has_repl():
            raise LeanUnavailable("REPL is not built; run `lake build REPL/repl` in lean/")
        if self._repl is None or not self._repl.alive():
            argv, stop = self._repl_argv()
            self._repl = _Repl(argv, self.load_timeout_s, stop, self.header)
        resp = self._repl.send({"cmd": body, "env": self._repl.base_env}, self.timeout_s)
        if resp is None:  # timeout or crash: restart the process
            self._repl.close()
            self._repl = None
            return self._finish("repl", theorem, [], problems, "", True, started)
        messages = [(m["severity"], m["pos"]["line"], m["data"]) for m in resp.get("messages", [])]
        if "message" in resp and not messages:  # REPL-level error
            messages = [("error", 0, resp["message"])]
        output = "\n".join(f"{s}:{ln}: {d}" for s, ln, d in messages)
        return self._finish("repl", theorem, messages, problems, output, False, started)

    # --- interactive proof states (capacity plan K2) ----------------------------------
    def _repl_ready(self) -> _Repl:
        if not self.has_repl():
            raise LeanUnavailable("REPL is not built; run `lake build REPL/repl` in lean/")
        if self._repl is None or not self._repl.alive():
            argv, stop = self._repl_argv()
            self._repl = _Repl(argv, self.load_timeout_s, stop, self.header)
        return self._repl

    def goals(self, source: str) -> list[dict]:
        """The goal state at every `sorry` of the file: [{"proofState", "goal", "line"}]. Proof states live in the
        REPL process; a restart invalidates them (the caller then sees an error from `tactic`)."""
        body, problems = self._prepare(source)
        if problems or FORBIDDEN_CMD_RE.search(source) or re.search(r"^\s*axiom\s", source, re.M):
            return []
        repl = self._repl_ready()
        resp = repl.send({"cmd": body, "env": repl.base_env}, self.timeout_s)
        if resp is None:
            self._repl.close()
            self._repl = None
            return []
        return [{"proofState": x["proofState"], "goal": x.get("goal", ""), "line": (x.get("pos") or {}).get("line", 0)}
                for x in resp.get("sorries", []) if "proofState" in x]

    def tactic(self, proof_state: int, tactic: str) -> dict:
        """Applies one tactic to a proof state: {"proofState", "goals", "error"}. The result is NOT a verdict; the finished
        script is compiled as a file through `check` like every other proof."""
        if self._repl is None or not self._repl.alive():
            return {"proofState": None, "goals": [], "error": "the REPL restarted; proof states are gone"}
        # The REPL's tactic mode parses ONE tactic; a sequence (`a; b`, several lines) is sent as one parenthesized
        # tactic block, which Lean accepts as a single tactic with the same effect.
        text = tactic.strip()
        if ";" in text or "\n" in text:
            text = "(" + text + ")"
        resp = self._repl.send({"tactic": text, "proofState": int(proof_state)}, min(self.timeout_s, 120))
        if resp is None:
            self._repl.close()
            self._repl = None
            return {"proofState": None, "goals": [], "error": "timed out"}
        errors = [m.get("data", "") for m in resp.get("messages", []) if m.get("severity") == "error"]
        if "message" in resp and "proofState" not in resp:
            errors.append(str(resp["message"]))
        if errors:
            return {"proofState": None, "goals": [], "error": "; ".join(errors)[:600]}
        return {"proofState": resp.get("proofState"), "goals": resp.get("goals", []), "error": None}

    # --- one-shot, clean environment --------------------------------------------
    def _oneshot(self, source: str, theorem, problems, started) -> LeanReport:
        work = fresh_workdir("quaera-lean-")
        text = source.rstrip() + (f"\n\n#print axioms {theorem}\n" if theorem else "\n")
        (work / "Proof.lean").write_text(text, encoding="utf-8")
        with LEAN_SLOTS:
            res = self.sandbox.run([str(self.lean_bin), str(work / "Proof.lean")], workdir=work,
                                   ro_paths=self.binds, env={"LEAN_PATH": ":".join(self.lean_path)},
                                   timeout_s=self.load_timeout_s)
        out = res.stdout + res.stderr
        messages = [(sev, int(ln), txt) for ln, _c, sev, txt in MSG_RE.findall(out)]
        # The `lean` command line prints the #print axioms output without a position prefix, so the line at the
        # end of the raw output is added separately (even if it got mixed into another message's text).
        for m in sorted(list(AXIOMS_RE.finditer(out)) + list(NO_AXIOMS_RE.finditer(out)), key=lambda m: m.start()):
            messages.append(("info", 0, m.group(0)))
        if res.returncode != 0 and not any(s == "error" for s, _, _ in messages) and not res.timed_out:
            messages.append(("error", 0, out.strip()[-500:] or f"lean exited with code {res.returncode}"))
        return self._finish("clean", theorem, messages, problems, out, res.timed_out, started)

    def close(self) -> None:
        if self._repl:
            self._repl.close()
