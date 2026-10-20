"""Sandbox (ADR 0002, ADR 0009, ADR 0018).

Two backends with one contract (tests/test_sandbox_contract.py runs the same checks on both):
- network off; it is enabled only for calls given an allowlist,
- file system: only system libraries and explicitly given directories are visible, read-only;
  the home directory, SSH keys and other user files are not visible,
- the only writable location is the working directory (plus a private /tmp),
- wall-clock time (timeout), CPU time, process count and memory are limited,
- the environment is cleared; only the variables given are set.

`bwrap` (bubblewrap) is the default on Linux: no daemon, unprivileged. `container` (Docker or Podman) is for systems where
unprivileged namespaces are blocked (Ubuntu's AppArmor restriction) and, with images, for macOS (improvement plan A2).
On Linux the container backend binds the host's system directories read-only at the same paths, exactly like bwrap, so the
same commands (the ML environment, the Lean toolchain) run in both. Choose with QUAERA_SANDBOX=bwrap|container|auto.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Union

Bind = Union[str, Path, tuple]      # a path bound at the same place, or (source, destination)


def resource_prefix(mem: str | None = None, cpu: str | None = None) -> list[str]:
    """A memory/CPU-limited systemd scope for every command run in the bwrap sandbox (if available).

    The defaults can be changed with QUAERA_SANDBOX_MEM (2G) and QUAERA_SANDBOX_CPU (200%).
    If a limit is exceeded, only that command is killed. Without a working systemd user session (no systemd-run, or
    no session bus, e.g. `sudo -iu user` without XDG_RUNTIME_DIR) it runs without these limits: systemd-run would fail
    at once and take the sandboxed command with it.
    """
    if os.environ.get("QUAERA_NO_SYSTEMD") or not systemd_scope_works():
        return []
    mem = mem or os.environ.get("QUAERA_SANDBOX_MEM", "2G")
    cpu = cpu or os.environ.get("QUAERA_SANDBOX_CPU", "200%")
    return ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={mem}", "-p", "MemorySwapMax=0",
            "-p", f"CPUQuota={cpu}", "--"]


@functools.lru_cache(maxsize=1)
def systemd_scope_works() -> bool:
    if not shutil.which("systemd-run"):
        return False
    try:
        return subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "true"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


SYSTEM_RO = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives", "/etc/ssl", "/etc/ld.so.cache"]
BASE_ENV_PATH = "/usr/bin:/bin"


class SandboxUnavailable(Exception):
    pass


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def _pairs(binds) -> list[tuple[str, str]]:
    out = []
    for b in binds or ():
        src, dst = b if isinstance(b, tuple) else (b, b)
        out.append((str(Path(src).resolve()), str(dst)))
    return out


class BwrapSandbox:
    kind = "bwrap"

    def __init__(self, cpu_seconds: int = 600, max_processes: int = 256, mem: str | None = None, cpu: str | None = None):
        if not shutil.which("bwrap"):
            raise SandboxUnavailable("bubblewrap (bwrap) is not installed")
        self.mem, self.cpu = mem, cpu
        self.cpu_seconds = cpu_seconds
        self.max_processes = max_processes

    def argv(self, cmd: list[str], workdir: Path, ro_paths: list[Bind] = (), env: dict[str, str] | None = None,
             network: bool = False, cwd: Path | None = None, devices: list[str] = (), limits: bool = True) -> tuple[list[str], list[str] | None]:
        """The full command line, and a command that stops it from outside (None: killing the process is enough)."""
        workdir = Path(workdir).resolve()
        args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
        if network:
            args.append("--share-net")
        for p in SYSTEM_RO:
            if os.path.exists(p):
                args += ["--ro-bind", p, p]
        # /tmp first: a bind below /tmp (a project or data directory there) must stay visible on top of the empty /tmp
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for src, dst in _pairs(ro_paths):
            args += ["--ro-bind", src, dst]
        args += ["--bind", str(workdir), str(workdir)]
        for dev in devices:
            if os.path.exists(dev):
                args += ["--dev-bind", dev, dev]
        args += ["--chdir", str(Path(cwd).resolve() if cwd else workdir)]
        args += ["--clearenv"]
        base_env = {"PATH": BASE_ENV_PATH, "HOME": str(workdir), "LANG": "C.UTF-8"}
        for k, v in {**base_env, **(env or {})}.items():
            args += ["--setenv", k, v]
        rlimits = ["prlimit", f"--cpu={self.cpu_seconds}", f"--nproc={self.max_processes}", "--"] if limits else []
        return resource_prefix(self.mem, self.cpu) + args + rlimits + list(cmd), None

    def run(self, cmd: list[str], workdir: Path, ro_paths: list[Bind] = (), env: dict[str, str] | None = None,
            timeout_s: int = 300, network: bool = False, cwd: Path | None = None, devices: list[str] = ()) -> SandboxResult:
        argv, stop = self.argv(cmd, workdir, ro_paths, env, network, cwd, devices)
        return _execute(argv, stop, timeout_s)


def _cpus(cpu: str) -> str:
    """systemd's CPUQuota ("200%") as Docker's --cpus ("2.0")."""
    cpu = cpu.strip()
    return f"{float(cpu[:-1]) / 100:g}" if cpu.endswith("%") else cpu


class ContainerSandbox:
    """Docker or Podman: one `--rm` container per command (ADR 0018).

    With host binds (the default on Linux) the image only provides an empty root: the host's system directories are bound
    read-only at the same paths, as in bwrap. Without them (QUAERA_SANDBOX_HOST_BINDS=0, and always on macOS) the image must
    contain the tools itself: the Lean and ML images (quaera.images) always run this way."""
    kind = "container"

    def __init__(self, cpu_seconds: int = 600, max_processes: int = 256, mem: str | None = None, cpu: str | None = None,
                 engine: str | None = None, image: str | None = None, host_binds: bool | None = None):
        self.engine = engine or os.environ.get("QUAERA_CONTAINER_ENGINE") or next(
            (e for e in ("podman", "docker") if shutil.which(e)), None)
        if not self.engine or not shutil.which(self.engine):
            raise SandboxUnavailable("neither podman nor docker is installed")
        self.image = image or os.environ.get("QUAERA_SANDBOX_IMAGE", "debian:13")
        if host_binds is None:
            host_binds = sys.platform == "linux" and os.environ.get("QUAERA_SANDBOX_HOST_BINDS", "1") != "0"
        self.host_binds = host_binds
        self.mem, self.cpu = mem, cpu
        self.cpu_seconds = cpu_seconds
        self.max_processes = max_processes

    def argv(self, cmd: list[str], workdir: Path, ro_paths: list[Bind] = (), env: dict[str, str] | None = None,
             network: bool = False, cwd: Path | None = None, devices: list[str] = (), limits: bool = True) -> tuple[list[str], list[str] | None]:
        workdir = Path(workdir).resolve()
        name = f"quaera-sbx-{uuid.uuid4().hex[:12]}"
        mem = self.mem or os.environ.get("QUAERA_SANDBOX_MEM", "2G")
        cpu = self.cpu or os.environ.get("QUAERA_SANDBOX_CPU", "200%")
        args = [self.engine, "run", "--rm", "-i", "--name", name,
                "--network", "bridge" if network else "none",
                "--read-only", "--tmpfs", "/tmp:rw,exec,size=1g", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", str(self.max_processes), "--memory", mem.lower(), "--memory-swap", mem.lower(),
                "--cpus", _cpus(cpu), "--user", f"{os.getuid()}:{os.getgid()}"]
        if limits:
            args += ["--ulimit", f"cpu={self.cpu_seconds}:{self.cpu_seconds}"]
        if self.engine == "podman":
            args += ["--userns", "keep-id"]
        if self.host_binds:
            for p in SYSTEM_RO:
                if os.path.exists(p):
                    args += ["-v", f"{p}:{p}:ro"]
        for src, dst in _pairs(ro_paths):
            args += ["-v", f"{src}:{dst}:ro"]
        args += ["-v", f"{workdir}:{workdir}:rw", "-w", str(Path(cwd).resolve() if cwd else workdir)]
        for dev in devices:
            if os.path.exists(dev):
                args += ["--device", dev]
        base_env = {"PATH": BASE_ENV_PATH, "HOME": str(workdir), "LANG": "C.UTF-8"}
        for k, v in {**base_env, **(env or {})}.items():
            args += ["-e", f"{k}={v}"]
        return args + [self.image] + list(cmd), [self.engine, "rm", "-f", name]

    def run(self, cmd: list[str], workdir: Path, ro_paths: list[Bind] = (), env: dict[str, str] | None = None,
            timeout_s: int = 300, network: bool = False, cwd: Path | None = None, devices: list[str] = ()) -> SandboxResult:
        argv, stop = self.argv(cmd, workdir, ro_paths, env, network, cwd, devices)
        return _execute(argv, stop, timeout_s)


def _execute(argv: list[str], stop: list[str] | None, timeout_s: int) -> SandboxResult:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False)
    except subprocess.TimeoutExpired as exc:
        if stop:   # killing the container client would leave the container running
            subprocess.run(stop, capture_output=True, timeout=60)
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return SandboxResult(-1, out, err, True)


@functools.lru_cache(maxsize=1)
def bwrap_works() -> bool:
    if not shutil.which("bwrap"):
        return False
    try:
        # /proc and /dev too: inside an unprivileged container namespaces may work while mounting proc does not
        return subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "true"],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def make_sandbox(**kw) -> BwrapSandbox | ContainerSandbox:
    """The configured backend (QUAERA_SANDBOX): `bwrap`, `container`, or `auto` (default): bwrap where unprivileged
    namespaces work, otherwise a container engine. Never falls back to running without a sandbox."""
    choice = os.environ.get("QUAERA_SANDBOX", "auto").strip().lower()
    if choice == "bwrap":
        return BwrapSandbox(**kw)
    if choice == "container":
        return ContainerSandbox(**kw)
    if choice != "auto":
        raise SandboxUnavailable(f"unknown QUAERA_SANDBOX value: {choice!r} (bwrap, container or auto)")
    if sys.platform == "linux" and bwrap_works():
        return BwrapSandbox(**kw)
    try:
        return ContainerSandbox(**kw)
    except SandboxUnavailable:
        raise SandboxUnavailable("no sandbox: bubblewrap cannot create namespaces here and neither podman nor docker is "
                                 "installed (docs/installation.md#bubblewrap)") from None


def fresh_workdir(prefix: str = "quaera-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
