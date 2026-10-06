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
"""

from __future__ import annotations

import json
import os
import re
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .sandbox import SYSTEM_RO, BwrapSandbox, fresh_workdir, resource_prefix

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE = ROOT / "lean"
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


def statement_of(source: str, theorem: str) -> str | None:
    """Returns the `theorem NAME ... :=` header with whitespace normalized."""
    m = re.search(rf"(?:theorem|lemma)\s+{re.escape(theorem)}\b(.*?):=", source, re.S)
    return normalize(f"theorem {theorem}{m.group(1)}") if m else None


class _Repl:
    """A persistent Lean REPL process running inside the sandbox; the `import Mathlib` environment is kept ready.

    Output is read line by line on a separate thread and put on a queue. (select() + readline()
    cannot be combined: once readline pulls the answer into Python's internal buffer, select sees nothing.)
    """

    def __init__(self, argv: list[str], load_timeout_s: int):
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        resp = self.send({"cmd": "import Mathlib"}, load_timeout_s)
        if resp is None or "env" not in resp:
            self.close()
            raise LeanUnavailable(f"REPL could not load Mathlib: {resp}")
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


class LeanChecker:
    def __init__(self, workspace: Path = DEFAULT_WORKSPACE, timeout_s: int = 300, load_timeout_s: int = 1800):
        self.workspace = Path(workspace).resolve()
        toolchain = (self.workspace / "lean-toolchain").read_text().strip()
        self.toolchain_dir = ELAN_HOME / "toolchains" / toolchain.replace("/", "--").replace(":", "---")
        self.lean_bin = self.toolchain_dir / "bin" / "lean"
        if not self.lean_bin.exists():
            raise LeanUnavailable(f"Lean toolchain not found: {self.toolchain_dir}")
        libs = sorted((self.workspace / ".lake" / "packages").glob("*/.lake/build/lib/lean"))
        if not any((p / "Mathlib.olean").exists() for p in libs):
            raise LeanUnavailable("Mathlib build files are missing; run `lake exe cache get`")
        self.lean_path = [str(p) for p in libs] + [str(self.toolchain_dir / "lib" / "lean")]
        self.repl_bin = self.workspace / ".lake" / "packages" / "REPL" / ".lake" / "build" / "bin" / "repl"
        self.timeout_s = timeout_s
        self.load_timeout_s = load_timeout_s
        # Lean with Mathlib loaded holds ~5-6 GB (mostly reclaimable file cache); the limit can be changed with QUAERA_LEAN_MEM.
        self.mem = os.environ.get("QUAERA_LEAN_MEM", "7G")
        self.sandbox = BwrapSandbox(cpu_seconds=max(timeout_s, load_timeout_s) * 2, mem=self.mem)
        self._repl: _Repl | None = None

    # --- shared -----------------------------------------------------------------
    def _prepare(self, source: str) -> tuple[str, list[str]]:
        """Rejects disallowed imports; replaces import lines with blank lines (line numbers are preserved)."""
        problems, lines = [], []
        for line in source.splitlines():
            m = re.match(r"^\s*import\s+(\S+)", line)
            if m:
                if not m.group(1).startswith(ALLOWED_IMPORT_ROOTS):
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
              clean: bool = False) -> LeanReport:
        started = time.monotonic()
        theorem = theorem or (THEOREM_RE.findall(source) or [None])[-1]
        body, problems = self._prepare(source)
        if re.search(r"^\s*axiom\s", source, re.M):
            problems.append("The file declares a new `axiom`; this is not accepted.")
        for m in FORBIDDEN_CMD_RE.finditer(source):
            problems.append(f"The `{m.group(1)}` command is not allowed in a proof file.")
        if approved_statement and theorem and statement_of(source, theorem) != normalize(approved_statement):
            problems.append("The approved theorem statement was changed; it must be kept exactly.")
        if theorem:
            body = body.rstrip() + f"\n\n#print axioms {theorem}\n"
        if clean:
            return self._oneshot(source, theorem, problems, started)
        return self._via_repl(body, theorem, problems, started)

    # --- REPL -------------------------------------------------------------------
    def _repl_argv(self) -> list[str]:
        work = fresh_workdir("quaera-repl-")
        args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
        for p in SYSTEM_RO:
            if os.path.exists(p):
                args += ["--ro-bind", p, p]
        for p in (self.toolchain_dir, self.workspace):
            args += ["--ro-bind", str(p), str(p)]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(work), str(work), "--chdir", str(work),
                 "--clearenv", "--setenv", "PATH", f"{self.toolchain_dir / 'bin'}:/usr/bin:/bin", "--setenv", "HOME", str(work),
                 "--setenv", "LEAN_PATH", ":".join(self.lean_path)]
        return resource_prefix(self.mem, os.environ.get("QUAERA_LEAN_CPU", "200%")) + args + [str(self.repl_bin)]

    def _via_repl(self, body: str, theorem, problems, started) -> LeanReport:
        if not self.repl_bin.exists():
            raise LeanUnavailable("REPL is not built; run `lake build REPL/repl` in lean/")
        if self._repl is None or not self._repl.alive():
            self._repl = _Repl(self._repl_argv(), self.load_timeout_s)
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

    # --- one-shot, clean environment --------------------------------------------
    def _oneshot(self, source: str, theorem, problems, started) -> LeanReport:
        work = fresh_workdir("quaera-lean-")
        text = source.rstrip() + (f"\n\n#print axioms {theorem}\n" if theorem else "\n")
        (work / "Proof.lean").write_text(text, encoding="utf-8")
        res = self.sandbox.run([str(self.lean_bin), str(work / "Proof.lean")], workdir=work,
                               ro_paths=[self.toolchain_dir, self.workspace], env={"LEAN_PATH": ":".join(self.lean_path)},
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
