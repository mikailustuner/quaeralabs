"""Sandbox MCP server: writing and running experiment code, and setting up a clean environment.

Every project has a working directory (`<project>/work`); the code is kept there in a git repository.
- sandbox_write: writes a file and commits it; returns the commit ID.
- sandbox_exec: runs a command inside bwrap. No network, the ML environment and data are read-only, only the working directory is writable.
  GPU is enabled only with `gpu=true` and after the tool registry's permission check.
- sandbox_create_clean: extracts a given commit from scratch into a new directory (the Verifier's clean environment).

Remote runner (capacity plan ML3): with QUAERA_REMOTE_RUNNER set (a command prefix, e.g. `ssh -i ~/.ssh/key user@gpu-host`),
a GPU job is sent there under the same contract: the working directory and the data are synced with rsync, the remote
`quaera sandbox-exec` runs the command in ITS bubblewrap sandbox (no network, data read-only, only the working directory
writable), the working directory is synced back and the remote copy removed. QUAERA_REMOTE_DIR is the remote base directory
(default /tmp/quaera-remote), QUAERA_REMOTE_PREFIX the rsync host prefix (default: the last word of the runner + ":"),
QUAERA_REMOTE_QUAERA the remote command (default `quaera`). Without a runner a GPU job uses the local GPU devices.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from quaera.sandbox import SYSTEM_RO, resource_prefix

ROOT = Path(__file__).resolve().parents[3]
ML_ENV = Path(os.environ.get("QUAERA_ML_ENV", ROOT / "ml-env"))
GPU_DEVICES = ["/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools"]
mcp = MCPServer("sandbox")


def _base_python_binds() -> list[str]:
    """The virtualenv's base interpreter: both the path as seen through the symlink and the real path are bound."""
    cfg = ML_ENV / "pyvenv.cfg"
    if not cfg.exists():
        return []
    home = next((l.split("=", 1)[1].strip() for l in cfg.read_text().splitlines() if l.startswith("home")), None)
    if not home:
        return []
    shown, real = Path(home).parent, Path(home).resolve().parent
    out = ["--ro-bind", str(real), str(real)]
    if shown != real:
        out += ["--ro-bind", str(real), str(shown)]
    return out


def _git(work: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "quaera", "GIT_AUTHOR_EMAIL": "quaera@localhost",
           "GIT_COMMITTER_NAME": "quaera", "GIT_COMMITTER_EMAIL": "quaera@localhost"}
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


def _safe(work: Path, rel: str) -> Path:
    path = (work / rel).resolve()
    if work.resolve() not in path.parents and path != work.resolve():
        raise ValueError(f"cannot write outside the working directory: {rel}")
    return path


