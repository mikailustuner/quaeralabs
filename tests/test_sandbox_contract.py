"""The sandbox contract (ADR 0018, improvement plan A2): the same checks for every backend.

A backend that is not available on this machine is skipped, never silently replaced by another one."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quaera.sandbox import BwrapSandbox, ContainerSandbox, SandboxUnavailable, bwrap_works, make_sandbox

PY = "/usr/bin/python3"


def _container_ready() -> bool:
    engine = next((e for e in ("podman", "docker") if shutil.which(e)), None)
    if not engine:
        return False
    image = os.environ.get("QUAERA_SANDBOX_IMAGE", "debian:13")
    return subprocess.run([engine, "image", "inspect", image], capture_output=True).returncode == 0


BACKENDS = [
    pytest.param("bwrap", marks=pytest.mark.skipif(not bwrap_works(), reason="bubblewrap cannot create namespaces here")),
    pytest.param("container", marks=pytest.mark.skipif(not _container_ready(), reason="no container engine or base image")),
]
pytestmark = pytest.mark.skipif(not os.path.exists(PY), reason="host python3 missing")


@pytest.fixture(params=BACKENDS)
def box(request):
    cls = BwrapSandbox if request.param == "bwrap" else ContainerSandbox
    return cls(cpu_seconds=20, max_processes=64, mem="512M")


def py(box, code, work, **kw):
    return box.run([PY, "-c", code], work, timeout_s=kw.pop("timeout_s", 60), **kw)


def test_exit_code_and_output_pass_through(box, tmp_path):
    r = py(box, "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)", tmp_path)
    assert (r.returncode, r.stdout.strip(), r.stderr.strip(), r.timed_out) == (3, "out", "err", False)


def test_network_is_off(box, tmp_path):
    r = py(box, "import socket\ntry:\n socket.create_connection(('1.1.1.1', 53), timeout=3); print('online')\n"
                "except OSError as e: print('offline')", tmp_path)
    assert r.stdout.strip() == "offline"


def test_home_and_user_files_are_invisible(box, tmp_path):
    home = Path.home()
    r = py(box, f"import os; print(os.path.exists({str(home)!r}), os.environ['HOME'])", tmp_path)
    assert r.stdout.split() == ["False", str(tmp_path.resolve())]


def test_only_the_working_directory_is_writable(box, tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "data.txt").write_text("x")
    work = tmp_path / "work"
    work.mkdir()
    code = ("import os\n"
            "def w(p):\n try:\n  open(p, 'w').write('y'); return 'wrote'\n except OSError: return 'denied'\n"
            f"print(w({str(work / 'ok.txt')!r}), w({str(ro / 'data.txt')!r}), w('/usr/evil'), w('/tmp/scratch'))")
    r = py(box, code, work, ro_paths=[ro])
    assert r.stdout.split() == ["wrote", "denied", "denied", "wrote"]
    assert (work / "ok.txt").read_text() == "y" and (ro / "data.txt").read_text() == "x"


def test_bind_to_another_destination(box, tmp_path):
    data = tmp_path / "dataset"
    data.mkdir()
    (data / "a.csv").write_text("1,2\n")
    work = tmp_path / "work"
    work.mkdir()
    r = py(box, "print(open('/data/a.csv').read().strip())", work, ro_paths=[(data, "/data")])
    assert r.stdout.strip() == "1,2"


def test_environment_is_cleared(box, tmp_path, monkeypatch):
    monkeypatch.setenv("QUAERA_SECRET_PROBE", "leak")
    r = py(box, "import os; print(os.environ.get('QUAERA_SECRET_PROBE'), os.environ.get('GIVEN'))", tmp_path, env={"GIVEN": "yes"})
    assert r.stdout.split() == ["None", "yes"]


def test_timeout_kills_the_command(box, tmp_path):
    t = time.monotonic()
    r = py(box, "import time; time.sleep(60)", tmp_path, timeout_s=5)
    assert r.timed_out and time.monotonic() - t < 40
    if isinstance(box, ContainerSandbox):     # the container itself must be gone, not just its client
        time.sleep(1)
        left = subprocess.run([box.engine, "ps", "-q", "--filter", "name=quaera-sbx-"], capture_output=True, text=True).stdout
        assert left.strip() == ""


def test_process_count_is_limited(box, tmp_path):
    code = ("import subprocess\nps = []\ntry:\n for _ in range(200): ps.append(subprocess.Popen(['sleep', '5']))\n"
            " print('unlimited')\nexcept OSError: print('limited')\nfor p in ps: p.kill()")
    assert py(box, code, tmp_path).stdout.strip() == "limited"


def test_cpu_time_is_limited(box, tmp_path):
    b = type(box)(cpu_seconds=2, max_processes=64, mem="512M")
    r = py(b, "while True: pass", tmp_path, timeout_s=30)
    assert r.returncode != 0 and not r.timed_out


def test_factory_honours_the_setting(monkeypatch):
    monkeypatch.setenv("QUAERA_SANDBOX", "nonsense")
    with pytest.raises(SandboxUnavailable):
        make_sandbox()
    if bwrap_works():
        monkeypatch.setenv("QUAERA_SANDBOX", "bwrap")
        assert make_sandbox().kind == "bwrap"
        monkeypatch.setenv("QUAERA_SANDBOX", "auto")
        assert make_sandbox().kind == "bwrap"
    if shutil.which("docker") or shutil.which("podman"):
        monkeypatch.setenv("QUAERA_SANDBOX", "container")
        assert make_sandbox().kind == "container"


def test_read_only_binds_below_tmp_stay_visible(box, tmp_path):
    """A project or data directory under /tmp must not be hidden by the sandbox's private /tmp (found by `quaera prove`)."""
    data = Path("/tmp") / f"quaera-contract-{os.getpid()}"
    data.mkdir(exist_ok=True)
    (data / "x.txt").write_text("seen")
    try:
        r = py(box, f"print(open({str(data / 'x.txt')!r}).read())", tmp_path, ro_paths=[data])
        assert r.stdout.strip() == "seen"
    finally:
        shutil.rmtree(data)
