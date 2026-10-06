"""Sandbox (ADR 0002, ADR 0009).

The default Linux backend is bubblewrap (bwrap): no daemon needed, runs unprivileged.
- Network is off (--unshare-net); it is enabled only for calls given an allowlist.
- File system: only system libraries and explicitly given directories are visible, read-only;
  the home directory, SSH keys and other user files are not visible.
- The only writable location is the working directory.
- Wall-clock time (timeout), CPU time and process count are limited.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

def resource_prefix(mem: str | None = None, cpu: str | None = None) -> list[str]:
    """A memory/CPU-limited systemd scope for every command run in the sandbox (if available).

    The defaults can be changed with QUAERA_SANDBOX_MEM (2G) and QUAERA_SANDBOX_CPU (200%).
    If a limit is exceeded, only that command is killed. Without a systemd user session it runs unlimited.
    """
    if not shutil.which("systemd-run") or os.environ.get("QUAERA_NO_SYSTEMD"):
        return []
    mem = mem or os.environ.get("QUAERA_SANDBOX_MEM", "2G")
    cpu = cpu or os.environ.get("QUAERA_SANDBOX_CPU", "200%")
    return ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={mem}", "-p", "MemorySwapMax=0",
            "-p", f"CPUQuota={cpu}", "--"]


SYSTEM_RO = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives", "/etc/ssl", "/etc/ld.so.cache"]


class SandboxUnavailable(Exception):
    pass


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class BwrapSandbox:
    def __init__(self, cpu_seconds: int = 600, max_processes: int = 256, mem: str | None = None):
        if not shutil.which("bwrap"):
            raise SandboxUnavailable("bubblewrap (bwrap) is not installed")
        self.mem = mem
        self.cpu_seconds = cpu_seconds
        self.max_processes = max_processes

    def run(
        self,
        cmd: list[str],
        workdir: Path,
        ro_paths: list[Path] = (),
        env: dict[str, str] | None = None,
        timeout_s: int = 300,
        network: bool = False,
        cwd: Path | None = None,
    ) -> SandboxResult:
        workdir = Path(workdir).resolve()
        args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
        if network:
            args.append("--share-net")
        for p in SYSTEM_RO:
            if os.path.exists(p):
                args += ["--ro-bind", p, p]
        for p in ro_paths:
            p = str(Path(p).resolve())
            args += ["--ro-bind", p, p]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(workdir), str(workdir)]
        args += ["--chdir", str(Path(cwd).resolve() if cwd else workdir)]
        args += ["--clearenv"]
        base_env = {"PATH": "/usr/bin:/bin", "HOME": str(workdir), "LANG": "C.UTF-8"}
        for k, v in {**base_env, **(env or {})}.items():
            args += ["--setenv", k, v]
        limits = ["prlimit", f"--cpu={self.cpu_seconds}", f"--nproc={self.max_processes}", "--"]
        try:
            proc = subprocess.run(resource_prefix(self.mem) + args + limits + cmd, capture_output=True, text=True, timeout=timeout_s)
            return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return SandboxResult(-1, out, err, True)


def fresh_workdir(prefix: str = "quaera-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