@mcp.tool()
def sandbox_write(workdir: str, path: str, content: str, message: str = "quaera") -> str:
    """Writes a file into the working directory and commits it with git. Returns: {"commit": "<sha>"}."""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    if not (work / ".git").exists():
        _git(work, "init", "-q")
    target = _safe(work, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(work, "add", path)
    if _git(work, "status", "--porcelain"):
        _git(work, "commit", "-q", "-m", message)
    return json.dumps({"commit": _git(work, "rev-parse", "HEAD")})


def remote_runner() -> dict | None:
    """The configured remote runner, or None."""
    raw = os.environ.get("QUAERA_REMOTE_RUNNER", "").strip()
    if not raw:
        return None
    prefix_cmd = shlex.split(raw)
    host = os.environ.get("QUAERA_REMOTE_PREFIX")
    if host is None:
        host = prefix_cmd[-1] + ":" if prefix_cmd and prefix_cmd[0] == "ssh" else ""
    rsync_ssh = ["-e", shlex.join(prefix_cmd[:-1])] if prefix_cmd and prefix_cmd[0] == "ssh" and len(prefix_cmd) > 2 else []
    return {"cmd": prefix_cmd, "host": host, "rsync": rsync_ssh, "dir": os.environ.get("QUAERA_REMOTE_DIR", "/tmp/quaera-remote"),
            "quaera": shlex.split(os.environ.get("QUAERA_REMOTE_QUAERA", "quaera")), "label": raw.split()[-1] if raw.split() else raw}


def _remote_exec(r: dict, work: Path, command: list[str], data_dir: str | None, timeout_s: int) -> dict:
    job = f"{r['dir'].rstrip('/')}/{uuid.uuid4().hex[:12]}"
    rwork, rdata = f"{job}/work", f"{job}/data"
    sync = lambda src, dst: subprocess.run(["rsync", "-a", "--delete", "--exclude", ".git", *r["rsync"], src, dst],  # noqa: E731
                                           capture_output=True, text=True, timeout=1800, check=True)
    remote = lambda args: subprocess.run(r["cmd"] + [shlex.join(args)], capture_output=True, text=True, timeout=timeout_s + 300)  # noqa: E731
    try:
        mk = remote(["mkdir", "-p", rwork, rdata])
        if mk.returncode != 0:
            raise RuntimeError(f"remote runner unreachable: {mk.stderr.strip()[-300:]}")
        sync(f"{work}/", f"{r['host']}{rwork}/")
        if data_dir:
            sync(f"{Path(data_dir).resolve()}/", f"{r['host']}{rdata}/")
        proc = remote([*r["quaera"], "sandbox-exec", "--workdir", rwork, "--timeout", str(timeout_s), "--gpu",
                       *(["--data-dir", rdata] if data_dir else []), "--", *command])
        try:
            res = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            res = {"returncode": proc.returncode or -1, "stdout": proc.stdout[-4000:], "timed_out": False,
                   "stderr": f"remote runner returned no result: {proc.stderr[-2000:]}"}
        sync(f"{r['host']}{rwork}/", f"{work}/")     # results written by the job come back to the project's working directory
    finally:
        remote(["rm", "-rf", job])
    res["runner"] = r["label"]
    return res


@mcp.tool()
def sandbox_exec(workdir: str, command: list[str], data_dir: str | None = None, timeout_s: int = 600,
                 gpu: bool = False) -> str:
    """Runs a command inside the sandbox. Returns: {"returncode", "stdout", "stderr", "timed_out", "gpu"}."""
    work = Path(workdir).resolve()
    runner = remote_runner() if gpu else None
    if runner is not None:
        try:
            res = _remote_exec(runner, work, command, data_dir, timeout_s)
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            res = {"returncode": -1, "stdout": "", "stderr": f"remote runner failed: {exc}", "timed_out": False, "runner": runner["label"]}
        res["gpu"] = True
        return json.dumps(res, ensure_ascii=False)
    args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
    for p in SYSTEM_RO:
        if os.path.exists(p):
            args += ["--ro-bind", p, p]
    args += ["--ro-bind", str(ML_ENV.resolve()), str(ML_ENV.resolve())]
    args += _base_python_binds()   # the virtualenv's python is a symlink to the base interpreter installed by uv
    if data_dir:
        args += ["--ro-bind", str(Path(data_dir).resolve()), "/data"]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(work), str(work), "--chdir", str(work)]
    if gpu:
        for dev in GPU_DEVICES:
            if os.path.exists(dev):
                args += ["--dev-bind", dev, dev]
        if os.path.exists("/sys"):
            args += ["--ro-bind", "/sys", "/sys"]
    env = {"PATH": f"{ML_ENV.resolve()}/bin:/usr/bin:/bin", "HOME": str(work), "LANG": "C.UTF-8",
           "PYTHONHASHSEED": "0", "CUBLAS_WORKSPACE_CONFIG": ":4096:8", "OMP_NUM_THREADS": "4",
           "CUDA_VISIBLE_DEVICES": "0" if gpu else ""}
    args += ["--clearenv"] + [x for k, v in env.items() for x in ("--setenv", k, v)]
    limits = ["prlimit", f"--cpu={timeout_s * 4}", "--nproc=512", "--"]
    try:
        proc = subprocess.run(resource_prefix() + args + limits + command, capture_output=True, text=True, timeout=timeout_s)
        res = {"returncode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:], "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        res = {"returncode": -1, "stdout": str(exc.stdout or "")[-4000:], "stderr": str(exc.stderr or "")[-2000:], "timed_out": True}
    res["gpu"] = gpu
    return json.dumps(res, ensure_ascii=False)


@mcp.tool()
def sandbox_create_clean(workdir: str, commit: str) -> str:
    """Extracts the given commit from scratch into a new directory. Returns: {"workdir": "<new directory>"}."""
    clean = Path(tempfile.mkdtemp(prefix="quaera-clean-"))
    archive = subprocess.run(["git", "-C", workdir, "archive", commit], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(clean)], input=archive, check=True)
    return json.dumps({"workdir": str(clean)})


if __name__ == "__main__":
    mcp.run()

