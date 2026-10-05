"""Sandbox MCP sunucusu: deney kodunu yazma, çalıştırma ve temiz ortam kurma.

Her proje bir çalışma dizinine sahiptir (`<proje>/work`); kod orada bir git deposunda tutulur.
- sandbox_write: dosya yazar ve commit eder; commit kimliğini döndürür.
- sandbox_exec: komutu bwrap içinde çalıştırır. Ağ kapalı, ML ortamı ve veri salt okunur, yalnızca çalışma dizini yazılabilir.
  GPU yalnızca `gpu=true` ile ve araç kaydının izin kontrolünden sonra açılır.
- sandbox_create_clean: belirli bir commit'i sıfırdan, yeni bir dizine çıkarır (Doğrulayıcı'nın temiz ortamı).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from quaera.sandbox import SYSTEM_RO, resource_prefix

ROOT = Path(__file__).resolve().parents[3]
ML_ENV = Path(os.environ.get("QUAERA_ML_ENV", ROOT / "ml-env"))
GPU_DEVICES = ["/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools"]
mcp = MCPServer("sandbox")


def _base_python_binds() -> list[str]:
    """Sanal ortamın temel yorumlayıcısı: hem sembolik bağın göründüğü yol hem gerçek yol bağlanır."""
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
        raise ValueError(f"çalışma dizini dışına yazılamaz: {rel}")
    return path


@mcp.tool()
def sandbox_write(workdir: str, path: str, content: str, message: str = "quaera") -> str:
    """Çalışma dizinine dosya yazar ve git ile commit eder. Dönüş: {"commit": "<sha>"}."""
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


@mcp.tool()
def sandbox_exec(workdir: str, command: list[str], data_dir: str | None = None, timeout_s: int = 600,
                 gpu: bool = False) -> str:
    """Komutu sandbox içinde çalıştırır. Dönüş: {"returncode", "stdout", "stderr", "timed_out", "gpu"}."""
    work = Path(workdir).resolve()
    args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
    for p in SYSTEM_RO:
        if os.path.exists(p):
            args += ["--ro-bind", p, p]
    args += ["--ro-bind", str(ML_ENV.resolve()), str(ML_ENV.resolve())]
    args += _base_python_binds()   # sanal ortamın python'u uv'nin kurduğu temel yorumlayıcıya sembolik bağdır
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
    """Belirtilen commit'i sıfırdan yeni bir dizine çıkarır. Dönüş: {"workdir": "<yeni dizin>"}."""
    clean = Path(tempfile.mkdtemp(prefix="quaera-clean-"))
    archive = subprocess.run(["git", "-C", workdir, "archive", commit], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(clean)], input=archive, check=True)
    return json.dumps({"workdir": str(clean)})


if __name__ == "__main__":
    mcp.run()

